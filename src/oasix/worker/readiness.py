"""Asynchronous HTTP readiness adapter for configured worker services."""

from __future__ import annotations

from enum import Enum, auto

import httpx

from oasix.config.models import RuntimeConfig, ServiceSettings
from oasix.config.secrets import ResolvedSecrets
from oasix.worker.contracts import ServiceId, ServiceReadinessObservation, WorkerId
from oasix.worker.errors import (
    WorkerCommunicationError,
    WorkerConfigurationError,
    WorkerTimeoutError,
)
from oasix.worker.resolution import ActiveWorkerTarget, resolve_active_worker


class _ProbeOutcome(Enum):
    TIMEOUT = auto()
    COMMUNICATION_ERROR = auto()


class HttpServiceReadinessAdapter:
    """Probe configured services without exposing endpoints or credentials."""

    __slots__ = ("_client", "_secrets", "_target")

    def __init__(
        self,
        target: ActiveWorkerTarget,
        secrets: ResolvedSecrets,
        timeout_seconds: int,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise WorkerConfigurationError from None
        self._target = target
        self._secrets = secrets
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds)),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return (
            "HttpServiceReadinessAdapter("
            f"worker_id={self.worker_id!r}, services=<configured>, secrets=<resolved>)"
        )

    @property
    def worker_id(self) -> WorkerId:
        """Return the generic configured worker identifier."""

        return self._target.worker_id

    async def __aenter__(self) -> HttpServiceReadinessAdapter:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""

        await self._client.aclose()

    async def check_readiness(self, service_id: ServiceId) -> ServiceReadinessObservation:
        """Return one configured HTTP readiness result or a controlled error."""

        service = self._target.profile.services.get(str(service_id))
        if service is None or not service.enabled:
            raise WorkerConfigurationError from None

        expected_status_codes = service.readiness.expected_status_codes
        request_status = self._request_status(service)
        service = None
        result = await request_status
        if result is _ProbeOutcome.TIMEOUT:
            raise WorkerTimeoutError from None
        if result is _ProbeOutcome.COMMUNICATION_ERROR:
            raise WorkerCommunicationError from None
        return ServiceReadinessObservation(ready=result in expected_status_codes)

    async def _request_status(self, service: ServiceSettings) -> int | _ProbeOutcome:
        response: httpx.Response | None = None
        outcome: int | _ProbeOutcome
        try:
            request = self._build_request(service)
            service = None
            response = await self._client.send(
                request,
                stream=True,
                follow_redirects=False,
            )
            outcome = response.status_code
        except httpx.TimeoutException:
            outcome = _ProbeOutcome.TIMEOUT
        except Exception:
            outcome = _ProbeOutcome.COMMUNICATION_ERROR

        service = None
        if response is not None:
            try:
                await response.aclose()
            except Exception:
                outcome = _ProbeOutcome.COMMUNICATION_ERROR
        return outcome

    def _build_request(self, service: ServiceSettings) -> httpx.Request:
        readiness_url = httpx.URL(str(service.endpoint)).join(service.readiness.path)
        request = self._client.build_request(service.readiness.method, readiness_url)
        authentication = service.auth
        if authentication.method == "bearer":
            secret = self._secrets.get(authentication.secret).get_secret_value()
            request.headers["Authorization"] = f"Bearer {secret}"
            secret = ""
        elif authentication.method == "header":
            secret = self._secrets.get(authentication.secret).get_secret_value()
            request.headers[authentication.header_name] = secret
            secret = ""
        return request


def create_http_service_readiness_adapter(
    runtime: RuntimeConfig,
    secrets: ResolvedSecrets,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HttpServiceReadinessAdapter:
    """Bind an HTTP readiness adapter to the configured active worker."""

    return HttpServiceReadinessAdapter(
        resolve_active_worker(runtime),
        secrets,
        runtime.policies.readiness_timeout_seconds,
        transport=transport,
    )
