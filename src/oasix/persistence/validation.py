"""Typed, fail-closed validation for values written through repositories."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import wraps
from types import MappingProxyType
from typing import Any, NoReturn, Protocol, Self, get_args, get_origin
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBytes,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError

from oasix.persistence.errors import PersistenceConfigurationError, PersistenceValidationError
from oasix.persistence.models import (
    ATTEMPT_STATUSES,
    DISPATCH_MODES,
    JOB_STATUSES,
    RECOVERY_STATUSES,
    WORKER_STATES,
)

INPUT_PAYLOAD_MAX_BYTES = 1_048_576
RESULT_REF_MAX_BYTES = 2_048
EXECUTION_REF_MAX_BYTES = 2_048
CONTINUATION_REF_MAX_BYTES = 4_096
ERROR_DETAIL_MAX_BYTES = 8_192
EXTRA_METRICS_MAX_BYTES = 65_536
EVENT_METADATA_MAX_BYTES = 16_384
EXTRA_METRICS_MAX_DEPTH = 8

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_TECHNICAL_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_KNOWN_FIELDS = frozenset(
    {
        "agent_role",
        "attempt_id",
        "attempt_number",
        "continuation_ref",
        "created_at",
        "dispatch_mode",
        "error_class",
        "error_code",
        "error_detail_redacted",
        "event_type",
        "execution_duration_ms",
        "execution_ref",
        "expires_at",
        "extra_metrics",
        "finished_at",
        "first_token_at",
        "from_status",
        "idempotency_key_digest",
        "idempotency_scope",
        "idle_since",
        "input_payload",
        "input_tokens",
        "job_id",
        "job_type",
        "last_clean_shutdown_at",
        "last_error_class",
        "last_error_code",
        "last_heartbeat_at",
        "last_ready_at",
        "metadata",
        "model",
        "next_eligible_at",
        "observed_at",
        "occurred_at",
        "output_tokens",
        "owner",
        "parent_job_id",
        "process_instance_id",
        "purpose",
        "queue_duration_ms",
        "queued_at",
        "recovery_status",
        "release_reason",
        "released_at",
        "request_fingerprint",
        "request_id",
        "result_ref",
        "resumability_confirmed_at",
        "singleton_id",
        "started_at",
        "state",
        "state_changed_at",
        "status",
        "to_status",
        "tool_calls_count",
        "tool_duration_ms",
        "total_tokens",
        "updated_at",
        "version",
        "wake_duration_ms",
        "wait_kind",
        "worker_id",
    }
)


class NonSecretReferenceAdapter(Protocol):
    """Adapter-owned contract for one explicitly non-secret reference field."""

    def validate_non_secret_reference(self, value: str, /) -> str:
        """Return the unchanged reference after adapter-specific validation."""


@dataclass(frozen=True, slots=True)
class ReferenceContracts:
    """Field-specific adapters; absent contracts reject non-null references."""

    result: NonSecretReferenceAdapter | None = field(default=None, repr=False)
    execution: NonSecretReferenceAdapter | None = field(default=None, repr=False)
    continuation: NonSecretReferenceAdapter | None = field(default=None, repr=False)


class PayloadSchemaRegistry:
    """Immutable explicit allowlists for job payloads and event metadata."""

    __slots__ = ("_event_schemas", "_job_schemas")

    def __init__(
        self,
        *,
        job_schemas: Mapping[str, type[BaseModel]] | None = None,
        event_schemas: Mapping[str, type[BaseModel]] | None = None,
    ) -> None:
        self._job_schemas = _prepare_schemas(job_schemas or {})
        self._event_schemas = _prepare_schemas(event_schemas or {})

    def _canonical_job_payload(self, job_type: str, value: object) -> str:
        return _canonical_schema_json(
            self._job_schemas.get(job_type), value, "input_payload", INPUT_PAYLOAD_MAX_BYTES
        )

    def _canonical_event_metadata(self, event_type: str, value: object) -> str:
        return _canonical_schema_json(
            self._event_schemas.get(event_type), value, "metadata", EVENT_METADATA_MAX_BYTES
        )


def _redact_validation_traceback[**P, R](operation: Callable[P, R]) -> Callable[P, R]:
    """Replace internal validation tracebacks which may retain rejected input."""

    @wraps(operation)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        message: str | None = None
        try:
            return operation(*args, **kwargs)
        except PersistenceValidationError as error:
            message = str(error)
        del args, kwargs
        raise PersistenceValidationError(message) from None

    return wrapped


class RepositoryValidation:
    """Central validation contract used by all persistence repositories."""

    __slots__ = ("_extra_metrics_schema", "_references", "_schemas")

    def __init__(
        self,
        *,
        schemas: PayloadSchemaRegistry | None = None,
        references: ReferenceContracts | None = None,
        extra_metrics_schema: type[BaseModel] | None = None,
    ) -> None:
        if extra_metrics_schema is not None:
            _ensure_closed_schema(extra_metrics_schema)
        self._schemas = schemas or PayloadSchemaRegistry()
        self._references = references or ReferenceContracts()
        self._extra_metrics_schema = extra_metrics_schema

    @_redact_validation_traceback
    def job(self, data: Mapping[str, object]) -> dict[str, Any]:
        validated = _validate_model(_JobInput, data)
        values = validated.model_dump(exclude={"input_payload"})
        values["input_payload_json"] = self._schemas._canonical_job_payload(
            validated.job_type, validated.input_payload
        )
        values["result_ref"] = _validate_reference(
            validated.result_ref,
            "result_ref",
            RESULT_REF_MAX_BYTES,
            self._references.result,
        )
        values["continuation_ref"] = _validate_reference(
            validated.continuation_ref,
            "continuation_ref",
            CONTINUATION_REF_MAX_BYTES,
            self._references.continuation,
        )
        return values

    @_redact_validation_traceback
    def attempt(self, data: Mapping[str, object]) -> dict[str, Any]:
        validated = _validate_model(_AttemptInput, data)
        values = validated.model_dump(exclude={"extra_metrics"})
        values["execution_ref"] = _validate_reference(
            validated.execution_ref,
            "execution_ref",
            EXECUTION_REF_MAX_BYTES,
            self._references.execution,
            urls_allowed=False,
        )
        values["extra_metrics_json"] = _optional_schema_json(
            self._extra_metrics_schema,
            validated.extra_metrics,
            "extra_metrics",
            EXTRA_METRICS_MAX_BYTES,
            maximum_depth=EXTRA_METRICS_MAX_DEPTH,
        )
        return values

    @_redact_validation_traceback
    def lease(self, data: Mapping[str, object]) -> dict[str, Any]:
        return _validate_model(_LeaseInput, data).model_dump()

    @_redact_validation_traceback
    def lease_observation(self, data: Mapping[str, object]) -> dict[str, Any]:
        return _validate_model(_LeaseObservationInput, data).model_dump()

    @_redact_validation_traceback
    def lease_renewal(self, data: Mapping[str, object]) -> dict[str, Any]:
        return _validate_model(_LeaseRenewalInput, data).model_dump()

    @_redact_validation_traceback
    def lease_release(self, data: Mapping[str, object]) -> dict[str, Any]:
        return _validate_model(_LeaseReleaseInput, data).model_dump()

    @_redact_validation_traceback
    def worker_state(self, data: Mapping[str, object]) -> dict[str, Any]:
        return _validate_model(_WorkerStateInput, data).model_dump()

    @_redact_validation_traceback
    def control_state(self, data: Mapping[str, object]) -> dict[str, Any]:
        return _validate_model(_ControlStateInput, data).model_dump()

    @_redact_validation_traceback
    def job_event(self, data: Mapping[str, object]) -> dict[str, Any]:
        validated = _validate_model(_JobEventInput, data)
        values = validated.model_dump(exclude={"metadata"})
        canonical_metadata = self._schemas._canonical_event_metadata(
            validated.event_type,
            {} if validated.metadata is None else validated.metadata,
        )
        values["metadata_json"] = None if validated.metadata is None else canonical_metadata
        return values


class _InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class _JobInput(_InputModel):
    job_id: StrictStr
    job_type: StrictStr
    role: StrictStr | None = None
    status: StrictStr
    parent_job_id: StrictStr | None = None
    idempotency_scope: StrictStr | None = None
    idempotency_key_digest: StrictBytes | None = Field(default=None, repr=False)
    request_fingerprint: StrictBytes | None = Field(default=None, repr=False)
    request_id: StrictStr | None = None
    input_payload: dict[str, Any] = Field(repr=False)
    result_ref: StrictStr | None = Field(default=None, repr=False)
    wait_kind: StrictStr | None = None
    continuation_ref: StrictStr | None = Field(default=None, repr=False)
    resumability_confirmed_at: StrictInt | None = None
    next_eligible_at: StrictInt | None = None
    created_at: StrictInt
    queued_at: StrictInt
    updated_at: StrictInt
    finished_at: StrictInt | None = None
    version: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        _canonical_uuid(self.job_id, "job_id")
        _identifier(self.job_type, "job_type")
        _optional_identifier(self.role, "role")
        _enum(self.status, JOB_STATUSES, "status")
        if self.parent_job_id is not None:
            _canonical_uuid(self.parent_job_id, "parent_job_id")
        if self.parent_job_id == self.job_id:
            _fail("parent_job_id", "different_from_job_id")
        _bounded_technical(self.idempotency_scope, "idempotency_scope", 128)
        _bounded_technical(self.request_id, "request_id", 128)
        idem_values = (
            self.idempotency_scope,
            self.idempotency_key_digest,
            self.request_fingerprint,
        )
        if any(value is not None for value in idem_values) and not all(
            value is not None for value in idem_values
        ):
            _fail("idempotency_scope", "all_or_none")
        for field_name, value in (
            ("idempotency_key_digest", self.idempotency_key_digest),
            ("request_fingerprint", self.request_fingerprint),
        ):
            if value is not None and len(value) != 32:
                _fail(field_name, "byte_length", 32)
        _nonnegative_timestamps(self)
        if not self.created_at <= self.queued_at <= self.updated_at:
            _fail("updated_at", "timestamp_order")
        if (
            self.finished_at is not None
            and not self.queued_at <= self.finished_at <= self.updated_at
        ):
            _fail("finished_at", "timestamp_order")
        if self.resumability_confirmed_at is not None and not (
            self.created_at <= self.resumability_confirmed_at <= self.updated_at
        ):
            _fail("resumability_confirmed_at", "timestamp_order")
        if self.next_eligible_at is not None and self.next_eligible_at < self.created_at:
            _fail("next_eligible_at", "timestamp_order")
        if (self.status in {"QUEUED", "RETRY_WAIT"}) != (self.next_eligible_at is not None):
            _fail("next_eligible_at", "status_consistency")
        if (self.status in {"DONE", "FAILED", "INTERRUPTED"}) != (self.finished_at is not None):
            _fail("finished_at", "status_consistency")
        if self.status == "WAITING":
            _enum(self.wait_kind, ("LOCAL", "EXTERNAL"), "wait_kind")
        elif self.wait_kind is not None:
            _fail("wait_kind", "status_consistency")
        continuation_present = self.continuation_ref is not None
        if continuation_present != (self.resumability_confirmed_at is not None):
            _fail("continuation_ref", "paired_field")
        if continuation_present and self.wait_kind != "EXTERNAL":
            _fail("continuation_ref", "external_wait_only")
        return self


class _AttemptInput(_InputModel):
    attempt_id: StrictStr
    job_id: StrictStr
    attempt_number: StrictInt = Field(ge=1)
    worker_id: StrictStr
    status: StrictStr
    execution_ref: StrictStr | None = Field(default=None, repr=False)
    agent_role: StrictStr | None = None
    model: StrictStr | None = None
    created_at: StrictInt
    updated_at: StrictInt
    started_at: StrictInt | None = None
    first_token_at: StrictInt | None = None
    finished_at: StrictInt | None = None
    queue_duration_ms: StrictInt | None = Field(default=None, ge=0)
    wake_duration_ms: StrictInt | None = Field(default=None, ge=0)
    execution_duration_ms: StrictInt | None = Field(default=None, ge=0)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)
    tool_calls_count: StrictInt | None = Field(default=None, ge=0)
    tool_duration_ms: StrictInt | None = Field(default=None, ge=0)
    error_class: StrictStr | None = None
    error_code: StrictStr | None = None
    error_detail_redacted: StrictStr | None = Field(default=None, repr=False)
    extra_metrics: dict[str, Any] | None = Field(default=None, repr=False)
    version: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        _canonical_uuid(self.attempt_id, "attempt_id")
        _canonical_uuid(self.job_id, "job_id")
        _identifier(self.worker_id, "worker_id")
        _enum(self.status, ATTEMPT_STATUSES, "status")
        _optional_identifier(self.agent_role, "agent_role")
        _bounded_technical(self.model, "model", 255)
        _bounded_technical(self.error_class, "error_class", 16)
        _bounded_technical(self.error_code, "error_code", 128)
        _optional_utf8_limit(
            self.error_detail_redacted, "error_detail_redacted", ERROR_DETAIL_MAX_BYTES
        )
        _nonnegative_timestamps(self)
        if self.updated_at < self.created_at:
            _fail("updated_at", "timestamp_order")
        if (
            self.started_at is not None
            and not self.created_at <= self.started_at <= self.updated_at
        ):
            _fail("started_at", "timestamp_order")
        start = self.started_at if self.started_at is not None else self.created_at
        for field_name, value in (
            ("first_token_at", self.first_token_at),
            ("finished_at", self.finished_at),
        ):
            if value is not None and not start <= value <= self.updated_at:
                _fail(field_name, "timestamp_order")
        if (self.status in {"SUCCEEDED", "FAILED", "INTERRUPTED"}) != (
            self.finished_at is not None
        ):
            _fail("finished_at", "status_consistency")
        return self


class _LeaseInput(_InputModel):
    lease_id: StrictStr
    worker_id: StrictStr
    owner: StrictStr
    purpose: StrictStr
    job_id: StrictStr | None = None
    attempt_id: StrictStr | None = None
    created_at: StrictInt
    last_heartbeat_at: StrictInt
    expires_at: StrictInt
    released_at: StrictInt | None = None
    release_reason: StrictStr | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        _canonical_uuid(self.lease_id, "lease_id")
        _identifier(self.worker_id, "worker_id")
        _identifier(self.owner, "owner", 128)
        _identifier(self.purpose, "purpose")
        if self.job_id is not None:
            _canonical_uuid(self.job_id, "job_id")
        if self.attempt_id is not None:
            _canonical_uuid(self.attempt_id, "attempt_id")
        _optional_identifier(self.release_reason, "release_reason")
        _nonnegative_timestamps(self)
        if not self.created_at <= self.last_heartbeat_at <= self.expires_at:
            _fail("last_heartbeat_at", "timestamp_order")
        if self.expires_at <= self.created_at:
            _fail("expires_at", "timestamp_order")
        if self.released_at is not None and self.released_at < self.created_at:
            _fail("released_at", "timestamp_order")
        return self


class _LeaseObservationInput(_InputModel):
    lease_id: StrictStr | None = None
    worker_id: StrictStr | None = None
    observed_at: StrictInt

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if (self.lease_id is None) == (self.worker_id is None):
            _fail("input", "exactly_one_identifier")
        if self.lease_id is not None:
            _canonical_uuid(self.lease_id, "lease_id")
        if self.worker_id is not None:
            _identifier(self.worker_id, "worker_id")
        _nonnegative_timestamps(self)
        return self


class _LeaseRenewalInput(_InputModel):
    lease_id: StrictStr
    last_heartbeat_at: StrictInt
    expires_at: StrictInt

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        _canonical_uuid(self.lease_id, "lease_id")
        _nonnegative_timestamps(self)
        if self.expires_at <= self.last_heartbeat_at:
            _fail("expires_at", "timestamp_order")
        return self


class _LeaseReleaseInput(_InputModel):
    lease_id: StrictStr
    released_at: StrictInt
    release_reason: StrictStr

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        _canonical_uuid(self.lease_id, "lease_id")
        _identifier(self.release_reason, "release_reason")
        _nonnegative_timestamps(self)
        return self


class _WorkerStateInput(_InputModel):
    worker_id: StrictStr
    state: StrictStr
    observed_at: StrictInt | None = None
    state_changed_at: StrictInt
    updated_at: StrictInt
    idle_since: StrictInt | None = None
    last_ready_at: StrictInt | None = None
    last_error_class: StrictStr | None = None
    last_error_code: StrictStr | None = None
    version: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        _identifier(self.worker_id, "worker_id")
        _enum(self.state, WORKER_STATES, "state")
        _bounded_technical(self.last_error_class, "last_error_class", 16)
        _bounded_technical(self.last_error_code, "last_error_code", 128)
        _nonnegative_timestamps(self)
        return self


class _ControlStateInput(_InputModel):
    singleton_id: StrictInt = 1
    dispatch_mode: StrictStr
    recovery_status: StrictStr
    process_instance_id: StrictStr | None = None
    started_at: StrictInt | None = None
    updated_at: StrictInt
    last_clean_shutdown_at: StrictInt | None = None
    version: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.singleton_id != 1:
            _fail("singleton_id", "singleton")
        _enum(self.dispatch_mode, DISPATCH_MODES, "dispatch_mode")
        _enum(self.recovery_status, RECOVERY_STATUSES, "recovery_status")
        if self.process_instance_id is not None:
            _canonical_uuid(self.process_instance_id, "process_instance_id")
        if (self.process_instance_id is None) != (self.started_at is None):
            _fail("process_instance_id", "paired_field")
        _nonnegative_timestamps(self)
        return self


class _JobEventInput(_InputModel):
    job_id: StrictStr
    attempt_id: StrictStr | None = None
    event_type: StrictStr
    from_status: StrictStr | None = None
    to_status: StrictStr | None = None
    error_class: StrictStr | None = None
    error_code: StrictStr | None = None
    metadata: dict[str, Any] | None = Field(default=None, repr=False)
    occurred_at: StrictInt

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        _canonical_uuid(self.job_id, "job_id")
        if self.attempt_id is not None:
            _canonical_uuid(self.attempt_id, "attempt_id")
        _identifier(self.event_type, "event_type")
        if (self.from_status is None) != (self.to_status is None):
            _fail("from_status", "paired_field")
        if self.from_status is not None:
            _enum(self.from_status, JOB_STATUSES, "from_status")
            _enum(self.to_status, JOB_STATUSES, "to_status")
        _bounded_technical(self.error_class, "error_class", 16)
        _bounded_technical(self.error_code, "error_code", 128)
        _nonnegative_timestamps(self)
        return self


def _prepare_schemas(
    schemas: Mapping[str, type[BaseModel]],
) -> Mapping[str, type[BaseModel]]:
    prepared: dict[str, type[BaseModel]] = {}
    for name, schema in schemas.items():
        try:
            _identifier(name, "schema_type")
            _ensure_closed_schema(schema)
        except PersistenceValidationError:
            raise PersistenceConfigurationError(
                "Persistenzschema-Registry enthält einen ungültigen Vertrag."
            ) from None
        prepared[name] = schema
    return MappingProxyType(prepared)


def _ensure_closed_schema(
    schema: type[BaseModel], checked: set[type[BaseModel]] | None = None
) -> None:
    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        raise PersistenceConfigurationError(
            "Persistenzschema-Registry enthält einen ungültigen Vertrag."
        )
    visited = checked if checked is not None else set()
    if schema in visited:
        return
    visited.add(schema)
    if schema.model_config.get("extra") != "forbid":
        raise PersistenceConfigurationError(
            "Persistenzschema muss unbekannte Felder ausdrücklich verbieten."
        )
    for model_field in schema.model_fields.values():
        _ensure_closed_annotation(model_field.annotation, visited)


def _ensure_closed_annotation(annotation: object, checked: set[type[BaseModel]]) -> None:
    if annotation is Any or annotation is object:
        raise PersistenceConfigurationError(
            "Persistenzschema darf keine ungeprüften Nutzdatenfelder enthalten."
        )
    origin = get_origin(annotation)
    if origin in {dict, Mapping}:
        raise PersistenceConfigurationError(
            "Persistenzschema darf keine freien JSON-Objekte enthalten."
        )
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        _ensure_closed_schema(annotation, checked)
        return
    for argument in get_args(annotation):
        _ensure_closed_annotation(argument, checked)


def _validate_model[T: _InputModel](model: type[T], data: Mapping[str, object]) -> T:
    try:
        validated = model.model_validate(data)
    except PydanticValidationError as error:
        details = error.errors(include_input=False, include_url=False)
        first = details[0] if details else {}
        location = first.get("loc", ())
        candidate = location[0] if location else None
        field_name = (
            candidate if isinstance(candidate, str) and candidate in _KNOWN_FIELDS else "input"
        )
        rule = str(first.get("type", "invalid"))
        safe_error = PersistenceValidationError(
            f"Persistenzvalidierung fehlgeschlagen: Feld '{field_name}', Regel '{rule}'."
        )
    else:
        return validated
    raise safe_error from None


def _canonical_schema_json(
    schema: type[BaseModel] | None,
    value: object,
    field_name: str,
    maximum_bytes: int,
) -> str:
    if schema is None:
        _fail(field_name, "registered_schema")
    return _schema_json(schema, value, field_name, maximum_bytes)


def _optional_schema_json(
    schema: type[BaseModel] | None,
    value: object | None,
    field_name: str,
    maximum_bytes: int,
    *,
    maximum_depth: int,
) -> str | None:
    if value is None:
        return None
    if schema is None:
        _fail(field_name, "registered_schema")
    return _schema_json(
        schema,
        value,
        field_name,
        maximum_bytes,
        maximum_depth=maximum_depth,
    )


def _schema_json(
    schema: type[BaseModel],
    value: object,
    field_name: str,
    maximum_bytes: int,
    *,
    maximum_depth: int | None = None,
) -> str:
    validation_failed = False
    try:
        validated = schema.model_validate(value)
        json_value = validated.model_dump(mode="json")
    except (PydanticValidationError, TypeError, ValueError):
        validation_failed = True
    if validation_failed:
        _fail(field_name, "registered_schema")
    if not isinstance(json_value, dict):
        _fail(field_name, "json_object")
    if maximum_depth is not None and _json_depth(json_value) > maximum_depth:
        _fail(field_name, "maximum_depth", maximum_depth)
    serialization_failed = False
    try:
        serialized = json.dumps(
            json_value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        serialization_failed = True
    if serialization_failed:
        _fail(field_name, "json_serialization")
    _utf8_limit(serialized, field_name, maximum_bytes)
    return serialized


def _json_depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 0


def _validate_reference(
    value: str | None,
    field_name: str,
    maximum_bytes: int,
    adapter: NonSecretReferenceAdapter | None,
    *,
    urls_allowed: bool = True,
) -> str | None:
    if value is None:
        return None
    if adapter is None:
        _fail(field_name, "reference_contract")
    adapter_failed = False
    try:
        validated = adapter.validate_non_secret_reference(value)
    except Exception:
        adapter_failed = True
    if adapter_failed:
        _fail(field_name, "reference_contract")
    if validated != value or type(validated) is not str:
        _fail(field_name, "reference_contract")
    _utf8_limit(validated, field_name, maximum_bytes)
    invalid_url = False
    try:
        parsed = urlsplit(validated)
        username = parsed.username
        password = parsed.password
    except ValueError:
        invalid_url = True
    if invalid_url:
        _fail(field_name, "reference_format")
    if not urls_allowed and (parsed.scheme or parsed.netloc):
        _fail(field_name, "non_url_reference")
    if username is not None or password is not None or parsed.query or parsed.fragment:
        _fail(field_name, "non_authenticating_reference")
    return validated


def _canonical_uuid(value: str, field_name: str) -> None:
    invalid = False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        invalid = True
    if invalid:
        _fail(field_name, "canonical_uuid4")
    if parsed.version != 4 or str(parsed) != value:
        _fail(field_name, "canonical_uuid4")


def _identifier(value: str, field_name: str, maximum_length: int = 63) -> None:
    if len(value) > maximum_length or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        _fail(field_name, "generic_identifier", maximum_length)


def _optional_identifier(value: str | None, field_name: str, maximum_length: int = 63) -> None:
    if value is not None:
        _identifier(value, field_name, maximum_length)


def _bounded_technical(value: str | None, field_name: str, maximum_length: int) -> None:
    if value is None:
        return
    if len(value) > maximum_length or _TECHNICAL_VALUE_PATTERN.fullmatch(value) is None:
        _fail(field_name, "technical_identifier", maximum_length)


def _enum(value: object, allowed: tuple[str, ...], field_name: str) -> None:
    if value not in allowed:
        _fail(field_name, "allowed_value")


def _nonnegative_timestamps(model: BaseModel) -> None:
    for field_name in (
        "created_at",
        "expires_at",
        "finished_at",
        "first_token_at",
        "idle_since",
        "last_clean_shutdown_at",
        "last_heartbeat_at",
        "last_ready_at",
        "next_eligible_at",
        "observed_at",
        "occurred_at",
        "queued_at",
        "released_at",
        "resumability_confirmed_at",
        "started_at",
        "state_changed_at",
        "updated_at",
    ):
        value = getattr(model, field_name, None)
        if value is not None and value < 0:
            _fail(field_name, "utc_epoch_microseconds")


def _optional_utf8_limit(value: str | None, field_name: str, maximum_bytes: int) -> None:
    if value is not None:
        _utf8_limit(value, field_name, maximum_bytes)


def _utf8_limit(value: str, field_name: str, maximum_bytes: int) -> None:
    invalid_utf8 = False
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        invalid_utf8 = True
    if invalid_utf8:
        _fail(field_name, "utf8_encoding")
    if encoded_size > maximum_bytes:
        _fail(field_name, "utf8_byte_limit", maximum_bytes)


def _fail(field_name: str, rule: str, limit: int | None = None) -> NoReturn:
    suffix = "" if limit is None else f", Limit '{limit}'"
    raise PersistenceValidationError(
        f"Persistenzvalidierung fehlgeschlagen: Feld '{field_name}', Regel '{rule}'{suffix}."
    ) from None
