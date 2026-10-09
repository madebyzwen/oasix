"""Safe local persistence initialization for the OASIX control plane."""

from oasix.persistence.database import PersistenceDatabase, initialize_persistence
from oasix.persistence.errors import (
    PersistenceConfigurationError,
    PersistenceConnectionError,
    PersistenceError,
    PersistenceInitializationError,
    PersistenceIntegrityError,
    PersistenceLockingError,
    PersistenceMigrationError,
    PersistenceOperationError,
    PersistenceValidationError,
)
from oasix.persistence.migrations import EXPECTED_SCHEMA_REVISION, verify_schema_revision
from oasix.persistence.models import Attempt, Base, ControlState, Job, JobEvent, Lease, WorkerState
from oasix.persistence.repositories import PersistenceRepositories
from oasix.persistence.validation import (
    NonSecretReferenceAdapter,
    PayloadSchemaRegistry,
    ReferenceContracts,
    RepositoryValidation,
)

__all__ = [
    "PersistenceConfigurationError",
    "PersistenceConnectionError",
    "PersistenceDatabase",
    "PersistenceError",
    "PersistenceInitializationError",
    "PersistenceIntegrityError",
    "PersistenceLockingError",
    "PersistenceMigrationError",
    "PersistenceOperationError",
    "PersistenceRepositories",
    "PersistenceValidationError",
    "EXPECTED_SCHEMA_REVISION",
    "NonSecretReferenceAdapter",
    "PayloadSchemaRegistry",
    "ReferenceContracts",
    "RepositoryValidation",
    "Attempt",
    "Base",
    "ControlState",
    "Job",
    "JobEvent",
    "Lease",
    "WorkerState",
    "initialize_persistence",
    "verify_schema_revision",
]
