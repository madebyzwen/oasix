from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from oasix.persistence import (
    Attempt,
    Job,
    JobEvent,
    Lease,
    PayloadSchemaRegistry,
    PersistenceIntegrityError,
    PersistenceRepositories,
    PersistenceValidationError,
    RepositoryValidation,
    WorkerState,
)

WORKER_ID = "worker-primary"
JOB_ID = "00000000-0000-4000-8000-000000000001"
SECOND_JOB_ID = "00000000-0000-4000-8000-000000000002"
ATTEMPT_ID = "10000000-0000-4000-8000-000000000001"
LEASE_ID = "20000000-0000-4000-8000-000000000001"


class _JobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task: str


class _EventMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: str


def _validation() -> RepositoryValidation:
    return RepositoryValidation(
        schemas=PayloadSchemaRegistry(
            job_schemas={"test-job": _JobPayload},
            event_schemas={"test-event": _EventMetadata},
        )
    )


def _worker(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "worker_id": WORKER_ID,
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
    data.update(changes)
    return data


def _job(job_id: str = JOB_ID, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "job_id": job_id,
        "job_type": "test-job",
        "role": None,
        "status": "QUEUED",
        "parent_job_id": None,
        "idempotency_scope": None,
        "idempotency_key_digest": None,
        "request_fingerprint": None,
        "request_id": None,
        "input_payload": {"task": "test"},
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
        "extra_metrics": None,
        "version": 1,
    }
    data.update(changes)
    return data


def _lease(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "lease_id": LEASE_ID,
        "worker_id": WORKER_ID,
        "owner": "control-plane",
        "purpose": "job-execution",
        "job_id": JOB_ID,
        "attempt_id": ATTEMPT_ID,
        "created_at": 10,
        "last_heartbeat_at": 11,
        "expires_at": 20,
        "released_at": None,
        "release_reason": None,
    }
    data.update(changes)
    return data


def _event(job_id: str = JOB_ID, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "job_id": job_id,
        "attempt_id": ATTEMPT_ID,
        "event_type": "test-event",
        "from_status": None,
        "to_status": None,
        "error_class": None,
        "error_code": None,
        "metadata": {"source": "test"},
        "occurred_at": 12,
    }
    data.update(changes)
    return data


def _control_state() -> dict[str, Any]:
    return {
        "singleton_id": 1,
        "dispatch_mode": "PAUSED_ADMIN",
        "recovery_status": "CLEAN",
        "process_instance_id": None,
        "started_at": None,
        "updated_at": 20,
        "last_clean_shutdown_at": 19,
        "version": 2,
    }


def test_writes_and_reads_all_entities_across_transactions(migrated_database: Any) -> None:
    with migrated_database.transaction() as session:
        repositories = PersistenceRepositories(session, _validation())
        worker = repositories.worker_states.add(_worker())
        job = repositories.jobs.add(_job())
        attempt = repositories.attempts.add(_attempt())
        lease = repositories.leases.add(_lease())
        event = repositories.job_events.append(_event())
        control = repositories.control_state.replace(_control_state())
        assert worker.worker_id == WORKER_ID
        assert job.job_id == JOB_ID
        assert attempt.attempt_id == ATTEMPT_ID
        assert lease.lease_id == LEASE_ID
        assert event.event_type == "test-event"
        assert control.dispatch_mode == "PAUSED_ADMIN"

    with migrated_database.session() as session:
        repositories = PersistenceRepositories(session, _validation())
        assert repositories.worker_states.get(WORKER_ID) is not None
        assert repositories.jobs.get(JOB_ID) is not None
        assert repositories.attempts.get(ATTEMPT_ID) is not None
        assert repositories.leases.get(LEASE_ID) is not None
        assert repositories.attempts.list_for_job(JOB_ID)[0].attempt_id == ATTEMPT_ID
        events = repositories.job_events.list_for_job(JOB_ID)
        assert len(events) == 1
        assert repositories.job_events.get(events[0].event_id) is not None
        assert repositories.control_state.get().version == 2


def test_missing_reads_do_not_create_rows(migrated_database: Any) -> None:
    with migrated_database.session() as session:
        repositories = PersistenceRepositories(session, _validation())
        assert repositories.jobs.get(JOB_ID) is None
        assert repositories.attempts.get(ATTEMPT_ID) is None
        assert repositories.leases.get(LEASE_ID) is None
        assert repositories.worker_states.get(WORKER_ID) is None
        assert repositories.job_events.get(999) is None

    with migrated_database.connection() as connection:
        for table in (Job, Attempt, Lease, WorkerState, JobEvent):
            assert connection.execute(select(table)).all() == []


def test_repository_does_not_commit_caller_session(migrated_database: Any) -> None:
    with migrated_database.session() as session:
        repositories = PersistenceRepositories(session, _validation())
        repositories.worker_states.add(_worker())
        session.flush()
        assert repositories.worker_states.get(WORKER_ID) is not None

    with migrated_database.session() as session:
        repositories = PersistenceRepositories(session, _validation())
        assert repositories.worker_states.get(WORKER_ID) is None


def test_multiple_repository_operations_roll_back_atomically(migrated_database: Any) -> None:
    with pytest.raises(PersistenceIntegrityError, match="job_correlation"):
        with migrated_database.transaction() as session:
            repositories = PersistenceRepositories(session, _validation())
            repositories.worker_states.add(_worker())
            repositories.jobs.add(_job())
            repositories.jobs.add(_job(SECOND_JOB_ID))
            repositories.attempts.add(_attempt())
            repositories.job_events.append(_event(SECOND_JOB_ID))

    with migrated_database.connection() as connection:
        for table in (Job, Attempt, WorkerState, JobEvent):
            assert connection.execute(select(table)).all() == []


@pytest.mark.parametrize("entity", ["event", "lease"])
def test_rejects_mismatched_job_attempt_correlation(
    migrated_database: Any,
    entity: str,
) -> None:
    with migrated_database.transaction() as session:
        repositories = PersistenceRepositories(session, _validation())
        repositories.worker_states.add(_worker())
        repositories.jobs.add(_job())
        repositories.jobs.add(_job(SECOND_JOB_ID))
        repositories.attempts.add(_attempt())

    with pytest.raises(PersistenceIntegrityError, match="job_correlation"):
        with migrated_database.transaction() as session:
            repositories = PersistenceRepositories(session, _validation())
            if entity == "event":
                repositories.job_events.append(_event(SECOND_JOB_ID))
            else:
                repositories.leases.add(_lease(job_id=SECOND_JOB_ID))


def test_event_repository_is_append_only(migrated_database: Any) -> None:
    with migrated_database.session() as session:
        repository = PersistenceRepositories(session, _validation()).job_events
        assert not hasattr(repository, "update")
        assert not hasattr(repository, "delete")


def test_rejects_missing_foreign_rows_before_write(migrated_database: Any) -> None:
    with pytest.raises(PersistenceIntegrityError, match="referenced_row"):
        with migrated_database.transaction() as session:
            PersistenceRepositories(session, _validation()).attempts.add(_attempt())


def test_repository_validation_error_redacts_input_from_all_representations(
    migrated_database: Any,
    caplog: pytest.LogCaptureFixture,
    production_traceback_locals: Any,
) -> None:
    sentinel = "SENTINEL-PRIVATE-REPOSITORY-SECRET"
    invalid_job = _job(input_payload={sentinel: sentinel})

    with pytest.raises(PersistenceValidationError) as captured:
        with migrated_database.transaction() as session:
            PersistenceRepositories(session, _validation()).jobs.add(invalid_job)

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in caplog.text
    assert sentinel not in production_traceback_locals(captured.value)
    assert captured.value.__context__ is None
