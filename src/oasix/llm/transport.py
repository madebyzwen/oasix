"""Bounded HTTP transport for the configured provider-neutral LLM service."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
from pydantic import ValidationError

from oasix.config.models import ServiceSettings
from oasix.config.secrets import ResolvedSecrets
from oasix.llm.errors import (
    LlmUpstreamError,
    LlmUpstreamProtocolError,
    LlmUpstreamTimeoutError,
)
from oasix.llm.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)

MAX_UPSTREAM_RESPONSE_BYTES = 8 * 1_048_576
MAX_STREAM_EVENT_BYTES = 1_048_576
MAX_STREAM_RESPONSE_BYTES = 64 * 1_048_576


class HttpLlmUpstream:
    """Forward only the supported subset to one configured LLM service."""

    __slots__ = ("_client", "_secrets", "_service", "_timeout_seconds", "_url")

    def __init__(
        self,
        service: ServiceSettings,
        secrets: ResolvedSecrets,
        timeout_seconds: int,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._service = service
        self._secrets = secrets
        self._timeout_seconds = float(timeout_seconds)
        base_url = httpx.URL(str(service.endpoint))
        path = f"{base_url.path.rstrip('/')}/v1/chat/completions"
        self._url = base_url.copy_with(path=path, query=None, fragment=None)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds)),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return "HttpLlmUpstream(endpoint=<configured>, secrets=<resolved>)"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Return one validated non-streaming response behind a redaction boundary."""

        error_type: type[LlmUpstreamError] | None = None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._complete(request)
        except TimeoutError:
            error_type = LlmUpstreamTimeoutError
        except LlmUpstreamTimeoutError:
            error_type = LlmUpstreamTimeoutError
        except LlmUpstreamProtocolError:
            error_type = LlmUpstreamProtocolError
        except LlmUpstreamError:
            error_type = LlmUpstreamError
        except httpx.TimeoutException:
            error_type = LlmUpstreamTimeoutError
        except Exception:
            error_type = LlmUpstreamError
        request = None  # type: ignore[assignment]
        if error_type is None:  # pragma: no cover - every exception path assigns it
            raise AssertionError("upstream error mapping produced no category")
        raise error_type from None

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[bytes]:
        """Yield validated SSE events without buffering the complete response."""

        error_type: type[LlmUpstreamError] | None = None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async for event in self._stream(request):
                    yield event
            return
        except asyncio.CancelledError:
            request = None  # type: ignore[assignment]
            raise asyncio.CancelledError from None
        except TimeoutError:
            error_type = LlmUpstreamTimeoutError
        except LlmUpstreamTimeoutError:
            error_type = LlmUpstreamTimeoutError
        except LlmUpstreamProtocolError:
            error_type = LlmUpstreamProtocolError
        except LlmUpstreamError:
            error_type = LlmUpstreamError
        except httpx.TimeoutException:
            error_type = LlmUpstreamTimeoutError
        except Exception:
            error_type = LlmUpstreamError
        request = None  # type: ignore[assignment]
        if error_type is None:  # pragma: no cover - every exception path assigns it
            raise AssertionError("upstream stream error mapping produced no category")
        raise error_type from None

    async def _complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        outbound = self._client.build_request(
            "POST",
            self._url,
            json=request.model_dump(mode="json", exclude_none=True),
            headers={"Accept-Encoding": "identity"},
        )
        self._apply_authentication(outbound)
        response = await self._client.send(outbound, stream=True, follow_redirects=False)
        try:
            if not 200 <= response.status_code < 300:
                raise LlmUpstreamError from None
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise LlmUpstreamProtocolError from None
            if response.headers.get("content-encoding", "identity").casefold() != "identity":
                raise LlmUpstreamProtocolError from None
            body = await _read_bounded_response(response)
        finally:
            try:
                await response.aclose()
            except Exception:
                raise LlmUpstreamError from None

        try:
            document = json.loads(body, object_pairs_hook=_unique_json_object)
            result = ChatCompletionResponse.model_validate(document)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError):
            body = b""
            raise LlmUpstreamProtocolError from None
        body = b""
        document = None
        return result

    async def _stream(self, request: ChatCompletionRequest) -> AsyncIterator[bytes]:
        outbound = self._client.build_request(
            "POST",
            self._url,
            json=request.model_dump(mode="json", exclude_none=True),
            headers={
                "Accept": "text/event-stream",
                "Accept-Encoding": "identity",
            },
        )
        self._apply_authentication(outbound)
        response = await self._client.send(outbound, stream=True, follow_redirects=False)
        try:
            if not 200 <= response.status_code < 300:
                raise LlmUpstreamError from None
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "text/event-stream":
                raise LlmUpstreamProtocolError from None
            if response.headers.get("content-encoding", "identity").casefold() != "identity":
                raise LlmUpstreamProtocolError from None
            _validate_declared_length(response, MAX_STREAM_RESPONSE_BYTES)
            async for event in _validated_sse_events(response):
                yield event
        finally:
            try:
                await response.aclose()
            except Exception:
                raise LlmUpstreamError from None

    def _apply_authentication(self, request: httpx.Request) -> None:
        authentication = self._service.auth
        if authentication.method == "bearer":
            secret = self._secrets.get(authentication.secret).get_secret_value()
            request.headers["Authorization"] = f"Bearer {secret}"
            secret = ""
        elif authentication.method == "header":
            secret = self._secrets.get(authentication.secret).get_secret_value()
            request.headers[authentication.header_name] = secret
            secret = ""


