"""Persistent lease lifecycle with an explicit UTC clock and TTL contract."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps

from oasix.persistence.errors import (
    PersistenceError,
    PersistenceOperationError,
    PersistenceValidationError,
)
from oasix.persistence.models import Lease
from oasix.persistence.repositories import LeasesRepository

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECONDS_PER_SECOND = 1_000_000
_MAX_SQLITE_INTEGER = 2**63 - 1

type UtcClock = Callable[[], datetime]


def _redact_lifecycle_traceback[**P, R](operation: Callable[P, R]) -> Callable[P, R]:
    """Replace lifecycle tracebacks which may retain rejected caller input."""

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


class LeaseLifecycle:
    """Operate leases inside the transaction owned by the repository caller."""

    __slots__ = ("_clock", "_repository")

    def __init__(
        self,
        repository: LeasesRepository,
        *,
        clock: UtcClock | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or _utc_now

    def __repr__(self) -> str:
        return "LeaseLifecycle(session_bound=True)"

    @_redact_lifecycle_traceback
    def acquire(
        self,
        *,
        worker_id: str,
        owner: str,
        purpose: str,
        ttl_seconds: int,
        lease_id: str | None = None,
        job_id: str | None = None,
        attempt_id: str | None = None,
    ) -> Lease:
        """Create a lease, with caller-supplied IDs enabling safe operation retry."""

        observed_at = self._now()
        expires_at = _expiry_from_ttl(observed_at, ttl_seconds)
        identity = str(uuid.uuid4()) if lease_id is None else lease_id
        return self._repository.acquire(
            {
                "lease_id": identity,
                "worker_id": worker_id,
                "owner": owner,
                "purpose": purpose,
                "job_id": job_id,
                "attempt_id": attempt_id,
                "created_at": observed_at,
                "last_heartbeat_at": observed_at,
                "expires_at": expires_at,
                "released_at": None,
                "release_reason": None,
            },
            observed_at=observed_at,
        )

    @_redact_lifecycle_traceback
    def renew(self, *, lease_id: str, ttl_seconds: int) -> Lease:
        """Heartbeat and extend a lease which is active at the observed instant."""

        observed_at = self._now()
        expires_at = _expiry_from_ttl(observed_at, ttl_seconds)
        return self._repository.renew(
            {
                "lease_id": lease_id,
                "last_heartbeat_at": observed_at,
                "expires_at": expires_at,
            }
        )

    @_redact_lifecycle_traceback
    def heartbeat(self, *, lease_id: str, ttl_seconds: int) -> Lease:
        """Name the renewal operation explicitly for heartbeat-oriented callers."""

        return self.renew(lease_id=lease_id, ttl_seconds=ttl_seconds)

    @_redact_lifecycle_traceback
    def release(self, *, lease_id: str, reason: str = "completed") -> Lease:
        """Persist the first release; later calls return that historical result."""

        return self._repository.release(
            {
                "lease_id": lease_id,
                "released_at": self._now(),
                "release_reason": reason,
            }
        )

    @_redact_lifecycle_traceback
    def active_for_worker(self, *, worker_id: str) -> tuple[Lease, ...]:
        """Evaluate authoritative active use without persisting a counter."""

        return tuple(self._repository.list_active(worker_id=worker_id, observed_at=self._now()))

    def _now(self) -> int:
        invalid_clock = False
        try:
            value = self._clock()
            if not isinstance(value, datetime) or value.tzinfo is None:
                invalid_clock = True
            elif value.utcoffset() != UTC.utcoffset(value):
                invalid_clock = True
            else:
                delta = value - _EPOCH
                timestamp = (
                    delta.days * 86_400 + delta.seconds
                ) * _MICROSECONDS_PER_SECOND + delta.microseconds
                if timestamp < 0 or timestamp > _MAX_SQLITE_INTEGER:
                    invalid_clock = True
        except Exception:
            invalid_clock = True
        if invalid_clock:
            raise PersistenceOperationError("UTC-Zeitbasis konnte nicht sicher bestimmt werden.")
        return timestamp


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _expiry_from_ttl(observed_at: int, ttl_seconds: int) -> int:
    if type(ttl_seconds) is not int or ttl_seconds <= 0:
        raise PersistenceValidationError(
            "Persistenzvalidierung fehlgeschlagen: Feld 'ttl_seconds', Regel 'positive_integer'."
        )
    ttl_microseconds = ttl_seconds * _MICROSECONDS_PER_SECOND
    if ttl_microseconds > _MAX_SQLITE_INTEGER - observed_at:
        raise PersistenceValidationError(
            "Persistenzvalidierung fehlgeschlagen: Feld 'ttl_seconds', Regel 'utc_epoch_range'."
        )
    return observed_at + ttl_microseconds
