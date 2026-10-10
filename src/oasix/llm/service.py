"""Lease-protected non-streaming inference orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from oasix.llm.errors import (
    InferenceOverloadedError,
    LlmPathError,
    LlmReadinessError,
    LlmUpstreamError,
)
from oasix.llm.leases import InferenceLeaseRegistry, InferenceLeaseSession
from oasix.llm.models import ChatCompletionRequest, ChatCompletionResponse
from oasix.llm.telemetry import LlmRequestSpan
from oasix.worker.contracts import ServiceId
from oasix.worker.errors import WorkerInteractionError
from oasix.worker.orchestration import WorkerReadinessTiming


class ReadinessController(Protocol):
    async def ensure_ready(
        self, service_ids: tuple[ServiceId, ...]
    ) -> WorkerReadinessTiming | None: ...


class NonStreamingUpstream(Protocol):
    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse: ...

    def stream(self, request: ChatCompletionRequest) -> AsyncIterator[bytes]: ...


class InferenceAdmission:
    """One immediately reserved concurrency token with idempotent release."""

    __slots__ = ("_closed", "_gate", "_token")

    def __init__(self, gate: InferenceConcurrencyGate, token: object) -> None:
        self._gate = gate
        self._token = token
        self._closed = False

    async def __aenter__(self) -> InferenceAdmission:
        if self._closed:
            raise InferenceOverloadedError from None
        return self

    async def __aexit__(self, *_exception: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._gate._release(self._token)
            self._closed = True

    def belongs_to(self, gate: InferenceConcurrencyGate) -> bool:
        return self._gate is gate and not self._closed


class InferenceConcurrencyGate:
    """A bounded token pool with immediate overload rejection and no wait queue."""

    __slots__ = ("_tokens",)

    def __init__(self, limit: int) -> None:
        self._tokens: asyncio.Queue[object] = asyncio.Queue(maxsize=limit)
        for _ in range(limit):
            self._tokens.put_nowait(object())

    def acquire(self) -> InferenceAdmission:
        try:
            token = self._tokens.get_nowait()
        except asyncio.QueueEmpty:
            raise InferenceOverloadedError from None
        return InferenceAdmission(self, token)

    def _release(self, token: object) -> None:
        self._tokens.put_nowait(token)


class LlmProxyService:
    """Compose concurrency, persistent lease, readiness and upstream use."""

    __slots__ = (
        "_gate",
        "_heartbeat_interval",
        "_lease_registry",
        "_lease_ttl",
        "_readiness",
        "_service_id",
        "_upstream",
        "_worker_id",
    )

    def __init__(
        self,
        *,
        gate: InferenceConcurrencyGate,
        lease_registry: InferenceLeaseRegistry,
        worker_id: str,
        service_id: ServiceId,
        lease_ttl_seconds: int,
        heartbeat_interval_seconds: float,
        readiness: ReadinessController,
        upstream: NonStreamingUpstream,
    ) -> None:
        self._gate = gate
        self._lease_registry = lease_registry
        self._worker_id = worker_id
        self._service_id = service_id
        self._lease_ttl = lease_ttl_seconds
        self._heartbeat_interval = heartbeat_interval_seconds
        self._readiness = readiness
        self._upstream = upstream

    async def complete(
        self,
        request: ChatCompletionRequest,
        telemetry: LlmRequestSpan | None = None,
    ) -> ChatCompletionResponse:
        """Admit and run one request, replacing content-retaining tracebacks."""

        async with self.admit():
            return await self.complete_admitted(request, telemetry)

    def admit(self) -> InferenceAdmission:
        """Reserve capacity immediately, without introducing a waiting queue."""

        return self._gate.acquire()

    async def complete_admitted(
        self,
        request: ChatCompletionRequest,
        telemetry: LlmRequestSpan | None = None,
    ) -> ChatCompletionResponse:
        """Run an already-admitted request and replace sensitive tracebacks."""

        error_type: type[LlmPathError] | None = None
        try:
            return await self._complete_admitted(request, telemetry)
        except asyncio.CancelledError:
            request = None  # type: ignore[assignment]
            raise asyncio.CancelledError from None
        except LlmPathError as error:
            error_type = type(error)
        except WorkerInteractionError:
            error_type = LlmReadinessError
        except Exception:
            error_type = LlmUpstreamError
        request = None  # type: ignore[assignment]
        if error_type is None:  # pragma: no cover - every exception path assigns it
            raise AssertionError("LLM path error mapping produced no category")
        raise error_type from None

    async def _complete_admitted(
        self,
        request: ChatCompletionRequest,
        telemetry: LlmRequestSpan | None,
    ) -> ChatCompletionResponse:
        async with InferenceLeaseSession(
            self._lease_registry,
            self._worker_id,
            self._lease_ttl,
            self._heartbeat_interval,
        ) as lease:
            if telemetry is not None:
                telemetry.record_lease(lease.lease_id)
            timing = await self._readiness.ensure_ready((self._service_id,))
            if telemetry is not None:
                telemetry.record_readiness(timing)
            lease.ensure_healthy()
            response = await self._upstream.complete(request)
            lease.ensure_healthy()
            if telemetry is not None:
                telemetry.record_usage(response.usage)
            return response

    async def stream_admitted(
        self,
        request: ChatCompletionRequest,
        admission: InferenceAdmission,
        telemetry: LlmRequestSpan | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream one admitted request while owning its persistent lease."""

        if not admission.belongs_to(self._gate):
            raise InferenceOverloadedError from None

        error_type: type[LlmPathError] | None = None
        try:
            async with InferenceLeaseSession(
                self._lease_registry,
                self._worker_id,
                self._lease_ttl,
                self._heartbeat_interval,
            ) as lease:
                if telemetry is not None:
                    telemetry.record_lease(lease.lease_id)
                timing = await self._readiness.ensure_ready((self._service_id,))
                if telemetry is not None:
                    telemetry.record_readiness(timing)
                lease.ensure_healthy()
                upstream_stream = self._upstream.stream(request)
                try:
                    async for event in upstream_stream:
                        lease.ensure_healthy()
                        if telemetry is not None:
                            telemetry.record_first_token()
                        yield event
                finally:
                    close = getattr(upstream_stream, "aclose", None)
                    if close is not None:
                        await close()
                lease.ensure_healthy()
            return
        except asyncio.CancelledError:
            request = None  # type: ignore[assignment]
            raise asyncio.CancelledError from None
        except LlmPathError as error:
            error_type = type(error)
        except WorkerInteractionError:
            error_type = LlmReadinessError
        except Exception:
            error_type = LlmUpstreamError
        request = None  # type: ignore[assignment]
        if error_type is None:  # pragma: no cover - every exception path assigns it
            raise AssertionError("LLM stream error mapping produced no category")
        raise error_type from None
