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
    WorkerUnavailableError,
)
from oasix.worker.orchestration import WorkerReadinessOrchestrator
from oasix.worker.readiness import (
    HttpServiceReadinessAdapter,
    create_http_service_readiness_adapter,
)
from oasix.worker.resolution import ActiveWorkerTarget, resolve_active_worker
from oasix.worker.wake import (
    NoWakeController,
    WakeOnLanController,
    create_worker_wake_controller,
)

__all__ = [
    "ActiveWorkerTarget",
    "ServiceId",
    "ServiceReadinessObservation",
    "ServiceReadinessProbe",
    "WorkerCommunicationError",
    "WorkerConfigurationError",
    "HttpServiceReadinessAdapter",
    "NoWakeController",
    "WorkerId",
    "WorkerIdentity",
    "WorkerInteractionError",
    "WorkerLifecycleState",
    "WorkerSleepController",
    "WorkerStateObservation",
    "WorkerStateObserver",
    "WorkerTimeoutError",
    "WorkerUnavailableError",
    "WorkerWakeController",
    "create_http_service_readiness_adapter",
    "create_worker_wake_controller",
    "resolve_active_worker",
    "WakeOnLanController",
    "WorkerReadinessOrchestrator",
]
