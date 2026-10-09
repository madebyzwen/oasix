from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from oasix.persistence import Attempt, ControlState, Job, JobEvent, Lease, WorkerState

WORKER_ID = "worker-primary"
JOB_ID = "00000000-0000-4000-8000-000000000001"
SECOND_JOB_ID = "00000000-0000-4000-8000-000000000002"


def _worker_values(worker_id: str = WORKER_ID, **changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "worker_id": worker_id,
        "state": "UNKNOWN",
        "observed_at": None,
        "state_changed_at": 10,
        "updated_at": 10,
        "idle_since": None,
        "last_ready_at": None,
        "last_error_class": None,
        "last_error_code": None,
        "version": 1,
    }
    values.update(changes)
    return values


def _job_values(job_id: str = JOB_ID, **changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "job_id": job_id,
        "job_type": "generic-task",
        "role": None,
        "status": "QUEUED",
        "parent_job_id": None,
        "idempotency_scope": None,
        "idempotency_key_digest": None,
        "request_fingerprint": None,
        "request_id": None,
        "input_payload_json": "{}",
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
    values.update(changes)
    return values


def _attempt_values(
    attempt_id: str = "10000000-0000-4000-8000-000000000001",
    **changes: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "attempt_id": attempt_id,
        "job_id": JOB_ID,
        "attempt_number": 1,
        "worker_id": WORKER_ID,
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
        "extra_metrics_json": None,
        "version": 1,
    }
    values.update(changes)
    return values


def _lease_values(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "lease_id": "20000000-0000-4000-8000-000000000001",
        "worker_id": WORKER_ID,
        "owner": "control-plane",
        "purpose": "job-execution",
        "job_id": JOB_ID,
        "attempt_id": None,
        "created_at": 10,
        "last_heartbeat_at": 11,
        "expires_at": 20,
        "released_at": None,
        "release_reason": None,
    }
    values.update(changes)
    return values


def _seed_worker_and_jobs(database: Any, *job_ids: str) -> None:
    with database.transaction() as session:
        session.execute(WorkerState.__table__.insert(), _worker_values())
        for job_id in job_ids:
            session.execute(Job.__table__.insert(), _job_values(job_id))


def _expect_integrity_error(database: Any, statement: Any, parameters: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(statement, parameters)


def test_accepts_valid_rows_for_all_six_tables(migrated_database: Any) -> None:
    _seed_worker_and_jobs(migrated_database, JOB_ID)
    with migrated_database.transaction() as session:
        attempt_result = session.execute(Attempt.__table__.insert(), _attempt_values())
        session.execute(Lease.__table__.insert(), _lease_values())
        session.execute(
            JobEvent.__table__.insert(),
            {
                "job_id": JOB_ID,
                "attempt_id": None,
                "event_type": "job-created",
                "from_status": None,
                "to_status": None,
                "error_class": None,
                "error_code": None,
                "metadata_json": "{}",
                "occurred_at": 10,
            },
        )
        assert attempt_result.rowcount == 1


def test_rejects_foreign_key_violation(migrated_database: Any) -> None:
    _expect_integrity_error(
        migrated_database,
        Attempt.__table__.insert(),
        _attempt_values(job_id="ffffffff-ffff-4fff-8fff-ffffffffffff"),
    )


def test_rejects_self_referencing_job(migrated_database: Any) -> None:
    _expect_integrity_error(
        migrated_database,
        Job.__table__.insert(),
        _job_values(parent_job_id=JOB_ID),
    )


@pytest.mark.parametrize(
    ("statement_factory", "parameters_factory"),
    [
        (
            lambda: Job.__table__.insert(),
            lambda: _job_values(status="NOT_A_STATUS", next_eligible_at=None),
        ),
        (
            lambda: Attempt.__table__.insert(),
            lambda: _attempt_values(status="NOT_A_STATUS", finished_at=None),
        ),
        (
            lambda: WorkerState.__table__.insert(),
            lambda: _worker_values(state="NOT_A_STATE"),
        ),
        (
            lambda: update(ControlState).where(ControlState.singleton_id == 1),
            lambda: {"dispatch_mode": "NOT_A_MODE"},
        ),
        (
            lambda: update(ControlState).where(ControlState.singleton_id == 1),
            lambda: {"recovery_status": "NOT_A_STATUS"},
        ),
    ],
)
def test_rejects_invalid_status_values(
    migrated_database: Any,
    statement_factory: Callable[[], Any],
    parameters_factory: Callable[[], dict[str, Any]],
) -> None:
    statement = statement_factory()
    if statement.table.name == "attempts":
        _seed_worker_and_jobs(migrated_database, JOB_ID)
    _expect_integrity_error(
        migrated_database,
        statement,
        parameters_factory(),
    )


@pytest.mark.parametrize("entity", ["job", "attempt", "worker", "control"])
def test_rejects_invalid_versions(migrated_database: Any, entity: str) -> None:
    if entity == "job":
        statement = Job.__table__.insert()
        values = _job_values(version=0)
    elif entity == "attempt":
        _seed_worker_and_jobs(migrated_database, JOB_ID)
        statement = Attempt.__table__.insert()
        values = _attempt_values(version=0)
    elif entity == "worker":
        statement = WorkerState.__table__.insert()
        values = _worker_values(version=0)
    else:
        statement = update(ControlState).where(ControlState.singleton_id == 1)
        values = {"version": 0}
    _expect_integrity_error(migrated_database, statement, values)


@pytest.mark.parametrize(
    "changes",
    [
        {"idempotency_scope": "scope-only"},
        {"idempotency_key_digest": b"k" * 32, "request_fingerprint": b"f" * 32},
        {
            "idempotency_scope": "scope",
            "idempotency_key_digest": b"short",
            "request_fingerprint": b"f" * 32,
        },
        {
            "idempotency_scope": "scope",
            "idempotency_key_digest": b"k" * 32,
            "request_fingerprint": b"short",
        },
    ],
)
def test_rejects_invalid_idempotency_combinations(
    migrated_database: Any,
    changes: dict[str, Any],
) -> None:
    _expect_integrity_error(
        migrated_database,
        Job.__table__.insert(),
        _job_values(**changes),
    )


def test_idempotency_is_unique_only_within_scope(migrated_database: Any) -> None:
    shared_digest = b"k" * 32
    fingerprint = b"f" * 32
    with migrated_database.transaction() as session:
        session.execute(
            Job.__table__.insert(),
            _job_values(
                idempotency_scope="scope-a",
                idempotency_key_digest=shared_digest,
                request_fingerprint=fingerprint,
            ),
        )
        session.execute(
            Job.__table__.insert(),
            _job_values(
                SECOND_JOB_ID,
                idempotency_scope="scope-b",
                idempotency_key_digest=shared_digest,
                request_fingerprint=fingerprint,
            ),
        )

    _expect_integrity_error(
        migrated_database,
        Job.__table__.insert(),
        _job_values(
            "00000000-0000-4000-8000-000000000003",
            idempotency_scope="scope-a",
            idempotency_key_digest=shared_digest,
            request_fingerprint=b"g" * 32,
        ),
    )


def test_allows_multiple_completed_attempts_but_only_one_open_attempt(
    migrated_database: Any,
) -> None:
    _seed_worker_and_jobs(migrated_database, JOB_ID, SECOND_JOB_ID)
    with migrated_database.transaction() as session:
        session.execute(Attempt.__table__.insert(), _attempt_values())
        session.execute(
            Attempt.__table__.insert(),
            _attempt_values(
                "10000000-0000-4000-8000-000000000002",
                attempt_number=2,
            ),
        )
        session.execute(
            Attempt.__table__.insert(),
            _attempt_values(
                "10000000-0000-4000-8000-000000000003",
                job_id=SECOND_JOB_ID,
                status="PENDING",
                finished_at=None,
            ),
        )

    _expect_integrity_error(
        migrated_database,
        Attempt.__table__.insert(),
        _attempt_values(
            "10000000-0000-4000-8000-000000000004",
            job_id=SECOND_JOB_ID,
            attempt_number=2,
            status="RUNNING",
            finished_at=None,
        ),
    )


def test_rejects_duplicate_attempt_number(migrated_database: Any) -> None:
    _seed_worker_and_jobs(migrated_database, JOB_ID)
    with migrated_database.transaction() as session:
        session.execute(Attempt.__table__.insert(), _attempt_values())
    _expect_integrity_error(
        migrated_database,
        Attempt.__table__.insert(),
        _attempt_values("10000000-0000-4000-8000-000000000002"),
    )


def test_execution_reference_is_unique_per_worker(migrated_database: Any) -> None:
    _seed_worker_and_jobs(migrated_database, JOB_ID, SECOND_JOB_ID)
    with migrated_database.transaction() as session:
        session.execute(
            Attempt.__table__.insert(),
            _attempt_values(execution_ref="opaque-reference"),
        )
    _expect_integrity_error(
        migrated_database,
        Attempt.__table__.insert(),
        _attempt_values(
            "10000000-0000-4000-8000-000000000002",
            job_id=SECOND_JOB_ID,
            execution_ref="opaque-reference",
        ),
    )


@pytest.mark.parametrize(
    ("table", "values"),
    [
        (Job.__table__, _job_values(queued_at=9)),
        (Job.__table__, _job_values(updated_at=9)),
        (Attempt.__table__, _attempt_values(started_at=9)),
        (Attempt.__table__, _attempt_values(queue_duration_ms=-1)),
        (Attempt.__table__, _attempt_values(output_tokens=-1)),
        (Lease.__table__, _lease_values(expires_at=10)),
        (Lease.__table__, _lease_values(last_heartbeat_at=21)),
        (Lease.__table__, _lease_values(released_at=9)),
    ],
)
def test_rejects_invalid_time_order_and_negative_metrics(
    migrated_database: Any,
    table: Any,
    values: dict[str, Any],
) -> None:
    if table.name in {"attempts", "leases"}:
        _seed_worker_and_jobs(migrated_database, JOB_ID)
    _expect_integrity_error(migrated_database, table.insert(), values)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "WAITING", "next_eligible_at": None, "wait_kind": None},
        {"status": "RUNNING", "next_eligible_at": 11},
        {"status": "QUEUED", "finished_at": 10},
        {"status": "DONE", "next_eligible_at": None, "finished_at": None},
        {
            "status": "WAITING",
            "next_eligible_at": None,
            "wait_kind": "LOCAL",
            "continuation_ref": "opaque",
            "resumability_confirmed_at": 10,
        },
    ],
)
def test_rejects_invalid_job_wait_and_completion_combinations(
    migrated_database: Any,
    changes: dict[str, Any],
) -> None:
    _expect_integrity_error(
        migrated_database,
        Job.__table__.insert(),
        _job_values(**changes),
    )


