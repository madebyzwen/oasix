"""Safe startup errors which never retain raw configuration or secret values."""


class StartupConfigurationError(RuntimeError):
    """Base class for fatal configuration errors during startup."""


class BootstrapConfigurationError(StartupConfigurationError):
    """The locations of required configuration sources are invalid."""


class RuntimeConfigurationError(StartupConfigurationError):
    """The external runtime configuration is invalid."""


class SecretResolutionError(StartupConfigurationError):
    """A referenced secret could not be resolved safely."""
