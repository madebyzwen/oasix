"""Structured JSON logging with a closed, non-secret event boundary."""

from __future__ import annotations

import json
import logging as stdlib_logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TextIO

_LOG_RECORD_ATTRIBUTE = "_oasix_structured_event"
_STRUCTURED_LOGGER_CREATION_TOKEN = object()
_GENERIC_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_TECHNICAL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_EVENT_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_STANDARD_LEVELS = frozenset(
    {
        stdlib_logging.DEBUG,
        stdlib_logging.INFO,
        stdlib_logging.WARNING,
        stdlib_logging.ERROR,
        stdlib_logging.CRITICAL,
    }
)
_OPTIONAL_FIELD_ORDER = (
    "request_id",
    "job_id",
    "attempt_id",
    "worker_id",
    "lease_id",
    "error_class",
    "error_code",
    "request_duration_ms",
    "wake_latency_ms",
    "readiness_latency_ms",
    "time_to_first_token_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "completion_status",
)

REQUIRED_LOG_FIELDS = frozenset({"timestamp", "level", "component", "event_code", "message"})
OPTIONAL_LOG_FIELDS = frozenset(_OPTIONAL_FIELD_ORDER)
ALLOWED_LOG_FIELDS = REQUIRED_LOG_FIELDS | OPTIONAL_LOG_FIELDS

_MAX_COMPONENT_LENGTH = 63
_MAX_EVENT_CODE_LENGTH = 127
_MAX_DESCRIPTION_BYTES = 512
_MAX_REQUEST_ID_LENGTH = 128
_MAX_ERROR_CODE_LENGTH = 128
_MAX_METRIC_VALUE = (2**63) - 1


class LogValidationError(ValueError):
    """A log event violated the closed safe-logging contract."""

    def __init__(self) -> None:
        super().__init__("Strukturiertes Logereignis wurde sicher abgewiesen.")


class LogErrorClass(StrEnum):
    """Stable error classes explicitly required by REC-04."""

    CONFIG = "CONFIG"
    WAKE = "WAKE"
    READINESS = "READINESS"
    LLM = "LLM"
    AGENT = "AGENT"
    TOOL = "TOOL"
    TIMEOUT = "TIMEOUT"


