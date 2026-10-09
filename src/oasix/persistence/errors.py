"""Safe persistence errors which do not reveal configured paths or SQL values."""


class PersistenceError(RuntimeError):
    """Base class for controlled persistence failures."""


class PersistenceConfigurationError(PersistenceError):
    """The persistence configuration or storage path is unsafe."""


class PersistenceConnectionError(PersistenceError):
    """A SQLite connection could not be established safely."""


class PersistenceInitializationError(PersistenceError):
    """Required SQLite settings could not be initialized or verified."""


class PersistenceMigrationError(PersistenceError):
    """A schema migration or its integrity verification failed safely."""
