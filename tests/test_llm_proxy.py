from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from oasix.config import (
    BootstrapSettings,
    LoadedConfiguration,
    ResolvedSecrets,
    RuntimeConfig,
    load_startup_configuration,
)
from oasix.llm import (
    MAX_UPSTREAM_RESPONSE_BYTES,
    ChatCompletionRequest,
    ChatCompletionResponse,
    InferenceConcurrencyGate,
    InferenceLeaseReleaseError,
    InferenceLeaseRenewalError,
    InferenceOverloadedError,
    LlmConfigurationError,
    LlmProxyService,
    create_llm_gateway_app,
)
from oasix.persistence import Lease, PersistenceDatabase
from oasix.worker import ServiceId

CLIENT_KEY = "inference-client-key-current"
ADMIN_KEY = "administration-client-key"
PROVIDER_KEY = "api-token-value"
SENTINEL_PROMPT = "SENTINEL-PRIVATE-PROMPT"
SENTINEL_PROVIDER_SECRET = "SENTINEL-PROVIDER-CREDENTIAL"


def _loaded(
    data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    *,
    disable_wake: bool = True,
) -> LoadedConfiguration:
    if disable_wake:
        data["workers"]["worker-primary"]["power"]["wake"] = {"method": "none"}
    data["policies"]["retry"]["wake"].update(
        max_attempts=1,
        initial_delay_seconds=0.0,
        max_delay_seconds=0.0,
    )
    return load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(data),
            secrets_directory=secret_directory,
        )
    )


def _request_payload(*, stream: bool = False, content: str = "Hello") -> dict[str, Any]:
    return {
        "model": "generic-chat-model",
        "messages": [{"role": "user", "content": content}],
        "stream": stream,
        "temperature": 0.5,
        "max_tokens": 64,
    }


def _response_payload(*, content: str = "Hello back") -> dict[str, Any]:
    return {
        "id": "completion-1",
        "object": "chat.completion",
        "created": 1,
        "model": "generic-chat-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def _readiness_transport(status_code: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _request: httpx.Response(status_code))


async def _request_app(
    app: Any,
    *,
    headers: Any = None,
    payload: dict[str, Any] | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://control-plane.example.invalid",
        ) as client:
            if content is not None:
                return await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    content=content,
                )
            return await client.post(
                "/v1/chat/completions",
                headers=headers,
                json=_request_payload() if payload is None else payload,
            )
    finally:
        await app.state.oasix_resources.aclose()


def _app(
    loaded: LoadedConfiguration,
    database: PersistenceDatabase,
    upstream_transport: httpx.AsyncBaseTransport,
    *,
    readiness_transport: httpx.AsyncBaseTransport | None = None,
    wake_sender: Any = None,
) -> Any:
    return create_llm_gateway_app(
        loaded.runtime,
        loaded.secrets,
        database,
        readiness_transport=readiness_transport or _readiness_transport(),
        upstream_transport=upstream_transport,
        wake_sender=wake_sender,
    )


def test_authenticated_request_uses_provider_credential_and_releases_lease(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    captured: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.headers["authorization"] == f"Bearer {PROVIDER_KEY}"
        assert CLIENT_KEY not in request.headers["authorization"]
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=_response_payload())

    response = asyncio.run(
        _request_app(
            _app(loaded, migrated_database, httpx.MockTransport(upstream)),
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
        )
    )

    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"
    assert len(captured) == 1
    with migrated_database.session() as session:
        leases = list(session.scalars(select(Lease)))
        assert len(leases) == 1
        assert leases[0].worker_id == "worker-primary"
        assert leases[0].owner == "llm-gateway"
        assert leases[0].purpose == "inference"
        assert leases[0].released_at is not None
        assert leases[0].release_reason == "completed"


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        (None, 401),
        ({"Authorization": "Bearer unknown-key"}, 401),
        ({"Authorization": "Basic inference-client-key-current"}, 401),
        ({"Authorization": f"Bearer {ADMIN_KEY}"}, 403),
    ],
)
def test_rejects_missing_invalid_or_unauthorized_credentials_before_upstream(
    headers: dict[str, str] | None,
    expected_status: int,
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    called = False

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_response_payload())

    response = asyncio.run(
        _request_app(
            _app(loaded, migrated_database, httpx.MockTransport(upstream)),
            headers=headers,
        )
    )

    assert response.status_code == expected_status
    assert response.json()["error"]["message"] == "Request could not be processed."
    assert CLIENT_KEY not in response.text
    assert ADMIN_KEY not in response.text
    assert not called


