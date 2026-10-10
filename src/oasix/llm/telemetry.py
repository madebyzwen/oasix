"""Safe structured telemetry for the interactive LLM path."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from oasix.llm.models import TokenUsage
from oasix.logging import (
    EventDefinition,
    LogCompletionStatus,
    LogErrorClass,
    StructuredLogger,
    create_structured_logger,
)
from oasix.worker import WorkerReadinessTiming

type MonotonicClock = Callable[[], int]

_REQUEST_COMPLETED = EventDefinition(
    "llm.request_completed",
    "LLM request completed.",
)
_REQUEST_FAILED = EventDefinition(
    "llm.request_failed",
    "LLM request failed.",
)
_REQUEST_CANCELLED = EventDefinition(
    "llm.request_cancelled",
    "LLM request was cancelled.",
)


class LlmTelemetry:
    """Create isolated request spans backed by the safe logging contract."""

    __slots__ = ("_logger", "_monotonic_ns")

    def __init__(
        self,
        logger: StructuredLogger | None = None,
        *,
        monotonic_ns: MonotonicClock = time.monotonic_ns,
    ) -> None:
        self._logger = logger or create_structured_logger("llm_gateway")
        self._monotonic_ns = monotonic_ns

    def __repr__(self) -> str:
        return "LlmTelemetry(logger=<configured>)"

    def now(self) -> int:
        """Capture a monotonic request start without exposing wall-clock data."""

        return self._monotonic_ns()

    def start(
        self,
        request_id: str,
        worker_id: str,
        *,
        started_at: int | None = None,
    ) -> LlmRequestSpan:
        return LlmRequestSpan(
            self._logger,
            self._monotonic_ns,
            request_id,
            worker_id,
            started_at=started_at,
        )


class LlmRequestSpan:
    """Collect only allowlisted metrics and emit exactly one terminal event."""

    __slots__ = (
        "_completion_tokens",
        "_finished",
        "_first_token_at",
        "_lease_id",
        "_logger",
        "_monotonic_ns",
        "_prompt_tokens",
        "_readiness_latency_ms",
        "_request_id",
        "_started_at",
        "_total_tokens",
        "_wake_latency_ms",
        "_worker_id",
    )

    def __init__(
        self,
        logger: StructuredLogger,
        monotonic_ns: MonotonicClock,
        request_id: str,
        worker_id: str,
        *,
        started_at: int | None,
    ) -> None:
        self._logger = logger
        self._monotonic_ns = monotonic_ns
        self._request_id = request_id
        self._worker_id = worker_id
        self._started_at = monotonic_ns() if started_at is None else started_at
        self._first_token_at: int | None = None
        self._lease_id: str | None = None
        self._wake_latency_ms: int | None = None
        self._readiness_latency_ms: int | None = None
        self._prompt_tokens: int | None = None
        self._completion_tokens: int | None = None
        self._total_tokens: int | None = None
        self._finished = False

    def __repr__(self) -> str:
        return "LlmRequestSpan(<safe-telemetry>)"

    def record_lease(self, lease_id: str) -> None:
        self._lease_id = lease_id

    def record_readiness(self, timing: WorkerReadinessTiming | None) -> None:
        if timing is not None:
            self._readiness_latency_ms = timing.readiness_latency_ms
            self._wake_latency_ms = timing.wake_latency_ms

    def record_first_token(self) -> None:
        if self._first_token_at is None:
            self._first_token_at = self._monotonic_ns()

    def record_usage(self, usage: TokenUsage | None) -> None:
        if usage is not None:
            self._prompt_tokens = usage.prompt_tokens
            self._completion_tokens = usage.completion_tokens
            self._total_tokens = usage.total_tokens

    def succeed(self) -> None:
        self._finish(logging.INFO, _REQUEST_COMPLETED, LogCompletionStatus.SUCCEEDED)

    def fail(self, error_class: LogErrorClass, error_code: str) -> None:
        self._finish(
            logging.ERROR,
            _REQUEST_FAILED,
            LogCompletionStatus.FAILED,
            error_class=error_class,
            error_code=error_code,
        )

    def cancel(self) -> None:
        self._finish(
            logging.WARNING,
            _REQUEST_CANCELLED,
            LogCompletionStatus.CANCELLED,
        )

    def _finish(
        self,
        level: int,
        event: EventDefinition,
        status: LogCompletionStatus,
        *,
        error_class: LogErrorClass | None = None,
        error_code: str | None = None,
    ) -> None:
        if self._finished:
            return
        completed_at = self._monotonic_ns()
        fields: dict[str, object] = {
            "request_id": self._request_id,
            "worker_id": self._worker_id,
            "request_duration_ms": max(0, (completed_at - self._started_at) // 1_000_000),
            "completion_status": status,
        }
        optional = {
            "lease_id": self._lease_id,
            "wake_latency_ms": self._wake_latency_ms,
            "readiness_latency_ms": self._readiness_latency_ms,
            "time_to_first_token_ms": (
                None
                if self._first_token_at is None
                else max(0, (self._first_token_at - self._started_at) // 1_000_000)
            ),
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "total_tokens": self._total_tokens,
            "error_class": error_class,
            "error_code": error_code,
        }
        fields.update({name: value for name, value in optional.items() if value is not None})
        self._finished = True
        self._logger.emit(level, event, fields)
