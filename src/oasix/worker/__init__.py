"""Generic compute-worker contracts for the OASIX control plane."""

from oasix.worker.contracts import (
    ServiceId,
    ServiceReadinessObservation,
    ServiceReadinessProbe,
    WorkerId,
    WorkerIdentity,
    WorkerLifecycleState,
    WorkerSleepController,
    WorkerStateObservation,
    WorkerStateObserver,
    WorkerWakeController,
)
from oasix.worker.errors import (
    WorkerCommunicationError,
    WorkerInteractionError,
    WorkerTimeoutError,
)
from oasix.worker.resolution import ActiveWorkerTarget, resolve_active_worker

__all__ = [
    "ActiveWorkerTarget",
    "ServiceId",
    "ServiceReadinessObservation",
    "ServiceReadinessProbe",
    "WorkerCommunicationError",
    "WorkerId",
    "WorkerIdentity",
    "WorkerInteractionError",
    "WorkerLifecycleState",
    "WorkerSleepController",
    "WorkerStateObservation",
    "WorkerStateObserver",
    "WorkerTimeoutError",
    "WorkerWakeController",
    "resolve_active_worker",
]
