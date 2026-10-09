"""Safe client-authentication errors with deliberately generic messages."""

from oasix.config.errors import StartupConfigurationError


class ClientAuthConfigurationError(StartupConfigurationError):
    """Client authentication could not be initialized safely."""

    def __init__(self) -> None:
        super().__init__("Client-Authentifizierung konnte nicht initialisiert werden.")


class ClientAuthenticationError(RuntimeError):
    """A request did not present one unambiguous configured credential."""

    def __init__(self) -> None:
        super().__init__("Client-Authentifizierung fehlgeschlagen.")


class ClientAuthorizationError(RuntimeError):
    """An authenticated client lacks an explicitly required permission."""

    def __init__(self) -> None:
        super().__init__("Client-Autorisierung fehlgeschlagen.")
