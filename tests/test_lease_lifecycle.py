from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import func, select

from oasix.config import BootstrapSettings, RuntimeConfig
from oasix.persistence import (
    Lease,
    LeaseConflictError,
    LeaseInactiveError,
    LeaseLifecycle,
    PersistenceDatabase,
    PersistenceLockingError,
    PersistenceRepositories,
    PersistenceValidationError,
    initialize_persistence,
)

WORKER_ID = "worker-primary"
OTHER_WORKER_ID = "worker-secondary"
LEASE_ID = "20000000-0000-4000-8000-000000000001"
SECOND_LEASE_ID = "20000000-0000-4000-8000-000000000002"
THIRD_LEASE_ID = "20000000-0000-4000-8000-000000000003"
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _clock(seconds: int) -> Any:
    instant = BASE_TIME + timedelta(seconds=seconds)
    return lambda: instant


def _worker(worker_id: str) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "state": "UNKNOWN",
        "observed_at": None,
        "state_changed_at": 1,
        "updated_at": 1,
        "idle_since": None,
        "last_ready_at": None,
        "last_error_class": None,
        "last_error_code": None,
        "version": 1,
    }


def _prepare_workers(database: PersistenceDatabase, *worker_ids: str) -> None:
    with database.transaction() as session:
        repositories = PersistenceRepositories(session)
        for worker_id in worker_ids:
            repositories.worker_states.add(_worker(worker_id))


def test_acquire_persists_uuid4_identity_and_ttl(migrated_database: PersistenceDatabase) -> None:
    _prepare_workers(migrated_database, WORKER_ID)

    with migrated_database.transaction() as session:
        lifecycle = LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0))
        lease = lifecycle.acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="llm-request",
            purpose="inference",
            ttl_seconds=30,
        )
        assert lease.lease_id == LEASE_ID
        assert lease.last_heartbeat_at == lease.created_at
        assert lease.expires_at - lease.created_at == 30_000_000

    with migrated_database.session() as session:
        persisted = session.get(Lease, LEASE_ID)
        assert persisted is not None
        assert persisted.owner == "llm-request"
        assert persisted.purpose == "inference"


def test_acquire_generates_distinct_uuid4_identities(
    migrated_database: PersistenceDatabase,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)

    with migrated_database.transaction() as session:
        lifecycle = LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0))
        first = lifecycle.acquire(
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )
        second = lifecycle.acquire(
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )

    assert first.lease_id != second.lease_id


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("owner", "Owner With Spaces"), ("purpose", "purpose/with/path")],
)
def test_acquire_rejects_invalid_owner_and_purpose(
    migrated_database: PersistenceDatabase,
    field_name: str,
    value: str,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    arguments = {
        "lease_id": LEASE_ID,
        "worker_id": WORKER_ID,
        "owner": "control-plane",
        "purpose": "inference",
        "ttl_seconds": 30,
    }
    arguments[field_name] = value

    with pytest.raises(PersistenceValidationError):
        with migrated_database.transaction() as session:
            LeaseLifecycle(
                PersistenceRepositories(session).leases,
                clock=_clock(0),
            ).acquire(**arguments)


def test_repeated_acquire_is_idempotent_before_and_after_renew(
    migrated_database: PersistenceDatabase,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    with migrated_database.transaction() as session:
        lifecycle = LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0))
        first = lifecycle.acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )

    with migrated_database.transaction() as session:
        lifecycle = LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(1))
        repeated = lifecycle.acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )
        assert repeated.created_at == first.created_at
        assert repeated.expires_at == first.expires_at

    with migrated_database.transaction() as session:
        renewed = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(10),
        ).renew(lease_id=LEASE_ID, ttl_seconds=60)
        renewed_expiry = renewed.expires_at

    with migrated_database.transaction() as session:
        repeated_after_renew = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(11),
        ).acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )
        assert repeated_after_renew.last_heartbeat_at == renewed.last_heartbeat_at
        assert repeated_after_renew.expires_at == renewed_expiry


def test_repeated_acquire_does_not_treat_ttl_as_persisted_identity(
    migrated_database: PersistenceDatabase,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    with migrated_database.transaction() as session:
        original = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(0),
        ).acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )
        original_expiry = original.expires_at

    with migrated_database.transaction() as session:
        repeated = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(1),
        ).acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=60,
        )
        assert repeated.expires_at == original_expiry


@pytest.mark.parametrize(
    ("field_name", "conflicting_value"),
    [("owner", "different-owner"), ("purpose", "development")],
)
def test_repeated_acquire_rejects_changed_immutable_identity(
    migrated_database: PersistenceDatabase,
    field_name: str,
    conflicting_value: str,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    with migrated_database.transaction() as session:
        LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0)).acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )
    with migrated_database.transaction() as session:
        LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(10),
        ).renew(lease_id=LEASE_ID, ttl_seconds=60)

    arguments = {
        "lease_id": LEASE_ID,
        "worker_id": WORKER_ID,
        "owner": "control-plane",
        "purpose": "inference",
        "ttl_seconds": 30,
    }
    arguments[field_name] = conflicting_value
    with pytest.raises(LeaseConflictError):
        with migrated_database.transaction() as session:
            LeaseLifecycle(
                PersistenceRepositories(session).leases,
                clock=_clock(11),
            ).acquire(**arguments)