async def _read_bounded_response(response: httpx.Response) -> bytes:
    _validate_declared_length(response, MAX_UPSTREAM_RESPONSE_BYTES)
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_UPSTREAM_RESPONSE_BYTES:
            chunks.clear()
            raise LlmUpstreamProtocolError from None
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_declared_length(response: httpx.Response, limit: int) -> None:
    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
            if parsed_length < 0 or parsed_length > limit:
                raise LlmUpstreamProtocolError from None
        except ValueError:
            raise LlmUpstreamProtocolError from None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LlmUpstreamProtocolError from None
        result[key] = value
    return result


async def _validated_sse_events(response: httpx.Response) -> AsyncIterator[bytes]:
    buffer = bytearray()
    data_lines: list[bytes] = []
    event_size = 0
    total_size = 0

    async for chunk in response.aiter_bytes():
        total_size += len(chunk)
        if total_size > MAX_STREAM_RESPONSE_BYTES:
            raise LlmUpstreamProtocolError from None
        buffer.extend(chunk)
        if len(buffer) + event_size > MAX_STREAM_EVENT_BYTES:
            raise LlmUpstreamProtocolError from None

        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            if line.endswith(b"\r"):
                line = line[:-1]

            if line == b"":
                if not data_lines:
                    event_size = 0
                    continue
                event = b"\n".join(data_lines)
                data_lines.clear()
                event_size = 0
                if event == b"[DONE]":
                    return
                yield _validated_sse_chunk(event)
                continue

            event_size += len(line) + 1
            if event_size > MAX_STREAM_EVENT_BYTES:
                raise LlmUpstreamProtocolError from None
            if line.startswith(b":"):
                continue
            if not line.startswith(b"data:"):
                raise LlmUpstreamProtocolError from None
            data = line[5:]
            if data.startswith(b" "):
                data = data[1:]
            data_lines.append(data)

    raise LlmUpstreamProtocolError from None


def _validated_sse_chunk(event: bytes) -> bytes:
    try:
        document = json.loads(event, object_pairs_hook=_unique_json_object)
        chunk = ChatCompletionChunk.model_validate(document)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError):
        event = b""
        raise LlmUpstreamProtocolError from None
    event = b""
    document = None
    serialized = chunk.model_dump_json(exclude_none=True).encode("utf-8")
    return b"data: " + serialized + b"\n\n"
