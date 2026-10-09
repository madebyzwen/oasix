"""Lease-protected non-streaming inference orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from oasix.llm.errors import (
    InferenceOverloadedError,
    LlmPathError,
    LlmReadinessError,
    LlmUpstreamError,
)
from oasix.llm.leases import InferenceLeaseRegistry, InferenceLeaseSession
from oasix.llm.models import ChatCompletionRequest, ChatCompletionResponse
from oasix.worker.contracts import ServiceId
from oasix.worker.errors import WorkerInteractionError


class ReadinessController(Protocol):
    async def ensure_ready(self, service_ids: tuple[ServiceId, ...]) -> None: ...


class NonStreamingUpstream(Protocol):
    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse: ...


class InferenceConcurrencyGate:
    """A bounded token pool with immediate overload rejection and no wait queue."""

    __slots__ = ("_tokens",)

    def __init__(self, limit: int) -> None:
        self._tokens: asyncio.Queue[object] = asyncio.Queue(maxsize=limit)
        for _ in range(limit):
            self._tokens.put_nowait(object())

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        try:
            token = self._tokens.get_nowait()
        except asyncio.QueueEmpty:
            raise InferenceOverloadedError from None
        try:
            yield
        finally:
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

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Admit and run one request, replacing content-retaining tracebacks."""

        async with self.admission():
            return await self.complete_admitted(request)

    @asynccontextmanager
    async def admission(self) -> AsyncIterator[None]:
        """Reserve capacity immediately, without introducing a waiting queue."""

        async with self._gate.slot():
            yield

    async def complete_admitted(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Run an already-admitted request and replace sensitive tracebacks."""

        error_type: type[LlmPathError] | None = None
        try:
            return await self._complete_admitted(request)
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

    async def _complete_admitted(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        async with InferenceLeaseSession(
            self._lease_registry,
            self._worker_id,
            self._lease_ttl,
            self._heartbeat_interval,
        ) as lease:
            await self._readiness.ensure_ready((self._service_id,))
            lease.ensure_healthy()
            response = await self._upstream.complete(request)
            lease.ensure_healthy()
            return response
