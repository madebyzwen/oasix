"""Safe local persistence initialization for the OASIX control plane."""

from oasix.persistence.database import PersistenceDatabase, initialize_persistence
from oasix.persistence.errors import (
    PersistenceConfigurationError,
    PersistenceConnectionError,
    PersistenceError,
    PersistenceInitializationError,
    PersistenceMigrationError,
)
from oasix.persistence.models import Attempt, Base, ControlState, Job, JobEvent, Lease, WorkerState

__all__ = [
    "PersistenceConfigurationError",
    "PersistenceConnectionError",
    "PersistenceDatabase",
    "PersistenceError",
    "PersistenceInitializationError",
    "PersistenceMigrationError",
    "Attempt",
    "Base",
    "ControlState",
    "Job",
    "JobEvent",
    "Lease",
    "WorkerState",
    "initialize_persistence",
]