def test_rejects_invalid_attempt_completion_combination(migrated_database: Any) -> None:
    _seed_worker_and_jobs(migrated_database, JOB_ID)
    _expect_integrity_error(
        migrated_database,
        Attempt.__table__.insert(),
        _attempt_values(status="RUNNING", finished_at=12),
    )


def test_rejects_incomplete_control_process_pair(migrated_database: Any) -> None:
    _expect_integrity_error(
        migrated_database,
        update(ControlState).where(ControlState.singleton_id == 1),
        {"process_instance_id": "40000000-0000-4000-8000-000000000001"},
    )


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [("QUEUED", None), ("NOT_A_STATUS", "RUNNING")],
)
def test_rejects_invalid_job_event_status_pair(
    migrated_database: Any,
    from_status: str,
    to_status: str | None,
) -> None:
    _seed_worker_and_jobs(migrated_database, JOB_ID)
    _expect_integrity_error(
        migrated_database,
        JobEvent.__table__.insert(),
        {
            "job_id": JOB_ID,
            "attempt_id": None,
            "event_type": "invalid-transition",
            "from_status": from_status,
            "to_status": to_status,
            "error_class": None,
            "error_code": None,
            "metadata_json": None,
            "occurred_at": 10,
        },
    )


@pytest.mark.parametrize(
    ("table", "values"),
    [
        (Job.__table__, _job_values(input_payload_json="{")),
        (Attempt.__table__, _attempt_values(extra_metrics_json="{")),
        (
            JobEvent.__table__,
            {
                "job_id": JOB_ID,
                "attempt_id": None,
                "event_type": "invalid-json",
                "from_status": None,
                "to_status": None,
                "error_class": None,
                "error_code": None,
                "metadata_json": "{",
                "occurred_at": 10,
            },
        ),
    ],
)
def test_rejects_invalid_json(
    migrated_database: Any,
    table: Any,
    values: dict[str, Any],
) -> None:
    if table.name in {"attempts", "job_events"}:
        _seed_worker_and_jobs(migrated_database, JOB_ID)
    _expect_integrity_error(migrated_database, table.insert(), values)