def test_rejects_multiple_authorization_headers(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    headers = [
        ("Authorization", f"Bearer {CLIENT_KEY}"),
        ("Authorization", "Bearer second-key"),
    ]
    response = asyncio.run(
        _request_app(
            _app(
                loaded,
                migrated_database,
                httpx.MockTransport(lambda _request: pytest.fail("must not call upstream")),
            ),
            headers=headers,
        )
    )

    assert response.status_code == 401


def test_runtime_schema_two_cannot_create_gateway(
    valid_config_data: dict[str, Any],
    migrated_database: PersistenceDatabase,
) -> None:
    runtime = RuntimeConfig.model_validate(valid_config_data)

    with pytest.raises(LlmConfigurationError):
        create_llm_gateway_app(runtime, ResolvedSecrets({}), migrated_database)


def test_rejects_streaming_until_phase_b4_and_duplicate_json_keys(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)

    async def run() -> tuple[httpx.Response, httpx.Response]:
        app = _app(
            loaded,
            migrated_database,
            httpx.MockTransport(lambda _request: pytest.fail("must not call upstream")),
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://control-plane.example.invalid",
            ) as client:
                headers = {"Authorization": f"Bearer {CLIENT_KEY}"}
                streaming = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json=_request_payload(stream=True),
                )
                duplicate = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    content=(
                        b'{"model":"first","model":"second",'
                        b'"messages":[{"role":"user","content":"x"}]}'
                    ),
                )
                return streaming, duplicate
        finally:
            await app.state.oasix_resources.aclose()

    streaming, duplicate = asyncio.run(run())
    assert streaming.status_code == 400
    assert duplicate.status_code == 400