def test_renew_extends_active_lease_and_never_shortens_expiry(
    migrated_database: PersistenceDatabase,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    with migrated_database.transaction() as session:
        LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0)).acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )

    with migrated_database.transaction() as session:
        lease = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(10),
        ).heartbeat(lease_id=LEASE_ID, ttl_seconds=30)
        extended_expiry = lease.expires_at
        assert lease.last_heartbeat_at - lease.created_at == 10_000_000

    with migrated_database.transaction() as session:
        lease = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(11),
        ).renew(lease_id=LEASE_ID, ttl_seconds=1)
        assert lease.expires_at == extended_expiry


def test_expired_or_released_lease_cannot_be_renewed_or_reacquired(
    migrated_database: PersistenceDatabase,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    with migrated_database.transaction() as session:
        LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0)).acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=10,
        )

    with pytest.raises(LeaseInactiveError):
        with migrated_database.transaction() as session:
            LeaseLifecycle(
                PersistenceRepositories(session).leases,
                clock=_clock(10),
            ).renew(lease_id=LEASE_ID, ttl_seconds=10)

    with pytest.raises(LeaseInactiveError):
        with migrated_database.transaction() as session:
            LeaseLifecycle(
                PersistenceRepositories(session).leases,
                clock=_clock(10),
            ).acquire(
                lease_id=LEASE_ID,
                worker_id=WORKER_ID,
                owner="control-plane",
                purpose="inference",
                ttl_seconds=10,
            )

    with migrated_database.transaction() as session:
        lifecycle = LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(20))
        lifecycle.acquire(
            lease_id=SECOND_LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="development",
            ttl_seconds=10,
        )
        lifecycle.release(lease_id=SECOND_LEASE_ID, reason="completed")

    with pytest.raises(LeaseInactiveError):
        with migrated_database.transaction() as session:
            LeaseLifecycle(
                PersistenceRepositories(session).leases,
                clock=_clock(21),
            ).renew(lease_id=SECOND_LEASE_ID, ttl_seconds=10)

    with pytest.raises(LeaseInactiveError):
        with migrated_database.transaction() as session:
            LeaseLifecycle(
                PersistenceRepositories(session).leases,
                clock=_clock(21),
            ).acquire(
                lease_id=SECOND_LEASE_ID,
                worker_id=WORKER_ID,
                owner="control-plane",
                purpose="development",
                ttl_seconds=10,
            )


def test_release_is_idempotent_and_preserves_first_history(
    migrated_database: PersistenceDatabase,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    with migrated_database.transaction() as session:
        LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0)).acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )

    with migrated_database.transaction() as session:
        first = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(5),
        ).release(lease_id=LEASE_ID, reason="completed")
        first_release = first.released_at

    with migrated_database.transaction() as session:
        repeated = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(6),
        ).release(lease_id=LEASE_ID, reason="cancelled")
        assert repeated.released_at == first_release
        assert repeated.release_reason == "completed"


def test_active_query_has_strict_expiry_boundary_and_worker_isolation(
    migrated_database: PersistenceDatabase,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID, OTHER_WORKER_ID)
    with migrated_database.transaction() as session:
        lifecycle = LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0))
        lifecycle.acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=10,
        )
        lifecycle.acquire(
            lease_id=SECOND_LEASE_ID,
            worker_id=WORKER_ID,
            owner="developer",
            purpose="development",
            ttl_seconds=20,
        )
        lifecycle.acquire(
            lease_id=THIRD_LEASE_ID,
            worker_id=OTHER_WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=20,
        )

    with migrated_database.transaction() as session:
        LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(5),
        ).release(lease_id=LEASE_ID, reason="completed")

    with migrated_database.session() as session:
        lifecycle = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(5),
        )
        assert [lease.lease_id for lease in lifecycle.active_for_worker(worker_id=WORKER_ID)] == [
            SECOND_LEASE_ID
        ]
        assert [
            lease.lease_id for lease in lifecycle.active_for_worker(worker_id=OTHER_WORKER_ID)
        ] == [THIRD_LEASE_ID]

    with migrated_database.session() as session:
        active = LeaseLifecycle(
            PersistenceRepositories(session).leases,
            clock=_clock(10),
        ).active_for_worker(worker_id=WORKER_ID)
        assert [lease.lease_id for lease in active] == [SECOND_LEASE_ID]


