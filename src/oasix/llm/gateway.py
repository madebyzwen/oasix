"""FastAPI boundary for the authenticated OpenAI-compatible LLM subset."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from oasix.auth import (
    ClientAuthenticationError,
    ClientAuthenticator,
    ClientAuthorizationError,
    ClientAuthorizer,
    ClientPermission,
    create_client_authenticator,
)
from oasix.config import LoadedConfiguration, load_startup_configuration
from oasix.config.models import RuntimeConfig, ServiceSettings
from oasix.config.secrets import ResolvedSecrets
from oasix.llm.errors import (
    InferenceLeaseAcquireError,
    InferenceLeaseReleaseError,
    InferenceLeaseRenewalError,
    InferenceOverloadedError,
    LlmConfigurationError,
    LlmPathError,
    LlmReadinessError,
    LlmRequestError,
    LlmRequestTooLargeError,
    LlmUpstreamError,
    LlmUpstreamProtocolError,
    LlmUpstreamTimeoutError,
)
from oasix.llm.leases import PersistentInferenceLeaseRegistry
from oasix.llm.models import ChatCompletionRequest
from oasix.llm.service import InferenceConcurrencyGate, LlmProxyService
from oasix.llm.transport import HttpLlmUpstream
from oasix.persistence import PersistenceDatabase, initialize_persistence
from oasix.worker import (
    HttpServiceReadinessAdapter,
    ServiceId,
    WorkerReadinessOrchestrator,
    create_http_service_readiness_adapter,
    create_worker_wake_controller,
    resolve_active_worker,
)
from oasix.worker.wake import DatagramSender

MAX_CLIENT_REQUEST_BYTES = 1_048_576


@dataclass(slots=True)
class _GatewayResources:
    readiness: HttpServiceReadinessAdapter
    upstream: HttpLlmUpstream
    database: PersistenceDatabase | None = None

    async def aclose(self) -> None:
        try:
            await self.upstream.aclose()
        finally:
            try:
                await self.readiness.aclose()
            finally:
                if self.database is not None:
                    self.database.close()


def create_configured_llm_gateway_app() -> FastAPI:
    """Load startup inputs and return an app which owns its database resource."""

    loaded: LoadedConfiguration = load_startup_configuration()
    database = initialize_persistence(loaded.runtime, loaded.bootstrap)
    try:
        return create_llm_gateway_app(
            loaded.runtime,
            loaded.secrets,
            database,
            _owned_database=database,
        )
    except Exception:
        database.close()
        raise


def create_llm_gateway_app(
    runtime: RuntimeConfig,
    secrets: ResolvedSecrets,
    database: PersistenceDatabase,
    *,
    readiness_transport: httpx.AsyncBaseTransport | None = None,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    wake_sender: DatagramSender | None = None,
    _owned_database: PersistenceDatabase | None = None,
) -> FastAPI:
    """Build a fully protected gateway or fail before exposing any route."""

    if runtime.schema_version != 4 or runtime.policies.inference is None:
        raise LlmConfigurationError from None
    authenticator = create_client_authenticator(runtime, secrets)
    authorizer = ClientAuthorizer()
    target = resolve_active_worker(runtime)
    service_id, service = _select_llm_service(runtime)

    lease_registry = PersistentInferenceLeaseRegistry(database)
    lease_registry.ensure_worker(target.worker_id)
    readiness = create_http_service_readiness_adapter(
        runtime,
        secrets,
        transport=readiness_transport,
    )
    wake = create_worker_wake_controller(runtime, sender=wake_sender)
    orchestrator = WorkerReadinessOrchestrator(
        wake,
        readiness,
        runtime.policies.retry.wake,
        runtime.policies.readiness_timeout_seconds,
    )
    upstream = HttpLlmUpstream(
        service,
        secrets,
        runtime.policies.inference.request_timeout_seconds,
        transport=upstream_transport,
    )
    proxy = LlmProxyService(
        gate=InferenceConcurrencyGate(
            runtime.policies.concurrency.max_concurrent_inference_requests
        ),
        lease_registry=lease_registry,
        worker_id=target.worker_id,
        service_id=ServiceId(service_id),
        lease_ttl_seconds=runtime.policies.inference.lease_ttl_seconds,
        heartbeat_interval_seconds=runtime.policies.inference.heartbeat_interval_seconds,
        readiness=orchestrator,
        upstream=upstream,
    )
    resources = _GatewayResources(
        readiness=readiness,
        upstream=upstream,
        database=_owned_database,
    )

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await resources.aclose()

    application = FastAPI(
        title="OASIX Control Plane",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.oasix_resources = resources

    @application.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        request_id = str(uuid.uuid4())
        try:
            _authenticate_inference(request, authenticator, authorizer)
        except ClientAuthenticationError:
            return _error_response(
                401,
                "authentication_error",
                "authentication_failed",
                request_id,
                authenticate=True,
            )
        except ClientAuthorizationError:
            return _error_response(
                403,
                "permission_error",
                "insufficient_permission",
                request_id,
            )

        try:
            async with proxy.admission():
                chat_request = await _read_chat_request(request)
                if chat_request.stream:
                    raise LlmRequestError from None
                operation = proxy.complete_admitted(chat_request)
                chat_request = None
                result = await operation
        except LlmRequestTooLargeError:
            return _error_response(413, "invalid_request_error", "request_too_large", request_id)
        except LlmRequestError:
            return _error_response(400, "invalid_request_error", "invalid_request", request_id)
        except InferenceOverloadedError:
            return _error_response(429, "overload_error", "inference_overloaded", request_id)
        except (InferenceLeaseAcquireError, InferenceLeaseRenewalError):
            return _error_response(503, "service_error", "lease_unavailable", request_id)
        except InferenceLeaseReleaseError:
            return _error_response(503, "service_error", "lease_release_failed", request_id)
        except LlmReadinessError:
            return _error_response(503, "service_error", "worker_unavailable", request_id)
        except LlmUpstreamTimeoutError:
            return _error_response(504, "timeout_error", "upstream_timeout", request_id)
        except LlmUpstreamProtocolError:
            return _error_response(502, "upstream_error", "invalid_upstream_response", request_id)
        except LlmUpstreamError:
            return _error_response(502, "upstream_error", "upstream_unavailable", request_id)
        except LlmPathError:
            return _error_response(500, "server_error", "request_failed", request_id)
        except Exception:
            return _error_response(500, "server_error", "request_failed", request_id)

        return JSONResponse(
            result.model_dump(mode="json", exclude_none=True),
            status_code=200,
            headers={"x-request-id": request_id},
        )

    return application


def _authenticate_inference(
    request: Request,
    authenticator: ClientAuthenticator,
    authorizer: ClientAuthorizer,
) -> None:
    authorization_values = request.headers.getlist("authorization")
    if len(authorization_values) != 1:
        raise ClientAuthenticationError from None
    identity = authenticator.authenticate(authorization_values[0])
    authorization_values = []
    authorizer.require(identity, ClientPermission.INFERENCE)


async def _read_chat_request(request: Request) -> ChatCompletionRequest:
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_CLIENT_REQUEST_BYTES:
                chunks.clear()
                raise LlmRequestTooLargeError from None
            chunks.append(chunk)
        body = b"".join(chunks)
        document = json.loads(body, object_pairs_hook=_unique_json_object)
        result = ChatCompletionRequest.model_validate(document)
    except LlmRequestTooLargeError:
        raise LlmRequestTooLargeError from None
    except LlmRequestError:
        chunks.clear()
        if "body" in locals():
            body = b""
        if "document" in locals():
            document = None
        raise LlmRequestError from None
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError, ValueError):
        chunks.clear()
        raise LlmRequestError from None
    chunks.clear()
    body = b""
    document = None
    return result


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LlmRequestError from None
        result[key] = value
    return result


def _select_llm_service(runtime: RuntimeConfig) -> tuple[str, ServiceSettings]:
    candidates = [
        (service_id, service)
        for service_id, service in runtime.active_worker_profile.services.items()
        if service.enabled and service.kind == "llm"
    ]
    if len(candidates) != 1:
        raise LlmConfigurationError from None
    return candidates[0]


def _error_response(
    status_code: int,
    error_type: str,
    code: str,
    request_id: str,
    *,
    authenticate: bool = False,
) -> JSONResponse:
    headers = {"x-request-id": request_id}
    if authenticate:
        headers["www-authenticate"] = "Bearer"
    return JSONResponse(
        {
            "error": {
                "message": "Request could not be processed.",
                "type": error_type,
                "code": code,
            }
        },
        status_code=status_code,
        headers=headers,
    )