def test_rejects_oversized_request_before_worker_use(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    response = asyncio.run(
        _request_app(
            _app(
                loaded,
                migrated_database,
                httpx.MockTransport(lambda _request: pytest.fail("must not call upstream")),
            ),
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
            content=b"x" * (1_048_576 + 1),
        )
    )

    assert response.status_code == 413
    with migrated_database.session() as session:
        assert list(session.scalars(select(Lease))) == []


def test_readiness_observes_active_lease_and_no_open_write_transaction(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)

    def readiness(_request: httpx.Request) -> httpx.Response:
        with migrated_database.session() as session:
            lease = session.scalar(select(Lease))
            assert lease is not None
            assert lease.released_at is None
        return httpx.Response(200)

    response = asyncio.run(
        _request_app(
            _app(
                loaded,
                migrated_database,
                httpx.MockTransport(lambda _request: httpx.Response(200, json=_response_payload())),
                readiness_transport=httpx.MockTransport(readiness),
            ),
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
        )
    )
    assert response.status_code == 200


def test_heartbeat_renews_lease_during_long_request(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    gateway_config_data["policies"]["inference"]["heartbeat_interval_seconds"] = 0.01
    loaded = _loaded(gateway_config_data, write_config, secret_directory)

    async def upstream(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        with migrated_database.session() as session:
            lease = session.scalar(select(Lease))
            assert lease is not None
            assert lease.last_heartbeat_at > lease.created_at
        return httpx.Response(200, json=_response_payload())

    response = asyncio.run(
        _request_app(
            _app(loaded, migrated_database, httpx.MockTransport(upstream)),
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
        )
    )
    assert response.status_code == 200


def test_readiness_failure_releases_lease_without_upstream_call(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    response = asyncio.run(
        _request_app(
            _app(
                loaded,
                migrated_database,
                httpx.MockTransport(lambda _request: pytest.fail("must not call upstream")),
                readiness_transport=_readiness_transport(503),
            ),
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
        )
    )

    assert response.status_code == 503
    with migrated_database.session() as session:
        lease = session.scalar(select(Lease))
        assert lease is not None
        assert lease.released_at is not None
        assert lease.release_reason == "cancelled"


def test_wake_failure_is_bounded_and_releases_lease(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(
        gateway_config_data,
        write_config,
        secret_directory,
        disable_wake=False,
    )
    wake_calls = 0

    async def failing_wake(_packet: bytes, _host: str, _port: int) -> None:
        nonlocal wake_calls
        wake_calls += 1
        raise OSError("SENTINEL-WAKE-DETAIL")

    response = asyncio.run(
        _request_app(
            _app(
                loaded,
                migrated_database,
                httpx.MockTransport(lambda _request: pytest.fail("must not call upstream")),
                readiness_transport=_readiness_transport(503),
                wake_sender=failing_wake,
            ),
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
        )
    )

    assert response.status_code == 503
    assert wake_calls == 1
    assert "SENTINEL" not in response.text
    with migrated_database.session() as session:
        lease = session.scalar(select(Lease))
        assert lease is not None and lease.released_at is not None


@pytest.mark.parametrize(
    ("error_kind", "expected_status"),
    [
        ("connection", 502),
        ("timeout", 504),
    ],
)
def test_upstream_transport_failures_are_safe_and_release_lease(
    error_kind: str,
    expected_status: int,
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)

    def upstream(request: httpx.Request) -> httpx.Response:
        if error_kind == "timeout":
            raise httpx.ReadTimeout("SENTINEL-TIMEOUT-DETAIL", request=request)
        raise httpx.ConnectError("SENTINEL-CONNECTION-DETAIL", request=request)

    response = asyncio.run(
        _request_app(
            _app(loaded, migrated_database, httpx.MockTransport(upstream)),
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
            payload=_request_payload(content=SENTINEL_PROMPT),
        )
    )

    assert response.status_code == expected_status
    assert SENTINEL_PROMPT not in response.text
    assert "SENTINEL" not in response.text
    with migrated_database.session() as session:
        lease = session.scalar(select(Lease))
        assert lease is not None and lease.released_at is not None


@pytest.mark.parametrize(
    ("upstream_headers", "upstream_content"),
    [
        (
            {
                "content-type": "application/json",
                "content-length": str(MAX_UPSTREAM_RESPONSE_BYTES + 1),
            },
            b"{}",
        ),
        (
            {
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
            b"not-expanded",
        ),
        (
            {"content-type": "application/json"},
            (
                b'{"id":"first","id":"second","object":"chat.completion",'
                b'"created":1,"model":"m","choices":[]}'
            ),
        ),
    ],
)
def test_rejects_unbounded_encoded_or_ambiguous_upstream_responses(
    upstream_headers: dict[str, str],
    upstream_content: bytes,
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)

    def upstream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=upstream_headers,
            stream=httpx.ByteStream(upstream_content),
        )

    response = asyncio.run(
        _request_app(
            _app(
                loaded,
                migrated_database,
                httpx.MockTransport(upstream),
            ),
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
        )
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_upstream_response"


def test_closed_database_causes_controlled_acquire_failure(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    app = _app(
        loaded,
        migrated_database,
        httpx.MockTransport(lambda _request: pytest.fail("must not call upstream")),
    )
    migrated_database.close()

    response = asyncio.run(
        _request_app(
            app,
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
        )
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "lease_unavailable"


class _FakeLeaseRegistry:
    def __init__(self, *, fail_renew: bool = False, fail_release: bool = False) -> None:
        self.events: list[str] = []
        self.fail_renew = fail_renew
        self.fail_release = fail_release
        self.transaction_open = False

    async def acquire(self, _lease_id: str, _worker_id: str, _ttl_seconds: int) -> None:
        self.transaction_open = True
        self.events.append("acquire")
        await asyncio.sleep(0)
        self.transaction_open = False

    async def renew(self, _lease_id: str, _ttl_seconds: int) -> None:
        self.transaction_open = True
        self.events.append("renew")
        await asyncio.sleep(0)
        self.transaction_open = False
        if self.fail_renew:
            raise InferenceLeaseRenewalError

    async def release(self, _lease_id: str, _reason: str) -> None:
        self.transaction_open = True
        self.events.append("release")
        await asyncio.sleep(0)
        self.transaction_open = False
        if self.fail_release:
            raise InferenceLeaseReleaseError


class _FakeReadiness:
    def __init__(self, registry: _FakeLeaseRegistry) -> None:
        self._registry = registry

    async def ensure_ready(self, _service_ids: tuple[ServiceId, ...]) -> None:
        assert not self._registry.transaction_open
        self._registry.events.append("readiness")


class _FakeUpstream:
    def __init__(self, registry: _FakeLeaseRegistry, *, block: asyncio.Event | None = None) -> None:
        self._registry = registry
        self._block = block

    async def complete(self, _request: ChatCompletionRequest) -> ChatCompletionResponse:
        assert not self._registry.transaction_open
        self._registry.events.append("upstream")
        if self._block is not None:
            await self._block.wait()
        return ChatCompletionResponse.model_validate(_response_payload())


def _service(
    registry: _FakeLeaseRegistry,
    *,
    upstream: _FakeUpstream | None = None,
    limit: int = 1,
    heartbeat_interval: float = 60.0,
) -> LlmProxyService:
    return LlmProxyService(
        gate=InferenceConcurrencyGate(limit),
        lease_registry=registry,
        worker_id="worker-primary",
        service_id=ServiceId("llm"),
        lease_ttl_seconds=10,
        heartbeat_interval_seconds=heartbeat_interval,
        readiness=_FakeReadiness(registry),
        upstream=upstream or _FakeUpstream(registry),
    )


def _chat_request() -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(_request_payload())


def test_core_ordering_and_short_transaction_boundaries() -> None:
    registry = _FakeLeaseRegistry()
    result = asyncio.run(_service(registry).complete(_chat_request()))

    assert result.object == "chat.completion"
    assert registry.events == ["acquire", "readiness", "upstream", "release"]


def test_concurrency_limit_rejects_without_waiting_or_acquiring_lease() -> None:
    async def run() -> list[str]:
        registry = _FakeLeaseRegistry()
        unblock = asyncio.Event()
        service = _service(registry, upstream=_FakeUpstream(registry, block=unblock))
        first = asyncio.create_task(service.complete(_chat_request()))
        while "upstream" not in registry.events:
            await asyncio.sleep(0)
        with pytest.raises(InferenceOverloadedError):
            await service.complete(_chat_request())
        unblock.set()
        await first
        return registry.events

    events = asyncio.run(run())
    assert events.count("acquire") == 1
    assert events.count("release") == 1


def test_gateway_rejects_overload_before_parsing_request_body(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    gateway_config_data["policies"]["concurrency"]["max_concurrent_inference_requests"] = 1
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    upstream_started = asyncio.Event()
    finish_upstream = asyncio.Event()

    async def upstream(_request: httpx.Request) -> httpx.Response:
        upstream_started.set()
        await finish_upstream.wait()
        return httpx.Response(200, json=_response_payload())

    async def run() -> tuple[httpx.Response, httpx.Response]:
        app = _app(loaded, migrated_database, httpx.MockTransport(upstream))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://control-plane.example.invalid",
            ) as client:
                headers = {"Authorization": f"Bearer {CLIENT_KEY}"}
                first_task = asyncio.create_task(
                    client.post(
                        "/v1/chat/completions",
                        headers=headers,
                        json=_request_payload(),
                    )
                )
                await upstream_started.wait()
                overloaded = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    content=b"x" * (1_048_576 + 1),
                )
                finish_upstream.set()
                return overloaded, await first_task
        finally:
            await app.state.oasix_resources.aclose()

    overloaded, completed = asyncio.run(run())
    assert overloaded.status_code == 429
    assert completed.status_code == 200


def test_client_cancellation_releases_lease_and_cancels_upstream() -> None:
    async def run() -> list[str]:
        registry = _FakeLeaseRegistry()
        service = _service(
            registry,
            upstream=_FakeUpstream(registry, block=asyncio.Event()),
        )
        task = asyncio.create_task(service.complete(_chat_request()))
        while "upstream" not in registry.events:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return registry.events

    assert asyncio.run(run()) == ["acquire", "readiness", "upstream", "release"]


def test_heartbeat_failure_cancels_usage_and_release_failure_is_not_silent() -> None:
    async def renewal_failure() -> list[str]:
        registry = _FakeLeaseRegistry(fail_renew=True)
        service = _service(
            registry,
            upstream=_FakeUpstream(registry, block=asyncio.Event()),
            heartbeat_interval=0.01,
        )
        with pytest.raises(InferenceLeaseRenewalError):
            await service.complete(_chat_request())
        return registry.events

    renewal_events = asyncio.run(renewal_failure())
    assert renewal_events == ["acquire", "readiness", "upstream", "renew", "release"]

    registry = _FakeLeaseRegistry(fail_release=True)
    with pytest.raises(InferenceLeaseReleaseError):
        asyncio.run(_service(registry).complete(_chat_request()))
    assert registry.events[-1] == "release"


def test_gateway_error_does_not_expose_prompt_or_provider_secret(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (secret_directory / "llm_api_token").write_text(
        SENTINEL_PROVIDER_SECRET,
        encoding="utf-8",
    )
    loaded = _loaded(gateway_config_data, write_config, secret_directory)

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {SENTINEL_PROVIDER_SECRET}"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps({"private": SENTINEL_PROMPT}).encode(),
        )

    response = asyncio.run(
        _request_app(
            _app(loaded, migrated_database, httpx.MockTransport(upstream)),
            headers={"Authorization": f"Bearer {CLIENT_KEY}"},
            payload=_request_payload(content=SENTINEL_PROMPT),
        )
    )

    assert response.status_code == 502
    assert SENTINEL_PROMPT not in response.text
    assert SENTINEL_PROVIDER_SECRET not in response.text
    assert SENTINEL_PROMPT not in caplog.text
    assert SENTINEL_PROVIDER_SECRET not in caplog.text