def test_concurrent_repeated_acquire_creates_one_lease(
    migrated_database: PersistenceDatabase,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    barrier = Barrier(4)

    def acquire() -> str | type[PersistenceLockingError]:
        barrier.wait()
        try:
            with migrated_database.transaction() as session:
                lease = LeaseLifecycle(
                    PersistenceRepositories(session).leases,
                    clock=_clock(0),
                ).acquire(
                    lease_id=LEASE_ID,
                    worker_id=WORKER_ID,
                    owner="control-plane",
                    purpose="inference",
                    ttl_seconds=30,
                )
                return lease.lease_id
        except PersistenceLockingError:
            return PersistenceLockingError

    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(lambda _: acquire(), range(4)))
    assert LEASE_ID in outcomes
    assert all(outcome in {LEASE_ID, PersistenceLockingError} for outcome in outcomes)

    for outcome in outcomes:
        if outcome is PersistenceLockingError:
            with migrated_database.transaction() as session:
                repeated = LeaseLifecycle(
                    PersistenceRepositories(session).leases,
                    clock=_clock(1),
                ).acquire(
                    lease_id=LEASE_ID,
                    worker_id=WORKER_ID,
                    owner="control-plane",
                    purpose="inference",
                    ttl_seconds=30,
                )
                assert repeated.lease_id == LEASE_ID

    with migrated_database.session() as session:
        assert session.scalar(select(func.count()).select_from(Lease)) == 1


def test_concurrent_renewals_do_not_lose_newer_heartbeat(
    migrated_database: PersistenceDatabase,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    with migrated_database.transaction() as session:
        LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0)).acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=60,
        )
    barrier = Barrier(2)

    def renew(seconds: int) -> tuple[int, type[Exception] | None]:
        barrier.wait()
        try:
            with migrated_database.transaction() as session:
                LeaseLifecycle(
                    PersistenceRepositories(session).leases,
                    clock=_clock(seconds),
                ).renew(lease_id=LEASE_ID, ttl_seconds=60)
        except (LeaseConflictError, PersistenceLockingError) as error:
            return seconds, type(error)
        return seconds, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(renew, (10, 20)))
    assert all(
        outcome in {None, LeaseConflictError, PersistenceLockingError} for _, outcome in outcomes
    )
    successful_seconds = [seconds for seconds, outcome in outcomes if outcome is None]
    assert successful_seconds

    with migrated_database.session() as session:
        lease = session.get(Lease, LEASE_ID)
        assert lease is not None
        base_microseconds = int(
            (BASE_TIME - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1_000_000
        )
        assert lease.last_heartbeat_at == base_microseconds + max(successful_seconds) * 1_000_000


def test_transaction_failure_rolls_back_acquire(migrated_database: PersistenceDatabase) -> None:
    _prepare_workers(migrated_database, WORKER_ID)

    with pytest.raises(RuntimeError, match="later operation failed"):
        with migrated_database.transaction() as session:
            LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0)).acquire(
                lease_id=LEASE_ID,
                worker_id=WORKER_ID,
                owner="control-plane",
                purpose="inference",
                ttl_seconds=30,
            )
            raise RuntimeError("later operation failed")

    with migrated_database.session() as session:
        assert session.get(Lease, LEASE_ID) is None


def test_active_lease_survives_database_reinitialization(
    migrated_database: PersistenceDatabase,
    valid_config_data: dict[str, Any],
    secret_directory: Path,
    tmp_path: Path,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    with migrated_database.transaction() as session:
        LeaseLifecycle(PersistenceRepositories(session).leases, clock=_clock(0)).acquire(
            lease_id=LEASE_ID,
            worker_id=WORKER_ID,
            owner="control-plane",
            purpose="inference",
            ttl_seconds=30,
        )
    migrated_database.close()

    reopened = initialize_persistence(
        RuntimeConfig.model_validate(valid_config_data),
        BootstrapSettings(
            config_file=tmp_path / "unused-runtime.yaml",
            secrets_directory=secret_directory,
        ),
    )
    try:
        with reopened.session() as session:
            active = LeaseLifecycle(
                PersistenceRepositories(session).leases,
                clock=_clock(1),
            ).active_for_worker(worker_id=WORKER_ID)
            assert [lease.lease_id for lease in active] == [LEASE_ID]
    finally:
        reopened.close()


def test_lifecycle_errors_do_not_expose_rejected_values(
    migrated_database: PersistenceDatabase,
    caplog: pytest.LogCaptureFixture,
    production_traceback_locals: Any,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    sentinel = "SENTINEL-PRIVATE-LEASE-SECRET"

    with pytest.raises(PersistenceValidationError) as captured:
        with migrated_database.transaction() as session:
            LeaseLifecycle(
                PersistenceRepositories(session).leases,
                clock=_clock(0),
            ).acquire(
                lease_id=LEASE_ID,
                worker_id=WORKER_ID,
                owner=sentinel,
                purpose="inference",
                ttl_seconds=30,
            )

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in caplog.text
    assert sentinel not in production_traceback_locals(captured.value)


@pytest.mark.parametrize("ttl_seconds", [0, -1, True])
def test_ttl_must_be_a_positive_integer(
    migrated_database: PersistenceDatabase,
    ttl_seconds: Any,
) -> None:
    _prepare_workers(migrated_database, WORKER_ID)
    with pytest.raises(PersistenceValidationError, match="ttl_seconds"):
        with migrated_database.transaction() as session:
            LeaseLifecycle(
                PersistenceRepositories(session).leases,
                clock=_clock(0),
            ).acquire(
                lease_id=LEASE_ID,
                worker_id=WORKER_ID,
                owner="control-plane",
                purpose="inference",
                ttl_seconds=ttl_seconds,
            )
