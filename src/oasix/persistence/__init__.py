"""Safe local persistence initialization for the OASIX control plane."""

from oasix.persistence.database import PersistenceDatabase, initialize_persistence
from oasix.persistence.errors import (
    PersistenceConfigurationError,
    PersistenceConnectionError,
    PersistenceError,
    PersistenceInitializationError,
)

__all__ = [
    "PersistenceConfigurationError",
    "PersistenceConnectionError",
    "PersistenceDatabase",
    "PersistenceError",
    "PersistenceInitializationError",
    "initialize_persistence",
]
