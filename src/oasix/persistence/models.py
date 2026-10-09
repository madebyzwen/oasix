"""SQLAlchemy models for the six A.2 persistence entities."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JOB_STATUSES = (
    "QUEUED",
    "RUNNING",
    "WAITING",
    "RETRY_WAIT",
    "BLOCKED",
    "DONE",
    "FAILED",
    "INTERRUPTED",
)
ATTEMPT_STATUSES = (
    "PENDING",
    "RUNNING",
    "SUSPENDED",
    "SUCCEEDED",
    "FAILED",
    "INTERRUPTED",
)
WORKER_STATES = (
    "UNKNOWN",
    "WAKING",
    "READY",
    "BUSY",
    "IDLE",
    "SLEEPING",
    "UNAVAILABLE",
)
DISPATCH_MODES = ("ACTIVE", "PAUSED_RECOVERY", "PAUSED_ADMIN", "PAUSED_POWER")
RECOVERY_STATUSES = ("CLEAN", "REQUIRED", "RUNNING", "FAILED")

_JOB_STATUS_SQL = ", ".join(f"'{status}'" for status in JOB_STATUSES)
_ATTEMPT_STATUS_SQL = ", ".join(f"'{status}'" for status in ATTEMPT_STATUSES)
_WORKER_STATE_SQL = ", ".join(f"'{state}'" for state in WORKER_STATES)
_DISPATCH_MODE_SQL = ", ".join(f"'{mode}'" for mode in DISPATCH_MODES)
_RECOVERY_STATUS_SQL = ", ".join(f"'{status}'" for status in RECOVERY_STATUSES)


class Base(DeclarativeBase):
    """Shared metadata registry for all versioned OASIX persistence models."""


class Job(Base):
    """Stable job identity and persistent queue state."""

    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(63), nullable=False)
    role: Mapped[str | None] = mapped_column(String(63))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("jobs.job_id", name="fk_jobs_parent_job_id_jobs", ondelete="RESTRICT"),
    )
    idempotency_scope: Mapped[str | None] = mapped_column(String(128))
    idempotency_key_digest: Mapped[bytes | None] = mapped_column(LargeBinary)
    request_fingerprint: Mapped[bytes | None] = mapped_column(LargeBinary)
    request_id: Mapped[str | None] = mapped_column(String(128))
    input_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_ref: Mapped[str | None] = mapped_column(String(2048))
    wait_kind: Mapped[str | None] = mapped_column(String(8))
    continuation_ref: Mapped[str | None] = mapped_column(String(4096))
    resumability_confirmed_at: Mapped[int | None] = mapped_column(BigInteger)
    next_eligible_at: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    queued_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    parent: Mapped[Job | None] = relationship(
        "Job",
        back_populates="children",
        remote_side="Job.job_id",
    )
    children: Mapped[list[Job]] = relationship("Job", back_populates="parent")
    attempts: Mapped[list[Attempt]] = relationship("Attempt", back_populates="job")
    leases: Mapped[list[Lease]] = relationship("Lease", back_populates="job")
    events: Mapped[list[JobEvent]] = relationship("JobEvent", back_populates="job")

    __table_args__ = (
        PrimaryKeyConstraint("job_id", name="pk_jobs"),
        CheckConstraint(f"status IN ({_JOB_STATUS_SQL})", name="ck_jobs_status"),
        CheckConstraint("version >= 1", name="ck_jobs_version_positive"),
        CheckConstraint(
            "parent_job_id IS NULL OR parent_job_id <> job_id",
            name="ck_jobs_parent_not_self",
        ),
        CheckConstraint("queued_at >= created_at", name="ck_jobs_queued_after_created"),
        CheckConstraint("updated_at >= queued_at", name="ck_jobs_updated_after_queued"),
        CheckConstraint(
            "finished_at IS NULL OR (finished_at >= queued_at AND finished_at <= updated_at)",
            name="ck_jobs_finished_time_order",
        ),
        CheckConstraint(
            "resumability_confirmed_at IS NULL OR "
            "(resumability_confirmed_at >= created_at "
            "AND resumability_confirmed_at <= updated_at)",
            name="ck_jobs_resumability_time_order",
        ),
        CheckConstraint(
            "next_eligible_at IS NULL OR next_eligible_at >= created_at",
            name="ck_jobs_eligibility_after_created",
        ),
        CheckConstraint(
            "((idempotency_scope IS NULL AND idempotency_key_digest IS NULL "
            "AND request_fingerprint IS NULL) OR "
            "(idempotency_scope IS NOT NULL AND idempotency_key_digest IS NOT NULL "
            "AND request_fingerprint IS NOT NULL))",
            name="ck_jobs_idempotency_all_or_none",
        ),
        CheckConstraint(
            "idempotency_key_digest IS NULL OR length(idempotency_key_digest) = 32",
            name="ck_jobs_idempotency_digest_length",
        ),
        CheckConstraint(
            "request_fingerprint IS NULL OR length(request_fingerprint) = 32",
            name="ck_jobs_request_fingerprint_length",
        ),
        CheckConstraint(
            "((status = 'WAITING' AND wait_kind IS NOT NULL "
            "AND wait_kind IN ('LOCAL', 'EXTERNAL')) OR "
            "(status <> 'WAITING' AND wait_kind IS NULL))",
            name="ck_jobs_wait_kind_for_status",
        ),
        CheckConstraint(
            "((continuation_ref IS NULL AND resumability_confirmed_at IS NULL) OR "
            "(continuation_ref IS NOT NULL AND resumability_confirmed_at IS NOT NULL "
            "AND wait_kind = 'EXTERNAL'))",
            name="ck_jobs_continuation_pair",
        ),
        CheckConstraint(
            "((status IN ('QUEUED', 'RETRY_WAIT') AND next_eligible_at IS NOT NULL) OR "
            "(status NOT IN ('QUEUED', 'RETRY_WAIT') AND next_eligible_at IS NULL))",
            name="ck_jobs_eligibility_for_status",
        ),
        CheckConstraint(
            "((status IN ('DONE', 'FAILED', 'INTERRUPTED') AND finished_at IS NOT NULL) "
            "OR (status NOT IN ('DONE', 'FAILED', 'INTERRUPTED') AND finished_at IS NULL))",
            name="ck_jobs_finished_for_status",
        ),
        CheckConstraint("json_valid(input_payload_json) = 1", name="ck_jobs_payload_json"),
        CheckConstraint(
            "length(CAST(input_payload_json AS BLOB)) <= 1048576",
            name="ck_jobs_payload_bytes",
        ),
        CheckConstraint(
            "result_ref IS NULL OR length(CAST(result_ref AS BLOB)) <= 2048",
            name="ck_jobs_result_ref_bytes",
        ),
        CheckConstraint(
            "continuation_ref IS NULL OR length(CAST(continuation_ref AS BLOB)) <= 4096",
            name="ck_jobs_continuation_ref_bytes",
        ),
        Index(
            "uq_jobs_idempotency_scope_digest",
            "idempotency_scope",
            "idempotency_key_digest",
            unique=True,
            sqlite_where=text("idempotency_scope IS NOT NULL"),
        ),
        Index(
            "ix_jobs_queue_due",
            "next_eligible_at",
            "created_at",
            "job_id",
            sqlite_where=text("status IN ('QUEUED', 'RETRY_WAIT')"),
        ),
        Index("ix_jobs_parent_job_id", "parent_job_id"),
        Index("ix_jobs_request_id", "request_id"),
        Index("ix_jobs_status_updated_at", "status", "updated_at"),
    )


class WorkerState(Base):
    """Last observed state of a generically identified worker."""

    __tablename__ = "worker_states"

    worker_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[int | None] = mapped_column(BigInteger)
    state_changed_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idle_since: Mapped[int | None] = mapped_column(BigInteger)
    last_ready_at: Mapped[int | None] = mapped_column(BigInteger)
    last_error_class: Mapped[str | None] = mapped_column(String(16))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    attempts: Mapped[list[Attempt]] = relationship("Attempt", back_populates="worker")
    leases: Mapped[list[Lease]] = relationship("Lease", back_populates="worker")

    __table_args__ = (
        PrimaryKeyConstraint("worker_id", name="pk_worker_states"),
        CheckConstraint(f"state IN ({_WORKER_STATE_SQL})", name="ck_worker_states_state"),
        CheckConstraint("version >= 1", name="ck_worker_states_version_positive"),
        Index("ix_worker_states_state_updated_at", "state", "updated_at"),
        Index("ix_worker_states_idle_since", "idle_since"),
    )


class Attempt(Base):
    """One concrete execution attempt belonging to a stable job."""

    __tablename__ = "attempts"

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.job_id", name="fk_attempts_job_id_jobs", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(
        String(63),
        ForeignKey(
            "worker_states.worker_id",
            name="fk_attempts_worker_id_worker_states",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_ref: Mapped[str | None] = mapped_column(String(2048))
    agent_role: Mapped[str | None] = mapped_column(String(63))
    model: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[int | None] = mapped_column(BigInteger)
    first_token_at: Mapped[int | None] = mapped_column(BigInteger)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)
    queue_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    wake_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    execution_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    tool_calls_count: Mapped[int | None] = mapped_column(BigInteger)
    tool_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    error_class: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail_redacted: Mapped[str | None] = mapped_column(Text)
    extra_metrics_json: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    job: Mapped[Job] = relationship("Job", back_populates="attempts")
    worker: Mapped[WorkerState] = relationship("WorkerState", back_populates="attempts")
    leases: Mapped[list[Lease]] = relationship("Lease", back_populates="attempt")
    events: Mapped[list[JobEvent]] = relationship("JobEvent", back_populates="attempt")

    __table_args__ = (
        PrimaryKeyConstraint("attempt_id", name="pk_attempts"),
        UniqueConstraint("job_id", "attempt_number", name="uq_attempts_job_number"),
        CheckConstraint(
            f"status IN ({_ATTEMPT_STATUS_SQL})",
            name="ck_attempts_status",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_attempts_number_positive"),
        CheckConstraint("version >= 1", name="ck_attempts_version_positive"),
        CheckConstraint("updated_at >= created_at", name="ck_attempts_updated_after_created"),
        CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_attempts_started_after_created",
        ),
        CheckConstraint(
            "first_token_at IS NULL OR "
            "(first_token_at >= COALESCE(started_at, created_at) "
            "AND first_token_at <= updated_at)",
            name="ck_attempts_first_token_time_order",
        ),
        CheckConstraint(
            "finished_at IS NULL OR "
            "(finished_at >= COALESCE(started_at, created_at) "
            "AND finished_at <= updated_at)",
            name="ck_attempts_finished_time_order",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at <= updated_at",
            name="ck_attempts_started_before_updated",
        ),
        CheckConstraint(
            "((status IN ('SUCCEEDED', 'FAILED', 'INTERRUPTED') AND finished_at IS NOT NULL) "
            "OR (status NOT IN ('SUCCEEDED', 'FAILED', 'INTERRUPTED') "
            "AND finished_at IS NULL))",
            name="ck_attempts_finished_for_status",
        ),
        CheckConstraint(
            "queue_duration_ms IS NULL OR queue_duration_ms >= 0",
            name="ck_attempts_queue_duration_nonnegative",
        ),
        CheckConstraint(
            "wake_duration_ms IS NULL OR wake_duration_ms >= 0",
            name="ck_attempts_wake_duration_nonnegative",
        ),
        CheckConstraint(
            "execution_duration_ms IS NULL OR execution_duration_ms >= 0",
            name="ck_attempts_execution_duration_nonnegative",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_attempts_input_tokens_nonnegative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_attempts_output_tokens_nonnegative",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_attempts_total_tokens_nonnegative",
        ),
        CheckConstraint(
            "tool_calls_count IS NULL OR tool_calls_count >= 0",
            name="ck_attempts_tool_calls_nonnegative",
        ),
        CheckConstraint(
            "tool_duration_ms IS NULL OR tool_duration_ms >= 0",
            name="ck_attempts_tool_duration_nonnegative",
        ),
        CheckConstraint(
            "execution_ref IS NULL OR length(CAST(execution_ref AS BLOB)) <= 2048",
            name="ck_attempts_execution_ref_bytes",
        ),
        CheckConstraint(
            "error_detail_redacted IS NULL OR length(CAST(error_detail_redacted AS BLOB)) <= 8192",
            name="ck_attempts_error_detail_bytes",
        ),
        CheckConstraint(
            "extra_metrics_json IS NULL OR length(CAST(extra_metrics_json AS BLOB)) <= 65536",
            name="ck_attempts_extra_metrics_bytes",
        ),
        CheckConstraint(
            "extra_metrics_json IS NULL OR json_valid(extra_metrics_json) = 1",
            name="ck_attempts_extra_metrics_json",
        ),
        Index(
            "uq_attempts_one_open_per_job",
            "job_id",
            unique=True,
            sqlite_where=text("status IN ('PENDING', 'RUNNING', 'SUSPENDED')"),
        ),
        Index(
            "uq_attempts_worker_execution_ref",
            "worker_id",
            "execution_ref",
            unique=True,
            sqlite_where=text("execution_ref IS NOT NULL"),
        ),
        Index("ix_attempts_status_updated_at", "status", "updated_at"),
        Index("ix_attempts_worker_status", "worker_id", "status"),
        Index("ix_attempts_job_created_at", "job_id", "created_at"),
    )


class Lease(Base):
    """Only authoritative persisted record of active worker use."""

    __tablename__ = "leases"

    lease_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    worker_id: Mapped[str] = mapped_column(
        String(63),
        ForeignKey(
            "worker_states.worker_id",
            name="fk_leases_worker_id_worker_states",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(63), nullable=False)
    job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("jobs.job_id", name="fk_leases_job_id_jobs", ondelete="RESTRICT"),
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "attempts.attempt_id",
            name="fk_leases_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_heartbeat_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    released_at: Mapped[int | None] = mapped_column(BigInteger)
    release_reason: Mapped[str | None] = mapped_column(String(63))

    worker: Mapped[WorkerState] = relationship("WorkerState", back_populates="leases")
    job: Mapped[Job | None] = relationship("Job", back_populates="leases")
    attempt: Mapped[Attempt | None] = relationship("Attempt", back_populates="leases")

    __table_args__ = (
        PrimaryKeyConstraint("lease_id", name="pk_leases"),
        CheckConstraint("expires_at > created_at", name="ck_leases_expires_after_created"),
        CheckConstraint(
            "last_heartbeat_at >= created_at AND last_heartbeat_at <= expires_at",
            name="ck_leases_heartbeat_time_order",
        ),
        CheckConstraint(
            "released_at IS NULL OR released_at >= created_at",
            name="ck_leases_released_after_created",
        ),
        Index("ix_leases_worker_active", "worker_id", "released_at", "expires_at"),
        Index("ix_leases_owner_expires_at", "owner", "expires_at"),
        Index("ix_leases_job_id", "job_id"),
        Index("ix_leases_attempt_id", "attempt_id"),
    )


class JobEvent(Base):
    """Append-only audit and state-transition event for a job."""

    __tablename__ = "job_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "jobs.job_id",
            name="fk_job_events_job_id_jobs",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "attempts.attempt_id",
            name="fk_job_events_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
    )
    event_type: Mapped[str] = mapped_column(String(63), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    error_class: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    job: Mapped[Job] = relationship("Job", back_populates="events")
    attempt: Mapped[Attempt | None] = relationship("Attempt", back_populates="events")

    __table_args__ = (
        PrimaryKeyConstraint("event_id", name="pk_job_events"),
        CheckConstraint(
            f"((from_status IS NULL AND to_status IS NULL) OR "
            f"(from_status IS NOT NULL AND to_status IS NOT NULL "
            f"AND from_status IN ({_JOB_STATUS_SQL}) AND to_status IN ({_JOB_STATUS_SQL})))",
            name="ck_job_events_status_pair",
        ),
        CheckConstraint(
            "metadata_json IS NULL OR length(CAST(metadata_json AS BLOB)) <= 16384",
            name="ck_job_events_metadata_bytes",
        ),
        CheckConstraint(
            "metadata_json IS NULL OR json_valid(metadata_json) = 1",
            name="ck_job_events_metadata_json",
        ),
        Index("ix_job_events_job_time", "job_id", "occurred_at", "event_id"),
        Index("ix_job_events_attempt_id", "attempt_id"),
        Index("ix_job_events_type_time", "event_type", "occurred_at"),
    )


class ControlState(Base):
    """Singleton control-plane dispatch and recovery state."""

    __tablename__ = "control_state"

    singleton_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    dispatch_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    recovery_status: Mapped[str] = mapped_column(String(16), nullable=False)
    process_instance_id: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_clean_shutdown_at: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("singleton_id", name="pk_control_state"),
        CheckConstraint("singleton_id = 1", name="ck_control_state_singleton"),
        CheckConstraint(
            f"dispatch_mode IN ({_DISPATCH_MODE_SQL})",
            name="ck_control_state_dispatch_mode",
        ),
        CheckConstraint(
            f"recovery_status IN ({_RECOVERY_STATUS_SQL})",
            name="ck_control_state_recovery_status",
        ),
        CheckConstraint(
            "((process_instance_id IS NULL AND started_at IS NULL) OR "
            "(process_instance_id IS NOT NULL AND started_at IS NOT NULL))",
            name="ck_control_state_process_pair",
        ),
        CheckConstraint("version >= 1", name="ck_control_state_version_positive"),
    )
