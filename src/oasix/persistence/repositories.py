"""Session-scoped repositories for the six A.2 persistence entities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import wraps

from sqlalchemy import case, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from oasix.persistence.errors import (
    LeaseConflictError,
    LeaseInactiveError,
    LeaseNotFoundError,
    PersistenceError,
    PersistenceIntegrityError,
)
from oasix.persistence.migrations import verify_schema_revision
from oasix.persistence.models import Attempt, ControlState, Job, JobEvent, Lease, WorkerState
from oasix.persistence.validation import RepositoryValidation

_REVISION_VERIFIED = object()


def _redact_repository_traceback[**P, R](operation: Callable[P, R]) -> Callable[P, R]:
    """Replace write tracebacks which may otherwise retain rejected input."""

    @wraps(operation)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        message: str | None = None
        error_type: type[PersistenceError] = PersistenceError
        try:
            return operation(*args, **kwargs)
        except PersistenceError as error:
            message = str(error)
            error_type = type(error)
        del args, kwargs
        raise error_type(message) from None

    return wrapped


class PersistenceRepositories:
    """Bind validated repositories to one caller-owned SQLAlchemy session."""

    __slots__ = (
        "attempts",
        "control_state",
        "job_events",
        "jobs",
        "leases",
        "worker_states",
    )

    def __init__(
        self,
        session: Session,
        validation: RepositoryValidation | None = None,
    ) -> None:
        verify_schema_revision(session.connection())
        validator = validation or RepositoryValidation()
        self.jobs = JobsRepository(session, validator, _REVISION_VERIFIED)
        self.attempts = AttemptsRepository(session, validator, _REVISION_VERIFIED)
        self.leases = LeasesRepository(session, validator, _REVISION_VERIFIED)
        self.worker_states = WorkerStatesRepository(session, validator, _REVISION_VERIFIED)
        self.control_state = ControlStateRepository(session, validator, _REVISION_VERIFIED)
        self.job_events = JobEventsRepository(session, validator, _REVISION_VERIFIED)


class _Repository:
    __slots__ = ("_session", "_validation")

    def __init__(
        self,
        session: Session,
        validation: RepositoryValidation,
        revision_verification: object | None = None,
    ) -> None:
        if revision_verification is not _REVISION_VERIFIED:
            verify_schema_revision(session.connection())
        self._session = session
        self._validation = validation


class JobsRepository(_Repository):
    """Create and retrieve jobs without implementing state transitions."""

    def get(self, job_id: str) -> Job | None:
        return self._session.get(Job, job_id)

    @_redact_repository_traceback
    def add(self, data: Mapping[str, object]) -> Job:
        values = self._validation.job(data)
        parent_job_id = values["parent_job_id"]
        if parent_job_id is not None:
            _require_present(self._session, Job, parent_job_id, "parent_job_id")
        entity = Job(**values)
        self._session.add(entity)
        return entity


class AttemptsRepository(_Repository):
    """Create and retrieve immutable attempt identities."""

    def get(self, attempt_id: str) -> Attempt | None:
        return self._session.get(Attempt, attempt_id)

    def list_for_job(self, job_id: str) -> list[Attempt]:
        statement = select(Attempt).where(Attempt.job_id == job_id).order_by(Attempt.attempt_number)
        return list(self._session.scalars(statement))

    @_redact_repository_traceback
    def add(self, data: Mapping[str, object]) -> Attempt:
        values = self._validation.attempt(data)
        _require_present(self._session, Job, values["job_id"], "job_id")
        _require_present(self._session, WorkerState, values["worker_id"], "worker_id")
        entity = Attempt(**values)
        self._session.add(entity)
        return entity


class LeasesRepository(_Repository):
    """Persist atomic lease lifecycle operations in the caller's transaction."""

    def get(self, lease_id: str) -> Lease | None:
        return self._session.get(Lease, lease_id)

    @_redact_repository_traceback
    def add(self, data: Mapping[str, object]) -> Lease:
        values = self._validation.lease(data)
        self._require_references(values)
        entity = Lease(**values)
        self._session.add(entity)
        return entity

    @_redact_repository_traceback
    def acquire(self, data: Mapping[str, object], *, observed_at: int) -> Lease:
        """Insert once, or return the same still-active lease identity."""

        values = self._validation.lease(data)
        observation = self._validation.lease_observation(
            {"lease_id": values["lease_id"], "observed_at": observed_at}
        )
        self._require_references(values)
        statement = (
            sqlite_insert(Lease)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[Lease.lease_id])
        )
        result = self._session.execute(statement)
        entity = self._session.get(Lease, values["lease_id"], populate_existing=True)
        if entity is None:
            raise LeaseNotFoundError("Lease konnte nicht sicher gelesen werden.")
        if result.rowcount == 1:
            return entity
        if not _same_lease_contract(entity, values):
            raise LeaseConflictError("Lease-Vertrag steht in Konflikt mit dem Bestand.")
        if entity.created_at > observation["observed_at"]:
            raise LeaseConflictError("Lease-Zeitbasis steht in Konflikt mit dem Bestand.")
        if entity.released_at is not None or entity.expires_at <= observation["observed_at"]:
            raise LeaseInactiveError("Inaktive Lease darf nicht erneut aktiviert werden.")
        return entity

    @_redact_repository_traceback
    def renew(self, data: Mapping[str, object]) -> Lease:
        """Extend one active lease without reviving it or moving time backwards."""

        values = self._validation.lease_renewal(data)
        heartbeat_at = values["last_heartbeat_at"]
        statement = (
            update(Lease)
            .where(
                Lease.lease_id == values["lease_id"],
                Lease.released_at.is_(None),
                Lease.expires_at > heartbeat_at,
                Lease.last_heartbeat_at <= heartbeat_at,
            )
            .values(
                last_heartbeat_at=heartbeat_at,
                expires_at=case(
                    (Lease.expires_at < values["expires_at"], values["expires_at"]),
                    else_=Lease.expires_at,
                ),
            )
        )
        result = self._session.execute(statement)
        if result.rowcount != 1:
            self._raise_renewal_failure(values["lease_id"], heartbeat_at)
        entity = self._session.get(Lease, values["lease_id"], populate_existing=True)
        if entity is None:
            raise LeaseNotFoundError("Lease wurde nicht gefunden.")
        return entity

    @_redact_repository_traceback
    def release(self, data: Mapping[str, object]) -> Lease:
        """Release once and preserve the first release timestamp and reason."""

        values = self._validation.lease_release(data)
        existing = self.get(values["lease_id"])
        if existing is None:
            raise LeaseNotFoundError("Lease wurde nicht gefunden.")
        if existing.released_at is not None:
            return existing
        if values["released_at"] < existing.created_at:
            raise LeaseConflictError("Lease-Zeitbasis steht in Konflikt mit dem Bestand.")
        statement = (
            update(Lease)
            .where(
                Lease.lease_id == values["lease_id"],
                Lease.released_at.is_(None),
            )
            .values(
                released_at=values["released_at"],
                release_reason=values["release_reason"],
            )
        )
        self._session.execute(statement)
        entity = self._session.get(Lease, values["lease_id"], populate_existing=True)
        if entity is None:
            raise LeaseNotFoundError("Lease wurde nicht gefunden.")
        return entity

    @_redact_repository_traceback
    def list_active(self, *, worker_id: str, observed_at: int) -> list[Lease]:
        """Return only unreleased leases whose expiry is strictly in the future."""

        values = self._validation.lease_observation(
            {"worker_id": worker_id, "observed_at": observed_at}
        )
        statement = (
            select(Lease)
            .where(
                Lease.worker_id == values["worker_id"],
                Lease.released_at.is_(None),
                Lease.expires_at > values["observed_at"],
            )
            .order_by(Lease.lease_id)
        )
        return list(self._session.scalars(statement))

    def _require_references(self, values: Mapping[str, object]) -> None:
        _require_present(self._session, WorkerState, values["worker_id"], "worker_id")
        job_id = values["job_id"]
        attempt_id = values["attempt_id"]
        if job_id is not None:
            _require_present(self._session, Job, job_id, "job_id")
        if attempt_id is not None:
            attempt = _require_present(self._session, Attempt, attempt_id, "attempt_id")
            if job_id is not None and attempt.job_id != job_id:
                _integrity_failure("attempt_id", "job_correlation")

    def _raise_renewal_failure(self, lease_id: str, observed_at: int) -> None:
        entity = self.get(lease_id)
        if entity is None:
            raise LeaseNotFoundError("Lease wurde nicht gefunden.")
        if entity.released_at is not None or entity.expires_at <= observed_at:
            raise LeaseInactiveError("Inaktive Lease darf nicht erneuert werden.")
        raise LeaseConflictError("Lease-Zeitbasis steht in Konflikt mit dem Bestand.")


