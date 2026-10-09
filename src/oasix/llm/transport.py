"""Bounded HTTP transport for the configured provider-neutral LLM service."""

from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import ValidationError

from oasix.config.models import ServiceSettings
from oasix.config.secrets import ResolvedSecrets
from oasix.llm.errors import (
    LlmUpstreamError,
    LlmUpstreamProtocolError,
    LlmUpstreamTimeoutError,
)
from oasix.llm.models import ChatCompletionRequest, ChatCompletionResponse

MAX_UPSTREAM_RESPONSE_BYTES = 8 * 1_048_576


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
    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
            if parsed_length < 0 or parsed_length > MAX_UPSTREAM_RESPONSE_BYTES:
                raise LlmUpstreamProtocolError from None
        except ValueError:
            raise LlmUpstreamProtocolError from None
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_UPSTREAM_RESPONSE_BYTES:
            chunks.clear()
            raise LlmUpstreamProtocolError from None
        chunks.append(chunk)
    return b"".join(chunks)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LlmUpstreamProtocolError from None
        result[key] = value
    return result
