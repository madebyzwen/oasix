from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from oasix.config import BootstrapSettings, LoadedConfiguration, load_startup_configuration
from oasix.worker import (
    ServiceId,
    ServiceReadinessProbe,
    WorkerCommunicationError,
    WorkerConfigurationError,
    WorkerTimeoutError,
    create_http_service_readiness_adapter,
)

SENTINEL_SECRET = "SENTINEL-B1-HTTP-SECRET"


class _UnreadableResponseStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        raise AssertionError("readiness response body must not be consumed")
        yield b""  # pragma: no cover

    async def aclose(self) -> None:
        self.closed = True


def _load_configuration(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> LoadedConfiguration:
    (secret_directory / "llm_api_token").write_text(SENTINEL_SECRET, encoding="utf-8")
    return load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(valid_config_data),
            secrets_directory=secret_directory,
        )
    )


def _run_probe(
    loaded: LoadedConfiguration,
    transport: httpx.AsyncBaseTransport,
    service_id: str = "llm",
) -> bool:
    async def run() -> bool:
        async with create_http_service_readiness_adapter(
            loaded.runtime,
            loaded.secrets,
            transport=transport,
        ) as adapter:
            assert isinstance(adapter, ServiceReadinessProbe)
            return (await adapter.check_readiness(ServiceId(service_id))).ready

    return asyncio.run(run())


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_uses_configured_method_path_status_and_bearer_authentication(
    method: str,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    service = valid_config_data["workers"]["worker-primary"]["services"]["llm"]
    service["endpoint"] = "https://service.example.invalid/api/base"
    service["readiness"]["method"] = method
    service["readiness"]["path"] = "/health/ready"
    service["readiness"]["expected_status_codes"] = [204]
    loaded = _load_configuration(valid_config_data, write_config, secret_directory)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    assert _run_probe(loaded, httpx.MockTransport(handler)) is True
    assert len(captured) == 1
    assert captured[0].method == method
    assert str(captured[0].url) == "https://service.example.invalid/health/ready"
    assert captured[0].headers["Authorization"] == f"Bearer {SENTINEL_SECRET}"


def test_returns_not_ready_for_unexpected_status_without_following_redirect(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    loaded = _load_configuration(valid_config_data, write_config, secret_directory)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://redirect.example.invalid/ready"})

    assert _run_probe(loaded, httpx.MockTransport(handler)) is False
    assert len(requests) == 1


def test_does_not_consume_response_body_and_closes_response_stream(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    loaded = _load_configuration(valid_config_data, write_config, secret_directory)
    response_stream = _UnreadableResponseStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=response_stream)

    assert _run_probe(loaded, httpx.MockTransport(handler)) is True
    assert response_stream.closed is True


def test_ignores_environment_proxy_configuration(
    monkeypatch: Any,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://SENTINEL-PROXY.example.invalid:3128")
    loaded = _load_configuration(valid_config_data, write_config, secret_directory)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    assert _run_probe(loaded, httpx.MockTransport(handler)) is True
    assert len(requests) == 1


def test_uses_configured_custom_authentication_header(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    service = valid_config_data["workers"]["worker-primary"]["services"]["llm"]
    service["auth"] = {
        "method": "header",
        "header_name": "X-Service-Key",
        "secret": {"source": "file", "name": "llm_api_token"},
    }
    loaded = _load_configuration(valid_config_data, write_config, secret_directory)
    received_header: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_header.append(request.headers["X-Service-Key"])
        return httpx.Response(200)

    assert _run_probe(loaded, httpx.MockTransport(handler)) is True
    assert received_header == [SENTINEL_SECRET]


@pytest.mark.parametrize("failure_kind", ["timeout", "communication"])
def test_maps_transport_failures_without_exposing_secret_or_endpoint(
    failure_kind: str,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    production_traceback_locals: Any,
) -> None:
    endpoint = "https://sentinel-private-service.example.invalid"
    valid_config_data["workers"]["worker-primary"]["services"]["llm"]["endpoint"] = endpoint
    loaded = _load_configuration(valid_config_data, write_config, secret_directory)

    def handler(request: httpx.Request) -> httpx.Response:
        if failure_kind == "timeout":
            raise httpx.ReadTimeout("SENTINEL-TRANSPORT-DETAIL", request=request)
        raise httpx.ConnectError("SENTINEL-TRANSPORT-DETAIL", request=request)

    expected_error = WorkerTimeoutError if failure_kind == "timeout" else WorkerCommunicationError
    with pytest.raises(expected_error) as captured:
        _run_probe(loaded, httpx.MockTransport(handler))

    exposed = "\n".join(
        (
            str(captured.value),
            repr(captured.value),
            production_traceback_locals(captured.value),
        )
    )
    assert SENTINEL_SECRET not in exposed
    assert "SENTINEL-TRANSPORT-DETAIL" not in exposed
    assert endpoint not in exposed


@pytest.mark.parametrize("service_state", ["missing", "disabled"])
def test_rejects_unconfigured_or_disabled_services_without_network_access(
    service_state: str,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    if service_state == "disabled":
        valid_config_data["workers"]["worker-primary"]["services"]["llm"]["enabled"] = False
    loaded = _load_configuration(valid_config_data, write_config, secret_directory)

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request!r}")

    service_id = "unknown-service" if service_state == "missing" else "llm"
    with pytest.raises(WorkerConfigurationError):
        _run_probe(loaded, httpx.MockTransport(unexpected_request), service_id)


def test_adapter_representation_hides_endpoints_and_secrets(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    endpoint = "https://sentinel-private-service.example.invalid"
    valid_config_data["workers"]["worker-primary"]["services"]["llm"]["endpoint"] = endpoint
    loaded = _load_configuration(valid_config_data, write_config, secret_directory)

    async def inspect_adapter() -> str:
        async with create_http_service_readiness_adapter(
            loaded.runtime,
            loaded.secrets,
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        ) as adapter:
            return repr(adapter)

    representation = asyncio.run(inspect_adapter())
    assert "worker-primary" in representation
    assert endpoint not in representation
    assert SENTINEL_SECRET not in representation
