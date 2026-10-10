from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from io import StringIO
from typing import Any

import pytest
from pydantic import BaseModel

from oasix.logging import (
    ALLOWED_LOG_FIELDS,
    OPTIONAL_LOG_FIELDS,
    REQUIRED_LOG_FIELDS,
    EventDefinition,
    JsonLinesFormatter,
    LogCompletionStatus,
    LogErrorClass,
    LogValidationError,
    StructuredLogger,
    create_structured_logger,
)
from oasix.persistence import WorkerState

_TEST_EVENT = EventDefinition(
    code="control_plane.test_event",
    description="A safe test event occurred.",
)
_JOB_ID = "00000000-0000-4000-8000-000000000001"
_ATTEMPT_ID = "00000000-0000-4000-8000-000000000002"
_LEASE_ID = "00000000-0000-4000-8000-000000000003"
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class _SensitivePydanticModel(BaseModel):
    secret: str


def _logger_output(
    *,
    level: int = logging.INFO,
    fields: dict[str, object] | None = None,
) -> tuple[str, dict[str, Any]]:
    stream = StringIO()
    logger = create_structured_logger("control_plane", stream=stream)
    logger.emit(level, _TEST_EVENT, fields)
    serialized = stream.getvalue()
    return serialized, json.loads(serialized)


def test_emits_exactly_one_compact_json_object_per_event() -> None:
    serialized, payload = _logger_output()

    assert serialized.endswith("\n")
    assert len(serialized.splitlines()) == 1
    assert "\n" not in serialized.rstrip("\n")
    assert payload["component"] == "control_plane"
    assert payload["event_code"] == "control_plane.test_event"
    assert payload["message"] == "A safe test event occurred."


def test_emits_utc_timestamp_and_standard_log_level() -> None:
    _, payload = _logger_output(level=logging.WARNING)

    timestamp = payload["timestamp"]
    assert _TIMESTAMP_PATTERN.fullmatch(timestamp)
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert parsed.tzinfo == UTC
    assert payload["level"] == "WARNING"


def test_event_code_is_stable_across_multiple_entries() -> None:
    stream = StringIO()
    logger = create_structured_logger("control_plane", stream=stream)

    logger.emit(logging.INFO, _TEST_EVENT)
    logger.emit(logging.ERROR, _TEST_EVENT)

    payloads = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(payloads) == 2
    assert [payload["event_code"] for payload in payloads] == [
        "control_plane.test_event",
        "control_plane.test_event",
    ]
    assert [payload["level"] for payload in payloads] == ["INFO", "ERROR"]


def test_emits_only_validated_correlation_and_error_fields() -> None:
    _, payload = _logger_output(
        level=logging.ERROR,
        fields={
            "request_id": "request-0001",
            "job_id": _JOB_ID,
            "attempt_id": _ATTEMPT_ID,
            "worker_id": "worker-primary",
            "lease_id": _LEASE_ID,
            "error_class": LogErrorClass.TIMEOUT,
            "error_code": "worker.readiness_timeout",
        },
    )

    assert payload == {
        "timestamp": payload["timestamp"],
        "level": "ERROR",
        "component": "control_plane",
        "event_code": "control_plane.test_event",
        "message": "A safe test event occurred.",
        "request_id": "request-0001",
        "job_id": _JOB_ID,
        "attempt_id": _ATTEMPT_ID,
        "worker_id": "worker-primary",
        "lease_id": _LEASE_ID,
        "error_class": "TIMEOUT",
        "error_code": "worker.readiness_timeout",
    }