class WorkerStatesRepository(_Repository):
    """Create and retrieve persisted worker observations."""

    def get(self, worker_id: str) -> WorkerState | None:
        return self._session.get(WorkerState, worker_id)

    @_redact_repository_traceback
    def add(self, data: Mapping[str, object]) -> WorkerState:
        entity = WorkerState(**self._validation.worker_state(data))
        self._session.add(entity)
        return entity


class ControlStateRepository(_Repository):
    """Read or replace the singleton snapshot without transition semantics."""

    _WRITABLE_FIELDS = (
        "dispatch_mode",
        "recovery_status",
        "process_instance_id",
        "started_at",
        "updated_at",
        "last_clean_shutdown_at",
        "version",
    )

    def get(self) -> ControlState | None:
        return self._session.get(ControlState, 1)

    @_redact_repository_traceback
    def replace(self, data: Mapping[str, object]) -> ControlState:
        values = self._validation.control_state(data)
        entity = self.get()
        if entity is None:
            _integrity_failure("singleton_id", "required_row")
        for field_name in self._WRITABLE_FIELDS:
            setattr(entity, field_name, values[field_name])
        return entity


class JobEventsRepository(_Repository):
    """Append and read job events; update and delete are intentionally absent."""

    def get(self, event_id: int) -> JobEvent | None:
        return self._session.get(JobEvent, event_id)

    def list_for_job(self, job_id: str) -> list[JobEvent]:
        statement = (
            select(JobEvent)
            .where(JobEvent.job_id == job_id)
            .order_by(JobEvent.occurred_at, JobEvent.event_id)
        )
        return list(self._session.scalars(statement))

    @_redact_repository_traceback
    def append(self, data: Mapping[str, object]) -> JobEvent:
        values = self._validation.job_event(data)
        job_id = values["job_id"]
        _require_present(self._session, Job, job_id, "job_id")
        attempt_id = values["attempt_id"]
        if attempt_id is not None:
            attempt = _require_present(self._session, Attempt, attempt_id, "attempt_id")
            if attempt.job_id != job_id:
                _integrity_failure("attempt_id", "job_correlation")
        entity = JobEvent(**values)
        self._session.add(entity)
        return entity


def _require_present[T: Job | Attempt | WorkerState](
    session: Session,
    entity_type: type[T],
    identifier: object,
    field_name: str,
) -> T:
    entity = session.get(entity_type, identifier)
    if entity is None:
        _integrity_failure(field_name, "referenced_row")
    return entity


def _integrity_failure(field_name: str, rule: str) -> None:
    raise PersistenceIntegrityError(
        f"Persistenzintegrität verletzt: Feld '{field_name}', Regel '{rule}'."
    )


def _same_lease_contract(entity: Lease, values: Mapping[str, object]) -> bool:
    identity_matches = all(
        getattr(entity, field_name) == values[field_name]
        for field_name in ("worker_id", "owner", "purpose", "job_id", "attempt_id")
    )
    requested_ttl = values["expires_at"] - values["created_at"]
    persisted_ttl = entity.expires_at - entity.created_at
    return identity_matches and requested_ttl == persisted_ttl