class LogCompletionStatus(StrEnum):
    """Closed completion outcomes for operational request telemetry."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True, repr=False, init=False)
class EventDefinition:
    """A code-defined event code and non-sensitive static description."""

    code: str
    description: str

    def __init__(self, code: str, description: str) -> None:
        valid_code = (
            type(code) is str
            and len(code) <= _MAX_EVENT_CODE_LENGTH
            and _EVENT_CODE_PATTERN.fullmatch(code) is not None
        )
        valid_description = (
            type(description) is str
            and bool(description)
            and description == description.strip()
            and description.isprintable()
        )
        description_size = 0
        if valid_description:
            try:
                description_size = len(description.encode("utf-8"))
            except UnicodeEncodeError:
                valid_description = False
        if not valid_code or not valid_description or description_size > _MAX_DESCRIPTION_BYTES:
            code = "<invalid>"
            description = "<invalid>"
            description_size = 0
            valid_code = False
            valid_description = False
            object.__setattr__(self, "code", code)
            object.__setattr__(self, "description", description)
            raise LogValidationError from None
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "description", description)

    def __repr__(self) -> str:
        return "EventDefinition(<safe-static-definition>)"


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedEvent:
    level: int
    component: str
    code: str
    description: str
    fields: tuple[tuple[str, str | int], ...]


class JsonLinesFormatter(stdlib_logging.Formatter):
    """Render one allowlisted JSON object per logging record."""

    def format(self, record: stdlib_logging.LogRecord) -> str:
        prepared = getattr(record, _LOG_RECORD_ATTRIBUTE, None)
        if type(prepared) is not _PreparedEvent:
            payload: dict[str, str | int] = {
                "timestamp": _utc_timestamp(record.created),
                "level": "ERROR",
                "component": "logging",
                "event_code": "logging.invalid_record",
                "message": "Unzulässiger Logeintrag wurde sicher verworfen.",
            }
        else:
            payload = {
                "timestamp": _utc_timestamp(record.created),
                "level": stdlib_logging.getLevelName(prepared.level),
                "component": prepared.component,
                "event_code": prepared.code,
                "message": prepared.description,
            }
            payload.update(prepared.fields)
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )


class StructuredLogger:
    """Emit only validated event definitions and allowlisted scalar context."""

    __slots__ = ("_component", "_logger")

    def __init__(
        self,
        component: str,
        logger: stdlib_logging.Logger,
        *,
        _creation_token: object | None = None,
    ) -> None:
        if _creation_token is not _STRUCTURED_LOGGER_CREATION_TOKEN:
            component = "<invalid>"
            logger = stdlib_logging.Logger("oasix.invalid", level=stdlib_logging.NOTSET)
            _creation_token = None
            self._component = component
            self._logger = logger
            raise LogValidationError from None
        self._component = component
        self._logger = logger

    def __repr__(self) -> str:
        return f"StructuredLogger(component={self._component!r})"

    @property
    def component(self) -> str:
        """Return the validated generic component identifier."""

        return self._component

    def emit(
        self,
        level: int,
        event: EventDefinition,
        fields: Mapping[str, object] | None = None,
    ) -> None:
        """Validate and synchronously emit one structured event."""

        validation_failed = False
        prepared: _PreparedEvent | None = None
        try:
            prepared = _prepare_event(self._component, level, event, fields)
        except LogValidationError:
            validation_failed = True
        if validation_failed:
            level = stdlib_logging.NOTSET
            event = None  # type: ignore[assignment]
            fields = None
            prepared = None
            raise LogValidationError from None
        if prepared is None:  # pragma: no cover - defensive invariant
            raise AssertionError("logging validation produced no result")
        self._logger.log(
            prepared.level,
            prepared.description,
            extra={_LOG_RECORD_ATTRIBUTE: prepared},
        )


def create_structured_logger(
    component: str,
    *,
    stream: TextIO | None = None,
    level: int = stdlib_logging.INFO,
) -> StructuredLogger:
    """Create an isolated JSON-lines logger without changing global configuration."""

    validation_failed = False
    try:
        _validate_component(component)
        _validate_level(level)
    except LogValidationError:
        validation_failed = True
    if validation_failed:
        component = "<invalid>"
        stream = None
        level = stdlib_logging.NOTSET
        raise LogValidationError from None

    handler = stdlib_logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(JsonLinesFormatter())
    logger = stdlib_logging.Logger(f"oasix.{component}", level=level)
    logger.propagate = False
    logger.addHandler(handler)
    return StructuredLogger(
        component,
        logger,
        _creation_token=_STRUCTURED_LOGGER_CREATION_TOKEN,
    )


def _prepare_event(
    component: str,
    level: object,
    event: object,
    fields: Mapping[str, object] | None,
) -> _PreparedEvent:
    _validate_level(level)
    if type(event) is not EventDefinition:
        raise LogValidationError from None
    prepared_fields = _prepare_fields(fields)
    return _PreparedEvent(
        level=level,
        component=component,
        code=event.code,
        description=event.description,
        fields=prepared_fields,
    )


def _prepare_fields(fields: Mapping[str, object] | None) -> tuple[tuple[str, str | int], ...]:
    if fields is None:
        return ()
    if type(fields) is not dict:
        raise LogValidationError from None
    if any(type(name) is not str or name not in OPTIONAL_LOG_FIELDS for name in fields):
        raise LogValidationError from None

    prepared: dict[str, str | int] = {}
    for name in _OPTIONAL_FIELD_ORDER:
        value = fields.get(name)
        if value is None:
            continue
        prepared[name] = _validate_field(name, value)
    return tuple(prepared.items())


def _validate_field(name: str, value: object) -> str | int:
    if name == "error_class":
        if type(value) is not LogErrorClass:
            raise LogValidationError from None
        return value.value
    if name == "completion_status":
        if type(value) is not LogCompletionStatus:
            raise LogValidationError from None
        return value.value
    if name in {
        "request_duration_ms",
        "wake_latency_ms",
        "readiness_latency_ms",
        "time_to_first_token_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }:
        if type(value) is not int or not 0 <= value <= _MAX_METRIC_VALUE:
            raise LogValidationError from None
        return value
    if type(value) is not str:
        raise LogValidationError from None
    if name in {"job_id", "attempt_id", "lease_id"}:
        _validate_uuid4(value)
    elif name == "worker_id":
        if (
            len(value) > _MAX_COMPONENT_LENGTH
            or _GENERIC_IDENTIFIER_PATTERN.fullmatch(value) is None
        ):
            raise LogValidationError from None
    elif name == "request_id":
        if (
            len(value) > _MAX_REQUEST_ID_LENGTH
            or _TECHNICAL_IDENTIFIER_PATTERN.fullmatch(value) is None
        ):
            raise LogValidationError from None
    elif name == "error_code":
        if len(value) > _MAX_ERROR_CODE_LENGTH or _EVENT_CODE_PATTERN.fullmatch(value) is None:
            raise LogValidationError from None
    else:  # pragma: no cover - guarded by the central allowlist
        raise LogValidationError from None
    return value


def _validate_component(component: object) -> None:
    if (
        type(component) is not str
        or len(component) > _MAX_COMPONENT_LENGTH
        or _GENERIC_IDENTIFIER_PATTERN.fullmatch(component) is None
    ):
        raise LogValidationError from None


def _validate_level(level: object) -> None:
    if type(level) is not int or level not in _STANDARD_LEVELS:
        raise LogValidationError from None


def _validate_uuid4(value: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise LogValidationError from None
    if parsed.version != 4 or str(parsed) != value:
        raise LogValidationError from None


def _utc_timestamp(created: float) -> str:
    timestamp = datetime.fromtimestamp(created, tz=UTC).isoformat(timespec="milliseconds")
    return timestamp.replace("+00:00", "Z")