def test_emits_only_typed_allowlisted_operational_metrics() -> None:
    _, payload = _logger_output(
        fields={
            "request_duration_ms": 101,
            "wake_latency_ms": 81,
            "readiness_latency_ms": 99,
            "time_to_first_token_ms": 100,
            "prompt_tokens": 3,
            "completion_tokens": 5,
            "total_tokens": 8,
            "completion_status": LogCompletionStatus.SUCCEEDED,
        }
    )

    assert payload["request_duration_ms"] == 101
    assert payload["wake_latency_ms"] == 81
    assert payload["readiness_latency_ms"] == 99
    assert payload["time_to_first_token_ms"] == 100
    assert payload["prompt_tokens"] == 3
    assert payload["completion_tokens"] == 5
    assert payload["total_tokens"] == 8
    assert payload["completion_status"] == "succeeded"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("request_duration_ms", -1),
        ("wake_latency_ms", True),
        ("readiness_latency_ms", 1.5),
        ("time_to_first_token_ms", "1"),
        ("prompt_tokens", -1),
        ("completion_tokens", object()),
        ("total_tokens", 2**63),
        ("completion_status", "succeeded"),
    ],
)
def test_rejects_untyped_or_out_of_range_operational_metrics(
    field_name: str,
    value: object,
) -> None:
    stream = StringIO()
    logger = create_structured_logger("control_plane", stream=stream)

    with pytest.raises(LogValidationError) as captured:
        logger.emit(
            logging.INFO,
            _TEST_EVENT,
            {field_name: value, "request_id": "request-1"},
        )

    assert stream.getvalue() == ""
    assert field_name not in str(captured.value)
    assert field_name not in repr(captured.value)


def test_missing_optional_fields_are_omitted_instead_of_invented() -> None:
    _, payload = _logger_output(fields={"request_id": None, "worker_id": None})

    assert set(payload) == REQUIRED_LOG_FIELDS
    assert not OPTIONAL_LOG_FIELDS.intersection(payload)
    assert ALLOWED_LOG_FIELDS == REQUIRED_LOG_FIELDS | OPTIONAL_LOG_FIELDS


def test_unknown_field_is_rejected_without_disclosing_key_or_value(
    production_traceback_locals: Any,
) -> None:
    sentinel_key = "SENTINEL-SECRET-FIELD"
    sentinel_value = "SENTINEL-SECRET-VALUE"
    stream = StringIO()
    logger = create_structured_logger("control_plane", stream=stream)

    with pytest.raises(LogValidationError) as captured:
        logger.emit(logging.INFO, _TEST_EVENT, {sentinel_key: sentinel_value})

    assert stream.getvalue() == ""
    for representation in (
        str(captured.value),
        repr(captured.value),
        production_traceback_locals(captured.value),
    ):
        assert sentinel_key not in representation
        assert sentinel_value not in representation


@pytest.mark.parametrize(
    "field_name",
    [
        "password",
        "api_key",
        "authorization",
        "private_key",
        "session_token",
        "payload",
        "response",
        "result_ref",
        "execution_ref",
        "continuation_ref",
    ],
)
def test_sensitive_field_categories_are_not_in_the_allowlist(field_name: str) -> None:
    sentinel = "SENTINEL-FORBIDDEN-LOG-VALUE"
    stream = StringIO()
    logger = create_structured_logger("control_plane", stream=stream)

    with pytest.raises(LogValidationError) as captured:
        logger.emit(logging.INFO, _TEST_EVENT, {field_name: sentinel})

    assert stream.getvalue() == ""
    assert field_name not in ALLOWED_LOG_FIELDS
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


def test_credential_bearing_url_is_rejected_as_a_correlation_identifier() -> None:
    sentinel = "SENTINEL-URL-CREDENTIAL"
    stream = StringIO()
    logger = create_structured_logger("control_plane", stream=stream)

    with pytest.raises(LogValidationError) as captured:
        logger.emit(
            logging.INFO,
            _TEST_EVENT,
            {"request_id": f"https://user:{sentinel}@service.example.invalid"},
        )

    assert stream.getvalue() == ""
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


@pytest.mark.parametrize(
    "unsafe_value",
    [
        RuntimeError("SENTINEL-EXCEPTION-SECRET"),
        _SensitivePydanticModel(secret="SENTINEL-PYDANTIC-SECRET"),
        WorkerState(
            worker_id="worker-primary",
            state="READY",
            state_changed_at=0,
            updated_at=0,
            last_error_code="SENTINEL-SQLALCHEMY-SECRET",
            version=1,
        ),
    ],
)
def test_rejects_uncontrolled_objects_without_serializing_them(
    unsafe_value: object,
    production_traceback_locals: Any,
) -> None:
    stream = StringIO()
    logger = create_structured_logger("control_plane", stream=stream)

    with pytest.raises(LogValidationError) as captured:
        logger.emit(logging.ERROR, _TEST_EVENT, {"request_id": unsafe_value})

    assert stream.getvalue() == ""
    representations = (
        str(captured.value),
        repr(captured.value),
        production_traceback_locals(captured.value),
    )
    assert all("SENTINEL" not in representation for representation in representations)


