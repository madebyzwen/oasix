from __future__ import annotations

import asyncio
import json
import uuid
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.requests import ClientDisconnect

from oasix.config import BootstrapSettings, LoadedConfiguration, load_startup_configuration
from oasix.llm import (
    MAX_STREAM_EVENT_BYTES,
    ChatCompletionRequest,
    ChatCompletionResponse,
    HttpLlmUpstream,
    InferenceConcurrencyGate,
    InferenceLeaseReleaseError,
    InferenceLeaseRenewalError,
    InferenceOverloadedError,
    LlmProxyService,
    LlmRequestSpan,
    LlmTelemetry,
    create_llm_gateway_app,
)
from oasix.llm.gateway import _ManagedStreamingResponse, _safe_stream
from oasix.logging import StructuredLogger, create_structured_logger
from oasix.persistence import Lease, PersistenceDatabase
from oasix.worker import ServiceId

CLIENT_KEY = "inference-client-key-current"


def _loaded(
    data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    *,
    heartbeat_interval: float = 2.0,
    request_timeout: int = 30,
    disable_wake: bool = True,
) -> LoadedConfiguration:
    if disable_wake:
        data["workers"][data["active_worker"]]["power"]["wake"] = {"method": "none"}
    data["policies"]["inference"].update(
        heartbeat_interval_seconds=heartbeat_interval,
        request_timeout_seconds=request_timeout,
    )
    return load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(data),
            secrets_directory=secret_directory,
        )
    )


def _request_payload() -> dict[str, Any]:
    return {
        "model": "generic-chat-model",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True,
    }


