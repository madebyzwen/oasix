from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from oasix.persistence import (
    PayloadSchemaRegistry,
    ReferenceContracts,
    RepositoryValidation,
)
from oasix.persistence.errors import (
    PersistenceConfigurationError,
    PersistenceValidationError,
)
from oasix.persistence.validation import (
    CONTINUATION_REF_MAX_BYTES,
    ERROR_DETAIL_MAX_BYTES,
    EVENT_METADATA_MAX_BYTES,
    EXECUTION_REF_MAX_BYTES,
    EXTRA_METRICS_MAX_BYTES,
    INPUT_PAYLOAD_MAX_BYTES,
    RESULT_REF_MAX_BYTES,
)

JOB_ID = "00000000-0000-4000-8000-000000000001"
ATTEMPT_ID = "10000000-0000-4000-8000-000000000001"


class _JobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: str


class _EventMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    detail: str


class _EmptyEventMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _MetricsLevel8(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: int


class _MetricsLevel7(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nested: _MetricsLevel8


class _MetricsLevel6(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nested: _MetricsLevel7


class _MetricsLevel5(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nested: _MetricsLevel6


class _MetricsLevel4(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nested: _MetricsLevel5


class _MetricsLevel3(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nested: _MetricsLevel4


class _MetricsLevel2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nested: _MetricsLevel3


class _MetricsLevel1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nested: _MetricsLevel2


class _TooDeepMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nested: _MetricsLevel1


class _LargeMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: str


class _OpenSchema(BaseModel):
    value: str


class _ClosedWithOpenChild(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    child: _OpenSchema


class _UncheckedSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: Any


class _OpaqueReferenceAdapter:
    def validate_non_secret_reference(self, value: str, /) -> str:
        if not value.startswith("ref-"):
            raise ValueError
        return value


class _DeclaredReferenceAdapter:
    def validate_non_secret_reference(self, value: str, /) -> str:
        return value


def _validation(
    *,
    extra_metrics_schema: type[BaseModel] | None = None,
    references: ReferenceContracts | None = None,
) -> RepositoryValidation:
    return RepositoryValidation(
        schemas=PayloadSchemaRegistry(
            job_schemas={"test-job": _JobPayload},
            event_schemas={"test-event": _EventMetadata},
        ),
        references=references,
        extra_metrics_schema=extra_metrics_schema,
    )


def _job(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "job_id": JOB_ID,
        "job_type": "test-job",
        "role": None,
        "status": "QUEUED",
        "parent_job_id": None,
        "idempotency_scope": None,
        "idempotency_key_digest": None,
        "request_fingerprint": None,
        "request_id": None,
        "input_payload": {"value": "valid"},
        "result_ref": None,
        "wait_kind": None,
        "continuation_ref": None,
        "resumability_confirmed_at": None,
        "next_eligible_at": 10,
        "created_at": 10,
        "queued_at": 10,
        "updated_at": 10,
        "finished_at": None,
        "version": 1,
    }
    data.update(changes)
    return data


def _attempt(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "attempt_id": ATTEMPT_ID,
        "job_id": JOB_ID,
        "attempt_number": 1,
        "worker_id": "worker-primary",
        "status": "SUCCEEDED",
        "execution_ref": None,
        "agent_role": None,
        "model": None,
        "created_at": 10,
        "updated_at": 12,
        "started_at": 10,
        "first_token_at": None,
        "finished_at": 12,
        "queue_duration_ms": None,
        "wake_duration_ms": None,
        "execution_duration_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "tool_calls_count": None,
        "tool_duration_ms": None,
        "error_class": None,
        "error_code": None,
        "error_detail_redacted": None,
        "extra_metrics": None,
        "version": 1,
    }
    data.update(changes)
    return data


def _event(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "job_id": JOB_ID,
        "attempt_id": None,
        "event_type": "test-event",
        "from_status": None,
        "to_status": None,
        "error_class": None,
        "error_code": None,
        "metadata": {"detail": "valid"},
        "occurred_at": 10,
    }
    data.update(changes)
    return data


def _utf8_value_for_canonical_object_size(limit: int, key: str) -> str:
    overhead = len(f'{{"{key}":""}}'.encode())
    return "é" * ((limit - overhead) // 2) + ("a" if (limit - overhead) % 2 else "")


def _utf8_value_with_prefix(size: int, prefix: str = "") -> str:
    remaining = size - len(prefix.encode())
    return prefix + "é" * (remaining // 2) + ("a" if remaining % 2 else "")


@pytest.mark.parametrize(
    "job_id",
    [
        "00000000-0000-1000-8000-000000000001",
        "00000000-0000-4000-8000-00000000001",
        "00000000-0000-4000-8000-00000000000A",
    ],
)
def test_rejects_noncanonical_or_non_v4_uuids(job_id: str) -> None:
    with pytest.raises(PersistenceValidationError, match="canonical_uuid4"):
        _validation().job(_job(job_id=job_id))


@pytest.mark.parametrize(
    ("changes", "field_name"),
    [
        ({"status": "queued", "next_eligible_at": None}, "status"),
        ({"job_type": "Private Host"}, "job_type"),
        ({"created_at": -1}, "created_at"),
        ({"unexpected": "value"}, "input"),
    ],
)
def test_rejects_invalid_status_identifiers_timestamps_and_unknown_fields(
    changes: dict[str, Any], field_name: str
) -> None:
    with pytest.raises(PersistenceValidationError) as captured:
        _validation().job(_job(**changes))
    assert field_name in str(captured.value)


def test_requires_json_objects_and_registered_types() -> None:
    with pytest.raises(PersistenceValidationError, match="input_payload"):
        _validation().job(_job(input_payload=["not-an-object"]))
    with pytest.raises(PersistenceValidationError, match="registered_schema"):
        RepositoryValidation().job(_job(job_type="unknown-job"))
    with pytest.raises(PersistenceValidationError, match="registered_schema"):
        RepositoryValidation().job_event(_event(event_type="unknown-event"))
    with pytest.raises(PersistenceValidationError, match="registered_schema"):
        _validation().job_event(_event(metadata=None))

    empty_metadata = RepositoryValidation(
        schemas=PayloadSchemaRegistry(event_schemas={"test-event": _EmptyEventMetadata})
    ).job_event(_event(metadata=None))
    assert empty_metadata["metadata_json"] is None


@pytest.mark.parametrize(
    ("operation", "field_name"),
    [
        (lambda value: _validation().job(_job(input_payload=value)), "input_payload"),
        (lambda value: _validation().job_event(_event(metadata=value)), "metadata"),
    ],
)
def test_unknown_schema_fields_are_rejected_without_disclosure(
    operation: Any,
    field_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "SENTINEL-PRIVATE-SCHEMA-KEY"
    with pytest.raises(PersistenceValidationError) as captured:
        operation({sentinel: "SENTINEL-PRIVATE-SCHEMA-VALUE"})
    representations = (str(captured.value), repr(captured.value), caplog.text)
    assert field_name in str(captured.value)
    assert all(sentinel not in representation for representation in representations)
    assert captured.value.__context__ is None


@pytest.mark.parametrize("schema", [_OpenSchema, _ClosedWithOpenChild, _UncheckedSchema])
def test_requires_closed_registered_schemas(schema: type[BaseModel]) -> None:
    with pytest.raises(PersistenceConfigurationError):
        PayloadSchemaRegistry(job_schemas={"test-job": schema})


@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize(
    ("kind", "limit"),
    [
        ("input", INPUT_PAYLOAD_MAX_BYTES),
        ("event", EVENT_METADATA_MAX_BYTES),
        ("metrics", EXTRA_METRICS_MAX_BYTES),
    ],
)
def test_json_utf8_limits(kind: str, limit: int, delta: int) -> None:
    key = "detail" if kind == "event" else "value"
    value = _utf8_value_for_canonical_object_size(limit + delta, key)
    if kind == "input":

        def operation() -> None:
            _validation().job(_job(input_payload={"value": value}))

    elif kind == "event":

        def operation() -> None:
            _validation().job_event(_event(metadata={"detail": value}))

    else:

        def operation() -> None:
            _validation(extra_metrics_schema=_LargeMetrics).attempt(
                _attempt(extra_metrics={"value": value})
            )

    if delta <= 0:
        operation()
    else:
        with pytest.raises(PersistenceValidationError, match="utf8_byte_limit"):
            operation()


def test_extra_metrics_enforces_maximum_nesting() -> None:
    level8: dict[str, Any] = {"value": 1}
    level7 = {"nested": level8}
    level6 = {"nested": level7}
    level5 = {"nested": level6}
    level4 = {"nested": level5}
    level3 = {"nested": level4}
    level2 = {"nested": level3}
    level1 = {"nested": level2}
    _validation(extra_metrics_schema=_MetricsLevel1).attempt(_attempt(extra_metrics=level1))

    with pytest.raises(PersistenceValidationError, match="maximum_depth"):
        _validation(extra_metrics_schema=_TooDeepMetrics).attempt(
            _attempt(extra_metrics={"nested": level1})
        )


@pytest.mark.parametrize(
    ("field_name", "limit"),
    [
        ("result_ref", RESULT_REF_MAX_BYTES),
        ("execution_ref", EXECUTION_REF_MAX_BYTES),
        ("continuation_ref", CONTINUATION_REF_MAX_BYTES),
        ("error_detail_redacted", ERROR_DETAIL_MAX_BYTES),
    ],
)
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_string_utf8_limits(field_name: str, limit: int, delta: int) -> None:
    value = _utf8_value_with_prefix(limit + delta)
    adapter = _OpaqueReferenceAdapter()
    references = ReferenceContracts(result=adapter, execution=adapter, continuation=adapter)
    if field_name == "error_detail_redacted":

        def operation() -> None:
            _validation().attempt(_attempt(**{field_name: value}))

    elif field_name == "execution_ref":
        value = _utf8_value_with_prefix(limit + delta, "ref-")

        def operation() -> None:
            _validation(references=references).attempt(_attempt(**{field_name: value}))

    else:
        value = _utf8_value_with_prefix(limit + delta, "ref-")
        changes: dict[str, Any] = {field_name: value}
        if field_name == "continuation_ref":
            changes.update(
                status="WAITING",
                wait_kind="EXTERNAL",
                next_eligible_at=None,
                resumability_confirmed_at=10,
            )

        def operation() -> None:
            _validation(references=references).job(_job(**changes))

    if delta <= 0:
        operation()
    else:
        with pytest.raises(PersistenceValidationError, match="utf8_byte_limit"):
            operation()


def test_references_fail_closed_and_reject_authenticating_urls() -> None:
    with pytest.raises(PersistenceValidationError, match="reference_contract"):
        _validation().attempt(_attempt(execution_ref="ref-runtime"))

    adapter = _DeclaredReferenceAdapter()
    references = ReferenceContracts(result=adapter, execution=adapter, continuation=adapter)
    _validation(references=references).attempt(_attempt(execution_ref="ref-runtime"))
    for field_name, value in (
        ("result_ref", "https://user:password@example.invalid/result"),
        ("result_ref", "https://example.invalid/result?token=value"),
        ("execution_ref", "https://example.invalid/execution"),
    ):
        with pytest.raises(PersistenceValidationError):
            if field_name == "execution_ref":
                _validation(references=references).attempt(_attempt(**{field_name: value}))
            else:
                _validation(references=references).job(_job(**{field_name: value}))

    with pytest.raises(PersistenceValidationError, match="reference_format"):
        _validation(references=references).job(_job(result_ref="http://[invalid"))


@pytest.mark.parametrize("field", ["payload", "reference"])
def test_rejects_non_utf8_encodable_text_safely(field: str) -> None:
    invalid_text = "\ud800"
    with pytest.raises(PersistenceValidationError, match="utf8"):
        if field == "payload":
            _validation().job(_job(input_payload={"value": invalid_text}))
        else:
            adapter = _DeclaredReferenceAdapter()
            _validation(references=ReferenceContracts(result=adapter)).job(
                _job(result_ref=invalid_text)
            )


def test_validation_errors_do_not_retain_sentinel_values(
    caplog: pytest.LogCaptureFixture,
    production_traceback_locals: Any,
) -> None:
    sentinel = "SENTINEL-PRIVATE-PERSISTENCE-SECRET"
    invalid = deepcopy(_job())
    invalid["input_payload"] = {"value": sentinel, "unexpected": sentinel}
    with pytest.raises(PersistenceValidationError) as captured:
        _validation().job(invalid)
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in caplog.text
    assert captured.value.__context__ is None
    assert sentinel not in production_traceback_locals(captured.value)
