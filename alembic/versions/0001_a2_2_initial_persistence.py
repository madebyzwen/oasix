"""Create the six A.2 persistence tables.

Revision ID: 0001_a2_2
Revises: None
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_a2_2"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_STATUS_SQL = (
    "'QUEUED', 'RUNNING', 'WAITING', 'RETRY_WAIT', 'BLOCKED', 'DONE', 'FAILED', 'INTERRUPTED'"
)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=63), nullable=False),
        sa.Column("role", sa.String(length=63), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("parent_job_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_scope", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key_digest", sa.LargeBinary(), nullable=True),
        sa.Column("request_fingerprint", sa.LargeBinary(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("input_payload_json", sa.Text(), nullable=False),
        sa.Column("result_ref", sa.String(length=2048), nullable=True),
        sa.Column("wait_kind", sa.String(length=8), nullable=True),
        sa.Column("continuation_ref", sa.String(length=4096), nullable=True),
        sa.Column("resumability_confirmed_at", sa.BigInteger(), nullable=True),
        sa.Column("next_eligible_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("queued_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("job_id", name="pk_jobs"),
        sa.ForeignKeyConstraint(
            ["parent_job_id"],
            ["jobs.job_id"],
            name="fk_jobs_parent_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(f"status IN ({_JOB_STATUS_SQL})", name="ck_jobs_status"),
        sa.CheckConstraint("version >= 1", name="ck_jobs_version_positive"),
        sa.CheckConstraint(
            "parent_job_id IS NULL OR parent_job_id <> job_id",
            name="ck_jobs_parent_not_self",
        ),
        sa.CheckConstraint("queued_at >= created_at", name="ck_jobs_queued_after_created"),
        sa.CheckConstraint("updated_at >= queued_at", name="ck_jobs_updated_after_queued"),
        sa.CheckConstraint(
            "finished_at IS NULL OR (finished_at >= queued_at AND finished_at <= updated_at)",
            name="ck_jobs_finished_time_order",
        ),
        sa.CheckConstraint(
            "resumability_confirmed_at IS NULL OR "
            "(resumability_confirmed_at >= created_at "
            "AND resumability_confirmed_at <= updated_at)",
            name="ck_jobs_resumability_time_order",
        ),
        sa.CheckConstraint(
            "next_eligible_at IS NULL OR next_eligible_at >= created_at",
            name="ck_jobs_eligibility_after_created",
        ),
        sa.CheckConstraint(
            "((idempotency_scope IS NULL AND idempotency_key_digest IS NULL "
            "AND request_fingerprint IS NULL) OR "
            "(idempotency_scope IS NOT NULL AND idempotency_key_digest IS NOT NULL "
            "AND request_fingerprint IS NOT NULL))",
            name="ck_jobs_idempotency_all_or_none",
        ),
        sa.CheckConstraint(
            "idempotency_key_digest IS NULL OR length(idempotency_key_digest) = 32",
            name="ck_jobs_idempotency_digest_length",
        ),
        sa.CheckConstraint(
            "request_fingerprint IS NULL OR length(request_fingerprint) = 32",
            name="ck_jobs_request_fingerprint_length",
        ),
        sa.CheckConstraint(
            "((status = 'WAITING' AND wait_kind IS NOT NULL "
            "AND wait_kind IN ('LOCAL', 'EXTERNAL')) OR "
            "(status <> 'WAITING' AND wait_kind IS NULL))",
            name="ck_jobs_wait_kind_for_status",
        ),
        sa.CheckConstraint(
            "((continuation_ref IS NULL AND resumability_confirmed_at IS NULL) OR "
            "(continuation_ref IS NOT NULL AND resumability_confirmed_at IS NOT NULL "
            "AND wait_kind = 'EXTERNAL'))",
            name="ck_jobs_continuation_pair",
        ),
        sa.CheckConstraint(
            "((status IN ('QUEUED', 'RETRY_WAIT') AND next_eligible_at IS NOT NULL) OR "
            "(status NOT IN ('QUEUED', 'RETRY_WAIT') AND next_eligible_at IS NULL))",
            name="ck_jobs_eligibility_for_status",
        ),
        sa.CheckConstraint(
            "((status IN ('DONE', 'FAILED', 'INTERRUPTED') AND finished_at IS NOT NULL) "
            "OR (status NOT IN ('DONE', 'FAILED', 'INTERRUPTED') AND finished_at IS NULL))",
            name="ck_jobs_finished_for_status",
        ),
        sa.CheckConstraint("json_valid(input_payload_json) = 1", name="ck_jobs_payload_json"),
        sa.CheckConstraint(
            "length(CAST(input_payload_json AS BLOB)) <= 1048576",
            name="ck_jobs_payload_bytes",
        ),
        sa.CheckConstraint(
            "result_ref IS NULL OR length(CAST(result_ref AS BLOB)) <= 2048",
            name="ck_jobs_result_ref_bytes",
        ),
        sa.CheckConstraint(
            "continuation_ref IS NULL OR length(CAST(continuation_ref AS BLOB)) <= 4096",
            name="ck_jobs_continuation_ref_bytes",
        ),
    )
    op.create_index(
        "uq_jobs_idempotency_scope_digest",
        "jobs",
        ["idempotency_scope", "idempotency_key_digest"],
        unique=True,
        sqlite_where=sa.text("idempotency_scope IS NOT NULL"),
    )
    op.create_index(
        "ix_jobs_queue_due",
        "jobs",
        ["next_eligible_at", "created_at", "job_id"],
        unique=False,
        sqlite_where=sa.text("status IN ('QUEUED', 'RETRY_WAIT')"),
    )
    op.create_index("ix_jobs_parent_job_id", "jobs", ["parent_job_id"], unique=False)
    op.create_index("ix_jobs_request_id", "jobs", ["request_id"], unique=False)
    op.create_index("ix_jobs_status_updated_at", "jobs", ["status", "updated_at"], unique=False)

    op.create_table(
        "worker_states",
        sa.Column("worker_id", sa.String(length=63), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.BigInteger(), nullable=True),
        sa.Column("state_changed_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("idle_since", sa.BigInteger(), nullable=True),
        sa.Column("last_ready_at", sa.BigInteger(), nullable=True),
        sa.Column("last_error_class", sa.String(length=16), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("worker_id", name="pk_worker_states"),
        sa.CheckConstraint(
            "state IN ('UNKNOWN', 'WAKING', 'READY', 'BUSY', 'IDLE', 'SLEEPING', 'UNAVAILABLE')",
            name="ck_worker_states_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_worker_states_version_positive"),
    )
    op.create_index(
        "ix_worker_states_state_updated_at",
        "worker_states",
        ["state", "updated_at"],
        unique=False,
    )
    op.create_index("ix_worker_states_idle_since", "worker_states", ["idle_since"], unique=False)

    op.create_table(
        "attempts",
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=63), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("execution_ref", sa.String(length=2048), nullable=True),
        sa.Column("agent_role", sa.String(length=63), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=True),
        sa.Column("first_token_at", sa.BigInteger(), nullable=True),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.Column("queue_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("wake_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("execution_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("tool_calls_count", sa.BigInteger(), nullable=True),
        sa.Column("tool_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("error_class", sa.String(length=16), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail_redacted", sa.Text(), nullable=True),
        sa.Column("extra_metrics_json", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("attempt_id", name="pk_attempts"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.job_id"], name="fk_attempts_job_id_jobs", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["worker_states.worker_id"],
            name="fk_attempts_worker_id_worker_states",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_attempts_job_number"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUSPENDED', 'SUCCEEDED', 'FAILED', 'INTERRUPTED')",
            name="ck_attempts_status",
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_attempts_number_positive"),
        sa.CheckConstraint("version >= 1", name="ck_attempts_version_positive"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_attempts_updated_after_created"),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_attempts_started_after_created",
        ),
        sa.CheckConstraint(
            "first_token_at IS NULL OR "
            "(first_token_at >= COALESCE(started_at, created_at) "
            "AND first_token_at <= updated_at)",
            name="ck_attempts_first_token_time_order",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR "
            "(finished_at >= COALESCE(started_at, created_at) "
            "AND finished_at <= updated_at)",
            name="ck_attempts_finished_time_order",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at <= updated_at",
            name="ck_attempts_started_before_updated",
        ),
        sa.CheckConstraint(
            "((status IN ('SUCCEEDED', 'FAILED', 'INTERRUPTED') AND finished_at IS NOT NULL) "
            "OR (status NOT IN ('SUCCEEDED', 'FAILED', 'INTERRUPTED') "
            "AND finished_at IS NULL))",
            name="ck_attempts_finished_for_status",
        ),
        sa.CheckConstraint(
            "queue_duration_ms IS NULL OR queue_duration_ms >= 0",
            name="ck_attempts_queue_duration_nonnegative",
        ),
        sa.CheckConstraint(
            "wake_duration_ms IS NULL OR wake_duration_ms >= 0",
            name="ck_attempts_wake_duration_nonnegative",
        ),
        sa.CheckConstraint(
            "execution_duration_ms IS NULL OR execution_duration_ms >= 0",
            name="ck_attempts_execution_duration_nonnegative",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_attempts_input_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_attempts_output_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_attempts_total_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "tool_calls_count IS NULL OR tool_calls_count >= 0",
            name="ck_attempts_tool_calls_nonnegative",
        ),
        sa.CheckConstraint(
            "tool_duration_ms IS NULL OR tool_duration_ms >= 0",
            name="ck_attempts_tool_duration_nonnegative",
        ),
        sa.CheckConstraint(
            "execution_ref IS NULL OR length(CAST(execution_ref AS BLOB)) <= 2048",
            name="ck_attempts_execution_ref_bytes",
        ),
        sa.CheckConstraint(
            "error_detail_redacted IS NULL OR length(CAST(error_detail_redacted AS BLOB)) <= 8192",
            name="ck_attempts_error_detail_bytes",
        ),
        sa.CheckConstraint(
            "extra_metrics_json IS NULL OR length(CAST(extra_metrics_json AS BLOB)) <= 65536",
            name="ck_attempts_extra_metrics_bytes",
        ),
        sa.CheckConstraint(
            "extra_metrics_json IS NULL OR json_valid(extra_metrics_json) = 1",
            name="ck_attempts_extra_metrics_json",
        ),
    )
    op.create_index(
        "uq_attempts_one_open_per_job",
        "attempts",
        ["job_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('PENDING', 'RUNNING', 'SUSPENDED')"),
    )
    op.create_index(
        "uq_attempts_worker_execution_ref",
        "attempts",
        ["worker_id", "execution_ref"],
        unique=True,
        sqlite_where=sa.text("execution_ref IS NOT NULL"),
    )
    op.create_index(
        "ix_attempts_status_updated_at", "attempts", ["status", "updated_at"], unique=False
    )
    op.create_index("ix_attempts_worker_status", "attempts", ["worker_id", "status"], unique=False)
    op.create_index(
        "ix_attempts_job_created_at", "attempts", ["job_id", "created_at"], unique=False
    )

    op.create_table(
        "leases",
        sa.Column("lease_id", sa.String(length=36), nullable=False),
        sa.Column("worker_id", sa.String(length=63), nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=63), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("last_heartbeat_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("released_at", sa.BigInteger(), nullable=True),
        sa.Column("release_reason", sa.String(length=63), nullable=True),
        sa.PrimaryKeyConstraint("lease_id", name="pk_leases"),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["worker_states.worker_id"],
            name="fk_leases_worker_id_worker_states",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.job_id"], name="fk_leases_job_id_jobs", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.attempt_id"],
            name="fk_leases_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_leases_expires_after_created"),
        sa.CheckConstraint(
            "last_heartbeat_at >= created_at AND last_heartbeat_at <= expires_at",
            name="ck_leases_heartbeat_time_order",
        ),
        sa.CheckConstraint(
            "released_at IS NULL OR released_at >= created_at",
            name="ck_leases_released_after_created",
        ),
    )
    op.create_index(
        "ix_leases_worker_active",
        "leases",
        ["worker_id", "released_at", "expires_at"],
        unique=False,
    )
    op.create_index("ix_leases_owner_expires_at", "leases", ["owner", "expires_at"], unique=False)
    op.create_index("ix_leases_job_id", "leases", ["job_id"], unique=False)
    op.create_index("ix_leases_attempt_id", "leases", ["attempt_id"], unique=False)

    op.create_table(
        "job_events",
        sa.Column("event_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=63), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=True),
        sa.Column("error_class", sa.String(length=16), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_job_events"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.job_id"],
            name="fk_job_events_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.attempt_id"],
            name="fk_job_events_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"((from_status IS NULL AND to_status IS NULL) OR "
            f"(from_status IS NOT NULL AND to_status IS NOT NULL "
            f"AND from_status IN ({_JOB_STATUS_SQL}) AND to_status IN ({_JOB_STATUS_SQL})))",
            name="ck_job_events_status_pair",
        ),
        sa.CheckConstraint(
            "metadata_json IS NULL OR length(CAST(metadata_json AS BLOB)) <= 16384",
            name="ck_job_events_metadata_bytes",
        ),
        sa.CheckConstraint(
            "metadata_json IS NULL OR json_valid(metadata_json) = 1",
            name="ck_job_events_metadata_json",
        ),
    )
    op.create_index(
        "ix_job_events_job_time",
        "job_events",
        ["job_id", "occurred_at", "event_id"],
        unique=False,
    )
    op.create_index("ix_job_events_attempt_id", "job_events", ["attempt_id"], unique=False)
    op.create_index(
        "ix_job_events_type_time",
        "job_events",
        ["event_type", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "control_state",
        sa.Column("singleton_id", sa.SmallInteger(), nullable=False),
        sa.Column("dispatch_mode", sa.String(length=24), nullable=False),
        sa.Column("recovery_status", sa.String(length=16), nullable=False),
        sa.Column("process_instance_id", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("last_clean_shutdown_at", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("singleton_id", name="pk_control_state"),
        sa.CheckConstraint("singleton_id = 1", name="ck_control_state_singleton"),
        sa.CheckConstraint(
            "dispatch_mode IN ('ACTIVE', 'PAUSED_RECOVERY', 'PAUSED_ADMIN', 'PAUSED_POWER')",
            name="ck_control_state_dispatch_mode",
        ),
        sa.CheckConstraint(
            "recovery_status IN ('CLEAN', 'REQUIRED', 'RUNNING', 'FAILED')",
            name="ck_control_state_recovery_status",
        ),
        sa.CheckConstraint(
            "((process_instance_id IS NULL AND started_at IS NULL) OR "
            "(process_instance_id IS NOT NULL AND started_at IS NOT NULL))",
            name="ck_control_state_process_pair",
        ),
        sa.CheckConstraint("version >= 1", name="ck_control_state_version_positive"),
    )

    control_state = sa.table(
        "control_state",
        sa.column("singleton_id", sa.SmallInteger()),
        sa.column("dispatch_mode", sa.String(length=24)),
        sa.column("recovery_status", sa.String(length=16)),
        sa.column("process_instance_id", sa.String(length=36)),
        sa.column("started_at", sa.BigInteger()),
        sa.column("updated_at", sa.BigInteger()),
        sa.column("last_clean_shutdown_at", sa.BigInteger()),
        sa.column("version", sa.Integer()),
    )
    op.bulk_insert(
        control_state,
        [
            {
                "singleton_id": 1,
                "dispatch_mode": "PAUSED_RECOVERY",
                "recovery_status": "REQUIRED",
                "process_instance_id": None,
                "started_at": None,
                "updated_at": time.time_ns() // 1_000,
                "last_clean_shutdown_at": None,
                "version": 1,
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("control_state")
    op.drop_table("job_events")
    op.drop_table("leases")
    op.drop_table("attempts")
    op.drop_table("worker_states")
    op.drop_table("jobs")
