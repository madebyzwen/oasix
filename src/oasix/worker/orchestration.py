"""Bounded wake and service-readiness orchestration."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from oasix.config.models import RetryPolicy
from oasix.worker.contracts import (
    ServiceId,
    ServiceReadinessProbe,
    WorkerId,
    WorkerWakeController,
)
from oasix.worker.errors import (
    WorkerCommunicationError,
    WorkerConfigurationError,
    WorkerTimeoutError,
    WorkerUnavailableError,
)

type AsyncSleep = Callable[[float], Awaitable[None]]
type JitterSource = Callable[[], float]
type MonotonicClock = Callable[[], int]


@dataclass(frozen=True, slots=True)
class WorkerReadinessTiming:
    """Available monotonic latencies for one successful readiness operation."""

    readiness_latency_ms: int
    wake_latency_ms: int | None


class WorkerReadinessOrchestrator:
    """Wake one configured worker until all requested services are ready."""

    __slots__ = (
        "_jitter_source",
        "_monotonic_ns",
        "_readiness",
        "_retry",
        "_sleep",
        "_timeout_seconds",
        "_wake",
        "_worker_id",
    )

    def __init__(
        self,
        wake: WorkerWakeController,
        readiness: ServiceReadinessProbe,
        retry: RetryPolicy,
        timeout_seconds: float,
        *,
        sleep: AsyncSleep = asyncio.sleep,
        jitter_source: JitterSource = random.random,
        monotonic_ns: MonotonicClock = time.monotonic_ns,
    ) -> None:
        if wake.worker_id != readiness.worker_id:
            raise WorkerConfigurationError from None
        if type(timeout_seconds) not in {int, float} or timeout_seconds <= 0:
            raise WorkerConfigurationError from None
        self._worker_id = wake.worker_id
        self._wake = wake
        self._readiness = readiness
        self._retry = retry
        self._timeout_seconds = float(timeout_seconds)
        self._sleep = sleep
        self._jitter_source = jitter_source
        self._monotonic_ns = monotonic_ns

    def __repr__(self) -> str:
        return f"WorkerReadinessOrchestrator(worker_id={self.worker_id!r})"

    @property
    def worker_id(self) -> WorkerId:
        return self._worker_id

    async def ensure_ready(self, service_ids: Sequence[ServiceId]) -> WorkerReadinessTiming:
        """Return only after every requested service confirms readiness."""

        services = tuple(service_ids)
        if not services or any(type(service_id) is not str for service_id in services):
            raise WorkerConfigurationError from None
        if len(services) != len(set(services)):
            raise WorkerConfigurationError from None

        started_at = self._monotonic_ns()
        if await self._services_ready(services):
            return self._timing(started_at, None)

        wake_started_at: int | None = None
        for attempt_index in range(self._retry.max_attempts):
            if wake_started_at is None:
                wake_started_at = self._monotonic_ns()
            await self._bounded_wake()
            await self._sleep(self._retry_delay(attempt_index))
            if await self._services_ready(services):
                return self._timing(started_at, wake_started_at)
        raise WorkerUnavailableError from None

    def _timing(
        self,
        started_at: int,
        wake_started_at: int | None,
    ) -> WorkerReadinessTiming:
        completed_at = self._monotonic_ns()
        return WorkerReadinessTiming(
            readiness_latency_ms=max(0, (completed_at - started_at) // 1_000_000),
            wake_latency_ms=(
                None
                if wake_started_at is None
                else max(0, (completed_at - wake_started_at) // 1_000_000)
            ),
        )

    async def _bounded_wake(self) -> None:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await self._wake.wake()
        except TimeoutError:
            return
        except (WorkerCommunicationError, WorkerTimeoutError):
            return

    async def _services_ready(self, services: tuple[ServiceId, ...]) -> bool:
        for service_id in services:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    observation = await self._readiness.check_readiness(service_id)
            except TimeoutError:
                return False
            except (WorkerCommunicationError, WorkerTimeoutError):
                return False
            if not observation.ready:
                return False
        return True

    def _retry_delay(self, attempt_index: int) -> float:
        base_delay = min(
            self._retry.initial_delay_seconds * (self._retry.multiplier**attempt_index),
            self._retry.max_delay_seconds,
        )
        if self._retry.jitter is None or base_delay == 0:
            return base_delay
        try:
            sample = self._jitter_source()
        except Exception:
            raise WorkerConfigurationError from None
        if type(sample) is not float or not 0.0 <= sample <= 1.0:
            raise WorkerConfigurationError from None
        ratio = self._retry.jitter.ratio
        return max(0.0, base_delay * (1.0 + ((2.0 * sample - 1.0) * ratio)))
