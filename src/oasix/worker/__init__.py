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
    WorkerConfigurationError,
    WorkerInteractionError,
    WorkerTimeoutError,
)
from oasix.worker.readiness import (
    HttpServiceReadinessAdapter,
    create_http_service_readiness_adapter,
)
from oasix.worker.resolution import ActiveWorkerTarget, resolve_active_worker

__all__ = [
    "ActiveWorkerTarget",
    "ServiceId",
    "ServiceReadinessObservation",
    "ServiceReadinessProbe",
    "WorkerCommunicationError",
    "WorkerConfigurationError",
    "HttpServiceReadinessAdapter",
    "WorkerId",
    "WorkerIdentity",
    "WorkerInteractionError",
    "WorkerLifecycleState",
    "WorkerSleepController",
    "WorkerStateObservation",
    "WorkerStateObserver",
    "WorkerTimeoutError",
    "WorkerWakeController",
    "create_http_service_readiness_adapter",
    "resolve_active_worker",
]