def _json_object_with_byte_length(length: int) -> str:
    return '{"v":"' + ("a" * (length - 8)) + '"}'


@pytest.mark.parametrize(
    ("field_name", "limit"),
    [
        ("input_payload_json", 1_048_576),
        ("result_ref", 2_048),
        ("continuation_ref", 4_096),
        ("execution_ref", 2_048),
        ("error_detail_redacted", 8_192),
        ("extra_metrics_json", 65_536),
        ("metadata_json", 16_384),
    ],
)
def test_enforces_utf8_byte_limits_at_boundaries(
    migrated_database: Any,
    field_name: str,
    limit: int,
) -> None:
    _seed_worker_and_jobs(migrated_database, JOB_ID, SECOND_JOB_ID)
    if field_name in {"execution_ref", "error_detail_redacted", "extra_metrics_json"}:
        table = Attempt.__table__
        make_values = lambda value, identifier: _attempt_values(  # noqa: E731
            identifier,
            attempt_number=int(identifier[-1]),
            **{field_name: value},
        )
    elif field_name == "metadata_json":
        table = JobEvent.__table__
        make_values = lambda value, identifier: {  # noqa: E731
            "job_id": JOB_ID,
            "attempt_id": None,
            "event_type": identifier,
            "from_status": None,
            "to_status": None,
            "error_class": None,
            "error_code": None,
            "metadata_json": value,
            "occurred_at": 10,
        }
    else:
        table = Job.__table__

        def make_values(value: str, identifier: str) -> dict[str, Any]:
            changes: dict[str, Any] = {field_name: value}
            if field_name == "continuation_ref":
                changes.update(
                    status="WAITING",
                    wait_kind="EXTERNAL",
                    next_eligible_at=None,
                    resumability_confirmed_at=10,
                )
            return _job_values(identifier, **changes)

    identifiers = [
        "30000000-0000-4000-8000-000000000001",
        "30000000-0000-4000-8000-000000000002",
        "30000000-0000-4000-8000-000000000003",
    ]
    values = []
    for size, identifier in zip((limit - 1, limit, limit + 1), identifiers, strict=True):
        if field_name in {"input_payload_json", "extra_metrics_json", "metadata_json"}:
            value = _json_object_with_byte_length(size)
        else:
            value = "é" * (size // 2) + ("a" if size % 2 else "")
        values.append(make_values(value, identifier))

    with migrated_database.transaction() as session:
        session.execute(table.insert(), values[0])
        session.execute(table.insert(), values[1])
    _expect_integrity_error(migrated_database, table.insert(), values[2])


def test_sensitive_rejected_value_is_hidden_from_exception(
    migrated_database: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "SENTINEL-SENSITIVE-EXECUTION-REFERENCE"
    _seed_worker_and_jobs(migrated_database, JOB_ID)
    with pytest.raises(IntegrityError) as captured:
        with migrated_database.transaction() as session:
            session.execute(
                Attempt.__table__.insert(),
                _attempt_values(execution_ref=sentinel * 100),
            )

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in caplog.text


def test_constraint_failure_rolls_back_related_inserts(migrated_database: Any) -> None:
    with pytest.raises(IntegrityError):
        with migrated_database.transaction() as session:
            session.execute(WorkerState.__table__.insert(), _worker_values())
            session.execute(Job.__table__.insert(), _job_values(version=0))

    with migrated_database.connection() as connection:
        assert connection.execute(select(WorkerState.worker_id)).all() == []
