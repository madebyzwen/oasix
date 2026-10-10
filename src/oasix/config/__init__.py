"""Validated configuration loading for the OASIX control plane."""

from oasix.config.bootstrap import BootstrapSettings
from oasix.config.errors import (
    BootstrapConfigurationError,
    RuntimeConfigurationError,
    SecretResolutionError,
    StartupConfigurationError,
)
from oasix.config.loader import LoadedConfiguration, load_startup_configuration
from oasix.config.models import (
    ClientAuthenticationSettings,
    ClientIdentitySettings,
    ClientPermission,
    InferencePolicy,
    PersistenceSettings,
    RuntimeConfig,
)
from oasix.config.secrets import ResolvedSecrets, SecretSource

__all__ = [
    "BootstrapConfigurationError",
    "BootstrapSettings",
    "ClientAuthenticationSettings",
    "ClientIdentitySettings",
    "ClientPermission",
    "InferencePolicy",
    "LoadedConfiguration",
    "PersistenceSettings",
    "ResolvedSecrets",
    "RuntimeConfig",
    "RuntimeConfigurationError",
    "SecretResolutionError",
    "SecretSource",
    "StartupConfigurationError",
    "load_startup_configuration",
]