def _chunk_payload(
    content: str | None = None,
    *,
    role: str | None = None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    return {
        "id": "stream-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "generic-chat-model",
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def _sse(payload: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(payload, separators=(",", ":")).encode() + b"\n\n"


class _AsyncChunks(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: list[bytes],
        *,
        delay: float = 0.0,
        failure: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.delay = delay
        self.failure = failure
        self.emitted = 0
        self.closed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.emitted += 1
            yield chunk
        if self.failure is not None:
            raise self.failure

    async def aclose(self) -> None:
        self.closed = True


def _stream_response(stream: httpx.AsyncByteStream) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=stream,
    )


def _app(
    loaded: LoadedConfiguration,
    database: PersistenceDatabase,
    stream: httpx.AsyncByteStream,
    *,
    readiness_transport: httpx.AsyncBaseTransport | None = None,
    wake_sender: Any = None,
    structured_logger: StructuredLogger | None = None,
) -> Any:
    return create_llm_gateway_app(
        loaded.runtime,
        loaded.secrets,
        database,
        readiness_transport=readiness_transport
        or httpx.MockTransport(lambda _request: httpx.Response(200)),
        upstream_transport=httpx.MockTransport(lambda _request: _stream_response(stream)),
        wake_sender=wake_sender,
        structured_logger=structured_logger,
    )


async def _post_stream(app: Any) -> httpx.Response:
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://control-plane.example.invalid",
        ) as client:
            return await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {CLIENT_KEY}"},
                json=_request_payload(),
            )
    finally:
        await app.state.oasix_resources.aclose()


def test_gateway_streams_multiple_validated_sse_events_and_done(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    serialized = (
        _sse(_chunk_payload(role="assistant"))
        + _sse(_chunk_payload("Hello"))
        + _sse(_chunk_payload(finish_reason="stop"))
        + b"data: [DONE]\n\n"
    )
    stream = _AsyncChunks([serialized[:17], serialized[17:61], serialized[61:]])

    response = asyncio.run(_post_stream(_app(loaded, migrated_database, stream)))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.content.count(b"data: {") == 3
    assert response.content.endswith(b"data: [DONE]\n\n")
    assert stream.closed
    with migrated_database.session() as session:
        lease = session.scalar(select(Lease))
        assert lease is not None
        assert lease.released_at is not None
        assert lease.release_reason == "completed"


def test_full_wake_readiness_streaming_path_emits_safe_correlated_telemetry(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    gateway_config_data["policies"]["retry"]["wake"].update(
        max_attempts=1,
        initial_delay_seconds=0.0,
        max_delay_seconds=0.0,
    )
    loaded = _loaded(
        gateway_config_data,
        write_config,
        secret_directory,
        disable_wake=False,
    )
    readiness_statuses = iter((503, 200))
    wake_deliveries: list[tuple[bytes, str, int]] = []
    output = StringIO()
    stream = _AsyncChunks(
        [
            _sse(_chunk_payload(role="assistant")),
            _sse(_chunk_payload("complete")),
            b"data: [DONE]\n\n",
        ]
    )

    async def wake_sender(packet: bytes, host: str, port: int) -> None:
        wake_deliveries.append((packet, host, port))

    app = _app(
        loaded,
        migrated_database,
        stream,
        readiness_transport=httpx.MockTransport(
            lambda _request: httpx.Response(next(readiness_statuses))
        ),
        wake_sender=wake_sender,
        structured_logger=create_structured_logger("llm_gateway", stream=output),
    )
    response = asyncio.run(_post_stream(app))

    event = json.loads(output.getvalue())
    with migrated_database.session() as session:
        lease = session.scalar(select(Lease))
        assert lease is not None
        assert lease.released_at is not None
    assert response.status_code == 200
    assert response.content.endswith(b"data: [DONE]\n\n")
    assert len(wake_deliveries) == 1
    assert event["event_code"] == "llm.request_completed"
    assert event["completion_status"] == "succeeded"
    assert event["request_id"] == response.headers["x-request-id"]
    assert event["worker_id"] == "worker-primary"
    assert event["lease_id"] == lease.lease_id
    assert event["request_duration_ms"] >= 0
    assert event["readiness_latency_ms"] >= 0
    assert event["wake_latency_ms"] >= 0
    assert event["time_to_first_token_ms"] >= 0
    assert "prompt_tokens" not in event
    assert "completion_tokens" not in event
    assert "total_tokens" not in event


def test_upstream_events_are_consumed_incrementally(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    source = _AsyncChunks(
        [
            _sse(_chunk_payload(role="assistant")),
            _sse(_chunk_payload("next")),
            b"data: [DONE]\n\n",
        ]
    )
    service = loaded.runtime.active_worker_profile.services["llm"]
    upstream = HttpLlmUpstream(
        service,
        loaded.secrets,
        30,
        transport=httpx.MockTransport(lambda _request: _stream_response(source)),
    )

    async def run() -> None:
        iterator = upstream.stream(ChatCompletionRequest.model_validate(_request_payload()))
        first = await anext(iterator)
        assert b'"role":"assistant"' in first
        assert source.emitted == 1
        remaining = [event async for event in iterator]
        assert len(remaining) == 1
        await upstream.aclose()

    asyncio.run(run())
    assert source.closed


@pytest.mark.parametrize("failure_mode", ["abort", "incomplete", "oversized"])
def test_stream_failure_emits_safe_error_without_done_and_releases_resources(
    failure_mode: str,
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(gateway_config_data, write_config, secret_directory)
    chunks = [_sse(_chunk_payload("partial"))]
    failure: Exception | None = None
    if failure_mode == "abort":
        failure = httpx.ReadError("SENTINEL-UPSTREAM-ABORT")
    elif failure_mode == "oversized":
        chunks.append(b"data: " + b"x" * MAX_STREAM_EVENT_BYTES)
    stream = _AsyncChunks(chunks, failure=failure)

    response = asyncio.run(_post_stream(_app(loaded, migrated_database, stream)))

    assert response.status_code == 200
    assert b'"content":"partial"' in response.content
    assert b'"code":"stream_failed"' in response.content
    assert b"[DONE]" not in response.content
    assert b"SENTINEL" not in response.content
    assert stream.closed
    with migrated_database.session() as session:
        lease = session.scalar(select(Lease))
        assert lease is not None and lease.released_at is not None


def test_slow_stream_keeps_lease_alive_with_heartbeats(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(
        gateway_config_data,
        write_config,
        secret_directory,
        heartbeat_interval=0.01,
    )
    stream = _AsyncChunks(
        [
            _sse(_chunk_payload(role="assistant")),
            _sse(_chunk_payload("slow")),
            b"data: [DONE]\n\n",
        ],
        delay=0.03,
    )

    response = asyncio.run(_post_stream(_app(loaded, migrated_database, stream)))

    assert response.content.endswith(b"data: [DONE]\n\n")
    with migrated_database.session() as session:
        lease = session.scalar(select(Lease))
        assert lease is not None
        assert lease.last_heartbeat_at > lease.created_at
        assert lease.released_at is not None


def test_stream_timeout_closes_upstream_and_never_reports_done(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    migrated_database: PersistenceDatabase,
) -> None:
    loaded = _loaded(
        gateway_config_data,
        write_config,
        secret_directory,
        request_timeout=1,
    )
    stream = _AsyncChunks(
        [_sse(_chunk_payload("late")), b"data: [DONE]\n\n"],
        delay=1.1,
    )

    response = asyncio.run(_post_stream(_app(loaded, migrated_database, stream)))

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"
    assert b"[DONE]" not in response.content
    assert stream.closed
    with migrated_database.session() as session:
        lease = session.scalar(select(Lease))
        assert lease is not None and lease.released_at is not None


class _FakeLeaseRegistry:
    def __init__(self, *, fail_renew: bool = False, fail_release: bool = False) -> None:
        self.events: list[str] = []
        self.fail_renew = fail_renew
        self.fail_release = fail_release

    async def acquire(self, _lease_id: str, _worker_id: str, _ttl_seconds: int) -> None:
        self.events.append("acquire")

    async def renew(self, _lease_id: str, _ttl_seconds: int) -> None:
        self.events.append("renew")
        if self.fail_renew:
            raise InferenceLeaseRenewalError

    async def release(self, _lease_id: str, _reason: str) -> None:
        self.events.append("release")
        if self.fail_release:
            raise InferenceLeaseReleaseError


class _Ready:
    async def ensure_ready(self, _service_ids: tuple[ServiceId, ...]) -> None:
        return None


class _BlockingStreamUpstream:
    def __init__(self, *, finish: bool = False) -> None:
        self.started = asyncio.Event()
        self.block = asyncio.Event()
        self.closed = False
        self.finish = finish

    async def complete(self, _request: ChatCompletionRequest) -> ChatCompletionResponse:
        raise AssertionError("non-streaming path must not be used")

    async def stream(self, _request: ChatCompletionRequest):  # type: ignore[no-untyped-def]
        try:
            self.started.set()
            yield _sse(_chunk_payload("partial"))
            if not self.finish:
                await self.block.wait()
        finally:
            self.closed = True


def _service(
    registry: _FakeLeaseRegistry,
    upstream: _BlockingStreamUpstream,
    *,
    heartbeat_interval: float = 60.0,
) -> LlmProxyService:
    return LlmProxyService(
        gate=InferenceConcurrencyGate(1),
        lease_registry=registry,
        worker_id="worker-primary",
        service_id=ServiceId("llm"),
        lease_ttl_seconds=10,
        heartbeat_interval_seconds=heartbeat_interval,
        readiness=_Ready(),
        upstream=upstream,
    )


def _chat_request() -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(_request_payload())


def _telemetry_span() -> LlmRequestSpan:
    telemetry = LlmTelemetry(create_structured_logger("llm_gateway", stream=StringIO()))
    return telemetry.start(str(uuid.uuid4()), "worker-primary")


def test_client_cancellation_closes_upstream_releases_lease_and_admission() -> None:
    async def run() -> tuple[list[str], bool, str]:
        registry = _FakeLeaseRegistry()
        upstream = _BlockingStreamUpstream()
        service = _service(registry, upstream)
        output = StringIO()
        telemetry = LlmTelemetry(create_structured_logger("llm_gateway", stream=output))
        span = telemetry.start(str(uuid.uuid4()), "worker-primary")
        body = _safe_stream(service, _chat_request(), service.admit(), span)
        response = _ManagedStreamingResponse(body, media_type="text/event-stream")

        async def receive() -> dict[str, str]:
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
        with pytest.raises(ClientDisconnect):
            await response(scope, receive, send)  # type: ignore[arg-type]
        replacement = service.admit()
        replacement.close()
        return registry.events, upstream.closed, output.getvalue()

    events, closed, telemetry_output = asyncio.run(run())
    assert events == ["acquire", "release"]
    assert closed
    telemetry_event = json.loads(telemetry_output)
    assert telemetry_event["event_code"] == "llm.request_cancelled"
    assert telemetry_event["completion_status"] == "cancelled"


def test_heartbeat_failure_stops_stream_and_never_reports_done() -> None:
    async def run() -> tuple[bytes, list[str], bool]:
        registry = _FakeLeaseRegistry(fail_renew=True)
        upstream = _BlockingStreamUpstream()
        service = _service(registry, upstream, heartbeat_interval=0.01)
        body = b"".join(
            [
                event
                async for event in _safe_stream(
                    service,
                    _chat_request(),
                    service.admit(),
                    _telemetry_span(),
                )
            ]
        )
        return body, registry.events, upstream.closed

    body, events, closed = asyncio.run(run())
    assert b'"code":"stream_failed"' in body
    assert b"[DONE]" not in body
    assert events == ["acquire", "renew", "release"]
    assert closed


def test_release_failure_replaces_done_with_stream_error() -> None:
    async def run() -> tuple[bytes, list[str]]:
        registry = _FakeLeaseRegistry(fail_release=True)
        upstream = _BlockingStreamUpstream(finish=True)
        service = _service(registry, upstream)
        body = b"".join(
            [
                event
                async for event in _safe_stream(
                    service,
                    _chat_request(),
                    service.admit(),
                    _telemetry_span(),
                )
            ]
        )
        return body, registry.events

    body, events = asyncio.run(run())
    assert b'"content":"partial"' in body
    assert b'"code":"stream_failed"' in body
    assert b"[DONE]" not in body
    assert events == ["acquire", "release"]


def test_concurrency_slot_remains_occupied_for_entire_stream() -> None:
    async def run() -> None:
        registry = _FakeLeaseRegistry()
        upstream = _BlockingStreamUpstream()
        service = _service(registry, upstream)
        admission = service.admit()
        stream = _safe_stream(service, _chat_request(), admission, _telemetry_span())

        async def consume() -> None:
            try:
                async for _event in stream:
                    await asyncio.sleep(0)
            finally:
                await stream.aclose()

        task = asyncio.create_task(consume())
        await upstream.started.wait()
        with pytest.raises(InferenceOverloadedError):
            service.admit()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        replacement = service.admit()
        replacement.close()

    asyncio.run(run())
