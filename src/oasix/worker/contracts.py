"""Hardware- and transport-neutral contracts for compute-worker interaction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType, Protocol, runtime_checkable

WorkerId = NewType("WorkerId", str)
ServiceId = NewType("ServiceId", str)


class WorkerLifecycleState(StrEnum):
    """The complete set of worker states defined by requirement WRK-02."""

    UNKNOWN = "UNKNOWN"
    WAKING = "WAKING"
    READY = "READY"
    BUSY = "BUSY"
    IDLE = "IDLE"
    SLEEPING = "SLEEPING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class WorkerStateObservation:
    """One successfully obtained worker-state observation."""

    state: WorkerLifecycleState

    def __post_init__(self) -> None:
        if not isinstance(self.state, WorkerLifecycleState):
            raise TypeError("state must be a WorkerLifecycleState")


@dataclass(frozen=True, slots=True)
class ServiceReadinessObservation:
    """One successfully obtained readiness result for a configured service."""

    ready: bool

    def __post_init__(self) -> None:
        if type(self.ready) is not bool:
            raise TypeError("ready must be a boolean")


@runtime_checkable
class WorkerIdentity(Protocol):
    """Common identity exposed by every adapter bound to one worker profile."""

    @property
    def worker_id(self) -> WorkerId:
        """Return the generic configured worker identifier."""

        ...


@runtime_checkable
class WorkerStateObserver(WorkerIdentity, Protocol):
    """Observe a worker state without exposing transport details."""

    async def observe_state(self) -> WorkerStateObservation:
        """Return a confirmed observation or raise a worker interaction error."""

        ...


@runtime_checkable
class ServiceReadinessProbe(WorkerIdentity, Protocol):
    """Check readiness for one configured service on the bound worker."""

    async def check_readiness(self, service_id: ServiceId) -> ServiceReadinessObservation:
        """Return a confirmed service result or raise a worker interaction error."""

        ...


@runtime_checkable
class WorkerWakeController(WorkerIdentity, Protocol):
    """Initiate the configured wake mechanism without implying readiness."""

    async def wake(self) -> None:
        """Complete command hand-off or raise a worker interaction error."""

        ...


@runtime_checkable
class WorkerSleepController(WorkerIdentity, Protocol):
    """Initiate the configured sleep mechanism after external policy checks."""

    async def sleep(self) -> None:
        """Complete command hand-off or raise a worker interaction error."""

        ...
