"""Safe error categories for the interactive LLM path."""


class LlmPathError(RuntimeError):
    """Base class for controlled, non-revealing LLM-path failures."""

    _safe_message = "LLM-Anfrage konnte nicht sicher verarbeitet werden."

    def __init__(self) -> None:
        super().__init__(self._safe_message)


class LlmConfigurationError(LlmPathError):
    """Runtime configuration cannot provide one safe LLM path."""

    _safe_message = "LLM-Pfad ist nicht sicher konfiguriert."


class LlmRequestError(LlmPathError):
    """The client request is outside the supported bounded subset."""

    _safe_message = "LLM-Anfrage ist ungültig oder wird nicht unterstützt."


class LlmRequestTooLargeError(LlmRequestError):
    """The client request exceeded the bounded input size."""

    _safe_message = "LLM-Anfrage überschreitet die zulässige Größe."


class InferenceOverloadedError(LlmPathError):
    """All configured inference admission slots are occupied."""

    _safe_message = "LLM-Pfad ist derzeit ausgelastet."


class InferenceLeaseAcquireError(LlmPathError):
    """The persistent usage lease could not be acquired."""

    _safe_message = "LLM-Nutzungslease konnte nicht erworben werden."


class InferenceLeaseRenewalError(LlmPathError):
    """The persistent usage lease could not be kept valid."""

    _safe_message = "LLM-Nutzungslease konnte nicht verlängert werden."


class InferenceLeaseReleaseError(LlmPathError):
    """The persistent usage lease could not be confirmed as released."""

    _safe_message = "LLM-Nutzungslease konnte nicht freigegeben werden."


class LlmReadinessError(LlmPathError):
    """The configured worker service did not become ready."""

    _safe_message = "LLM-Dienst ist nicht bereit."


class LlmUpstreamError(LlmPathError):
    """The configured upstream returned no safe successful result."""

    _safe_message = "LLM-Upstream-Kommunikation ist fehlgeschlagen."


class LlmUpstreamTimeoutError(LlmUpstreamError):
    """The configured upstream exceeded its request timeout."""

    _safe_message = "LLM-Upstream hat das zulässige Zeitlimit überschritten."


class LlmUpstreamProtocolError(LlmUpstreamError):
    """The configured upstream returned an unsupported or oversized response."""

    _safe_message = "LLM-Upstream-Antwort ist ungültig oder wird nicht unterstützt."
