"""Session-scoped repositories for the six A.2 persistence entities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import wraps

from sqlalchemy import select
from sqlalchemy.orm import Session

from oasix.persistence.errors import PersistenceError, PersistenceIntegrityError
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
    """Create and retrieve leases; lifecycle operations remain a later phase."""

    def get(self, lease_id: str) -> Lease | None:
        return self._session.get(Lease, lease_id)

    @_redact_repository_traceback
    def add(self, data: Mapping[str, object]) -> Lease:
        values = self._validation.lease(data)
        _require_present(self._session, WorkerState, values["worker_id"], "worker_id")
        job_id = values["job_id"]
        attempt_id = values["attempt_id"]
        if job_id is not None:
            _require_present(self._session, Job, job_id, "job_id")
        if attempt_id is not None:
            attempt = _require_present(self._session, Attempt, attempt_id, "attempt_id")
            if job_id is not None and attempt.job_id != job_id:
                _integrity_failure("attempt_id", "job_correlation")
        entity = Lease(**values)
        self._session.add(entity)
        return entity


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
