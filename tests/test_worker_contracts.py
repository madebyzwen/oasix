from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from oasix.config import RuntimeConfig
from oasix.persistence.models import WORKER_STATES
from oasix.worker import (
    ServiceId,
    ServiceReadinessObservation,
    ServiceReadinessProbe,
    WorkerCommunicationError,
    WorkerId,
    WorkerIdentity,
    WorkerLifecycleState,
    WorkerSleepController,
    WorkerStateObservation,
    WorkerStateObserver,
    WorkerTimeoutError,
    WorkerWakeController,
    resolve_active_worker,
)


class _ReadyFakeWorker:
    def __init__(self, worker_id: WorkerId) -> None:
        self._worker_id = worker_id
        self.wake_calls = 0
        self.sleep_calls = 0

    @property
    def worker_id(self) -> WorkerId:
        return self._worker_id

    async def observe_state(self) -> WorkerStateObservation:
        return WorkerStateObservation(state=WorkerLifecycleState.READY)

    async def check_readiness(self, service_id: ServiceId) -> ServiceReadinessObservation:
        return ServiceReadinessObservation(ready=service_id == ServiceId("llm"))

    async def wake(self) -> None:
        self.wake_calls += 1

    async def sleep(self) -> None:
        self.sleep_calls += 1


class _UnreachableFakeWorker:
    def __init__(self, worker_id: WorkerId) -> None:
        self._worker_id = worker_id

    @property
    def worker_id(self) -> WorkerId:
        return self._worker_id

    async def observe_state(self) -> WorkerStateObservation:
        try:
            raise OSError("SENTINEL-PRIVATE-CONNECTION")
        except OSError:
            raise WorkerCommunicationError() from None


def test_active_worker_is_resolved_only_from_validated_runtime_configuration(
    valid_config_data: dict[str, Any],
) -> None:
    second_profile = deepcopy(valid_config_data["workers"]["worker-primary"])
    second_profile["connection"]["host"] = "replacement-worker.example.invalid"
    valid_config_data["workers"]["worker-secondary"] = second_profile
    valid_config_data["active_worker"] = "worker-secondary"
    runtime = RuntimeConfig.model_validate(valid_config_data)

    target = resolve_active_worker(runtime)

    assert target.worker_id == WorkerId("worker-secondary")
    assert target.profile is runtime.workers["worker-secondary"]
    assert target.profile.connection.host == "replacement-worker.example.invalid"


def test_resolved_target_representation_hides_connection_and_secret_references(
    valid_config_data: dict[str, Any],
) -> None:
    profile = valid_config_data["workers"]["worker-primary"]
    profile["connection"]["host"] = "sentinel-private-host.example.invalid"
    profile["connection"]["ssh"]["private_key"]["name"] = "SENTINEL_PRIVATE_KEY_REF"
    runtime = RuntimeConfig.model_validate(valid_config_data)

    representation = repr(resolve_active_worker(runtime))

    assert "worker-primary" in representation
    assert "sentinel-private-host" not in representation
    assert "SENTINEL_PRIVATE_KEY_REF" not in representation


def test_fake_worker_satisfies_separate_capability_protocols() -> None:
    worker = _ReadyFakeWorker(WorkerId("worker-primary"))

    assert isinstance(worker, WorkerIdentity)
    assert isinstance(worker, WorkerStateObserver)
    assert isinstance(worker, ServiceReadinessProbe)
    assert isinstance(worker, WorkerWakeController)
    assert isinstance(worker, WorkerSleepController)

    state = asyncio.run(worker.observe_state())
    ready = asyncio.run(worker.check_readiness(ServiceId("llm")))
    not_ready = asyncio.run(worker.check_readiness(ServiceId("agents")))
    asyncio.run(worker.wake())
    asyncio.run(worker.sleep())

    assert state == WorkerStateObservation(WorkerLifecycleState.READY)
    assert ready == ServiceReadinessObservation(ready=True)
    assert not_ready == ServiceReadinessObservation(ready=False)
    assert worker.wake_calls == 1
    assert worker.sleep_calls == 1


def test_unreachable_worker_is_a_communication_error_not_a_sleeping_observation() -> None:
    worker: WorkerStateObserver = _UnreachableFakeWorker(WorkerId("worker-primary"))

    with pytest.raises(WorkerCommunicationError) as captured:
        asyncio.run(worker.observe_state())

    assert "SLEEPING" not in str(captured.value)
    assert "SENTINEL-PRIVATE-CONNECTION" not in str(captured.value)
    assert "SENTINEL-PRIVATE-CONNECTION" not in repr(captured.value)


def test_worker_errors_accept_no_sensitive_transport_details() -> None:
    with pytest.raises(TypeError):
        WorkerCommunicationError("SENTINEL-TOKEN")  # type: ignore[call-arg]

    for error in (WorkerCommunicationError(), WorkerTimeoutError()):
        assert "SENTINEL-TOKEN" not in str(error)
        assert "SENTINEL-TOKEN" not in repr(error)
    assert isinstance(WorkerTimeoutError(), WorkerCommunicationError)


def test_worker_results_are_typed_and_immutable() -> None:
    state = WorkerStateObservation(WorkerLifecycleState.UNKNOWN)
    readiness = ServiceReadinessObservation(ready=True)

    with pytest.raises(TypeError, match="WorkerLifecycleState"):
        WorkerStateObservation("READY")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="boolean"):
        ServiceReadinessObservation(ready=1)  # type: ignore[arg-type]
    with pytest.raises(FrozenInstanceError):
        state.state = WorkerLifecycleState.READY  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        readiness.ready = False  # type: ignore[misc]


def test_persistence_uses_the_canonical_worker_state_contract() -> None:
    assert tuple(state.value for state in WorkerLifecycleState) == (
        "UNKNOWN",
        "WAKING",
        "READY",
        "BUSY",
        "IDLE",
        "SLEEPING",
        "UNAVAILABLE",
    )
    assert WORKER_STATES == tuple(state.value for state in WorkerLifecycleState)
