"""Transport-neutral worker errors with intentionally fixed, safe messages."""


class WorkerInteractionError(RuntimeError):
    """Base class for controlled failures at a worker interaction boundary."""

    _safe_message = "Worker-Interaktion ist fehlgeschlagen."

    def __init__(self) -> None:
        super().__init__(self._safe_message)


class WorkerCommunicationError(WorkerInteractionError):
    """A worker operation could not obtain a reliable transport result."""

    _safe_message = "Worker-Kommunikation ist fehlgeschlagen."


class WorkerTimeoutError(WorkerCommunicationError):
    """A worker operation exceeded its externally enforced time limit."""

    _safe_message = "Worker-Interaktion hat das zulässige Zeitlimit überschritten."


class WorkerConfigurationError(WorkerInteractionError):
    """Validated runtime data does not permit the requested worker operation."""

    _safe_message = "Worker-Konfiguration erlaubt diese Interaktion nicht."


class WorkerUnavailableError(WorkerInteractionError):
    """The configured worker did not become ready within the bounded policy."""

    _safe_message = "Worker wurde innerhalb der zulässigen Versuche nicht bereit."
