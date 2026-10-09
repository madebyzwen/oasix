"""Reusable client API authentication and authorization foundation."""

from oasix.auth.core import (
    AuthenticatedClient,
    ClientAuthenticator,
    ClientAuthorizer,
    create_client_authenticator,
)
from oasix.auth.errors import (
    ClientAuthConfigurationError,
    ClientAuthenticationError,
    ClientAuthorizationError,
)
from oasix.config.models import ClientPermission

__all__ = [
    "AuthenticatedClient",
    "ClientAuthConfigurationError",
    "ClientAuthenticationError",
    "ClientAuthenticator",
    "ClientAuthorizationError",
    "ClientAuthorizer",
    "ClientPermission",
    "create_client_authenticator",
]
