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
