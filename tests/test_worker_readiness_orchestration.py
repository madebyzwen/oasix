from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from oasix.config import BootstrapSettings, RuntimeConfig, load_startup_configuration
from oasix.worker import (
    ServiceId,
    ServiceReadinessObservation,
    WorkerId,
    WorkerReadinessOrchestrator,
    WorkerUnavailableError,
    create_http_service_readiness_adapter,
    create_worker_wake_controller,
)


class _FakeWakeController:
    def __init__(self, worker_id: str, *, hang: bool = False) -> None:
        self._worker_id = WorkerId(worker_id)
        self.calls = 0
        self.hang = hang

    @property
    def worker_id(self) -> WorkerId:
        return self._worker_id

    async def wake(self) -> None:
        self.calls += 1
        if self.hang:
            await asyncio.Event().wait()


class _SequenceReadinessProbe:
    def __init__(
        self,
        worker_id: str,
        results: Sequence[bool],
        *,
        hang: bool = False,
    ) -> None:
        self._worker_id = WorkerId(worker_id)
        self.results = list(results)
        self.calls: list[ServiceId] = []
        self.hang = hang

    @property
    def worker_id(self) -> WorkerId:
        return self._worker_id

    async def check_readiness(self, service_id: ServiceId) -> ServiceReadinessObservation:
        self.calls.append(service_id)
        if self.hang:
            await asyncio.Event().wait()
        return ServiceReadinessObservation(self.results.pop(0))


def _retry_policy(valid_config_data: dict[str, Any]):
    return RuntimeConfig.model_validate(valid_config_data).policies.retry.wake


def test_already_ready_worker_is_not_woken(valid_config_data: dict[str, Any]) -> None:
    wake = _FakeWakeController("worker-primary")
    readiness = _SequenceReadinessProbe("worker-primary", [True])
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    orchestrator = WorkerReadinessOrchestrator(
        wake,
        readiness,
        _retry_policy(valid_config_data),
        1,
        sleep=sleep,
    )
    asyncio.run(orchestrator.ensure_ready((ServiceId("llm"),)))

    assert wake.calls == 0
    assert readiness.calls == [ServiceId("llm")]
    assert sleeps == []


def test_wake_success_does_not_imply_readiness_and_retries_are_bounded(
    valid_config_data: dict[str, Any],
) -> None:
    wake = _FakeWakeController("worker-primary")
    readiness = _SequenceReadinessProbe("worker-primary", [False, False, True])
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    orchestrator = WorkerReadinessOrchestrator(
        wake,
        readiness,
        _retry_policy(valid_config_data),
        1,
        sleep=sleep,
    )
    asyncio.run(orchestrator.ensure_ready((ServiceId("llm"),)))

    assert wake.calls == 2
    assert len(readiness.calls) == 3
    assert sleeps == [10.0, 20.0]


def test_exhausted_wake_attempts_fail_without_unbounded_loop(
    valid_config_data: dict[str, Any],
) -> None:
    wake = _FakeWakeController("worker-primary")
    readiness = _SequenceReadinessProbe("worker-primary", [False, False, False])

    async def sleep(delay: float) -> None:
        return None

    orchestrator = WorkerReadinessOrchestrator(
        wake,
        readiness,
        _retry_policy(valid_config_data),
        1,
        sleep=sleep,
    )

    with pytest.raises(WorkerUnavailableError):
        asyncio.run(orchestrator.ensure_ready((ServiceId("llm"),)))

    assert wake.calls == 2
    assert len(readiness.calls) == 3


def test_all_requested_services_must_confirm_readiness(
    valid_config_data: dict[str, Any],
) -> None:
    wake = _FakeWakeController("worker-primary")
    readiness = _SequenceReadinessProbe("worker-primary", [True, False, True, True])

    async def sleep(delay: float) -> None:
        return None

    orchestrator = WorkerReadinessOrchestrator(
        wake,
        readiness,
        _retry_policy(valid_config_data),
        1,
        sleep=sleep,
    )
    services = (ServiceId("llm"), ServiceId("agent-runtime"))
    asyncio.run(orchestrator.ensure_ready(services))

    assert wake.calls == 1
    assert readiness.calls == [
        ServiceId("llm"),
        ServiceId("agent-runtime"),
        ServiceId("llm"),
        ServiceId("agent-runtime"),
    ]


def test_configured_jitter_is_applied_with_bounded_sample(
    valid_config_data: dict[str, Any],
) -> None:
    valid_config_data["policies"]["retry"]["wake"]["jitter"] = {"ratio": 0.2}
    wake = _FakeWakeController("worker-primary")
    readiness = _SequenceReadinessProbe("worker-primary", [False, False, True])
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    orchestrator = WorkerReadinessOrchestrator(
        wake,
        readiness,
        _retry_policy(valid_config_data),
        1,
        sleep=sleep,
        jitter_source=lambda: 1.0,
    )
    asyncio.run(orchestrator.ensure_ready((ServiceId("llm"),)))

    assert sleeps == [12.0, 24.0]


@pytest.mark.parametrize("hanging_component", ["wake", "readiness"])
def test_wake_and_readiness_are_bounded_by_timeout(
    hanging_component: str,
    valid_config_data: dict[str, Any],
) -> None:
    wake = _FakeWakeController("worker-primary", hang=hanging_component == "wake")
    readiness = _SequenceReadinessProbe(
        "worker-primary",
        [False, False, False],
        hang=hanging_component == "readiness",
    )

    async def sleep(delay: float) -> None:
        return None

    orchestrator = WorkerReadinessOrchestrator(
        wake,
        readiness,
        _retry_policy(valid_config_data),
        0.001,
        sleep=sleep,
    )

    with pytest.raises(WorkerUnavailableError):
        asyncio.run(orchestrator.ensure_ready((ServiceId("llm"),)))

    assert wake.calls <= 2
    assert len(readiness.calls) <= 3


def test_integrates_configured_wake_with_http_service_readiness(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    loaded = load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(valid_config_data),
            secrets_directory=secret_directory,
        )
    )
    statuses = iter((503, 503, 200))
    wake_deliveries: list[tuple[bytes, str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses))

    async def sender(packet: bytes, host: str, port: int) -> None:
        wake_deliveries.append((packet, host, port))

    async def no_sleep(delay: float) -> None:
        return None

    async def run() -> None:
        async with create_http_service_readiness_adapter(
            loaded.runtime,
            loaded.secrets,
            transport=httpx.MockTransport(handler),
        ) as readiness:
            wake = create_worker_wake_controller(loaded.runtime, sender=sender)
            orchestrator = WorkerReadinessOrchestrator(
                wake,
                readiness,
                loaded.runtime.policies.retry.wake,
                loaded.runtime.policies.readiness_timeout_seconds,
                sleep=no_sleep,
            )
            await orchestrator.ensure_ready((ServiceId("llm"),))

    asyncio.run(run())

    assert len(wake_deliveries) == 2
    assert all(len(packet) == 102 for packet, _, _ in wake_deliveries)
