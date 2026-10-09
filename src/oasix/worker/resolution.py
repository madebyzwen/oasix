"""Resolve generic worker identities from the validated runtime configuration."""

from dataclasses import dataclass, field

from oasix.config.models import RuntimeConfig, WorkerProfile
from oasix.worker.contracts import WorkerId


@dataclass(frozen=True, slots=True, repr=False)
class ActiveWorkerTarget:
    """The active configured worker, with connection details hidden from repr."""

    worker_id: WorkerId
    profile: WorkerProfile = field(repr=False)

    def __repr__(self) -> str:
        return f"ActiveWorkerTarget(worker_id={self.worker_id!r}, profile=<validated-profile>)"


def resolve_active_worker(runtime: RuntimeConfig) -> ActiveWorkerTarget:
    """Resolve the active worker solely from an already validated runtime model."""

    return ActiveWorkerTarget(
        worker_id=WorkerId(runtime.active_worker),
        profile=runtime.active_worker_profile,
    )