def test_active_exception_context_is_not_serialized() -> None:
    sentinel = "SENTINEL-EXCEPTION-CONTEXT"
    stream = StringIO()
    logger = create_structured_logger("worker", stream=stream)

    try:
        raise RuntimeError(sentinel)
    except RuntimeError:
        logger.emit(
            logging.ERROR,
            _TEST_EVENT,
            {
                "worker_id": "worker-primary",
                "error_class": LogErrorClass.READINESS,
                "error_code": "worker.probe_failed",
            },
        )

    output = stream.getvalue()
    payload = json.loads(output)
    assert sentinel not in output
    assert "exc_info" not in payload
    assert "exception" not in payload
    assert payload["error_class"] == "READINESS"


def test_invalid_event_definition_does_not_disclose_its_values(
    production_traceback_locals: Any,
) -> None:
    sentinel_code = "SENTINEL@SECRET@CODE"
    sentinel_description = "SENTINEL-SECRET-DESCRIPTION"

    with pytest.raises(LogValidationError) as captured:
        EventDefinition(sentinel_code, sentinel_description)

    for representation in (
        str(captured.value),
        repr(captured.value),
        production_traceback_locals(captured.value),
    ):
        assert sentinel_code not in representation
        assert sentinel_description not in representation


def test_formatter_discards_unstructured_record_content_and_exception() -> None:
    sentinel = "SENTINEL-UNSTRUCTURED-LOG-SECRET"
    try:
        raise RuntimeError(sentinel)
    except RuntimeError:
        exception_info = sys.exc_info()
    record = logging.LogRecord(
        name="untrusted",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=sentinel,
        args=(),
        exc_info=exception_info,
    )

    serialized = JsonLinesFormatter().format(record)
    payload = json.loads(serialized)

    assert sentinel not in serialized
    assert payload["component"] == "logging"
    assert payload["event_code"] == "logging.invalid_record"
    assert set(payload) == REQUIRED_LOG_FIELDS


def test_invalid_level_and_identifier_fail_without_output() -> None:
    stream = StringIO()
    logger = create_structured_logger("control_plane", stream=stream)

    with pytest.raises(LogValidationError):
        logger.emit(15, _TEST_EVENT)
    with pytest.raises(LogValidationError):
        logger.emit(logging.INFO, _TEST_EVENT, {"job_id": "not-a-uuid"})
    with pytest.raises(LogValidationError):
        logger.emit(logging.INFO, _TEST_EVENT, {"error_class": "TIMEOUT"})

    assert stream.getvalue() == ""


def test_factory_does_not_change_global_logger_configuration() -> None:
    root = logging.getLogger()
    root_state = (root.level, tuple(root.handlers), tuple(root.filters), root.disabled)
    registered_loggers = dict(logging.Logger.manager.loggerDict)
    stream = StringIO()

    logger = create_structured_logger("control_plane", stream=stream)
    logger.emit(logging.INFO, _TEST_EVENT)

    assert (root.level, tuple(root.handlers), tuple(root.filters), root.disabled) == root_state
    assert dict(logging.Logger.manager.loggerDict) == registered_loggers
    assert "oasix.control_plane" not in logging.Logger.manager.loggerDict


def test_logger_construction_and_invalid_component_fail_safely(
    production_traceback_locals: Any,
) -> None:
    sentinel = "SENTINEL-PRIVATE-COMPONENT"

    for operation in (
        lambda: StructuredLogger(sentinel, logging.getLogger()),
        lambda: create_structured_logger(sentinel),
    ):
        with pytest.raises(LogValidationError) as captured:
            operation()
        for representation in (
            str(captured.value),
            repr(captured.value),
            production_traceback_locals(captured.value),
        ):
            assert sentinel not in representation
