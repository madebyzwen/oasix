"""Short-transaction persistent lease adapter and heartbeat session."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Protocol

from oasix.llm.errors import (
    InferenceLeaseAcquireError,
    InferenceLeaseReleaseError,
    InferenceLeaseRenewalError,
)
from oasix.persistence import LeaseLifecycle, PersistenceDatabase, PersistenceRepositories
from oasix.persistence.errors import PersistenceError

type MicrosecondClock = Callable[[], int]


class InferenceLeaseRegistry(Protocol):
    """Async boundary around short persistent lease transactions."""

    async def acquire(self, lease_id: str, worker_id: str, ttl_seconds: int) -> None: ...

    async def renew(self, lease_id: str, ttl_seconds: int) -> None: ...

    async def release(self, lease_id: str, reason: str) -> None: ...


class PersistentInferenceLeaseRegistry:
    """Run each lease operation in its own short SQLite transaction."""

    __slots__ = ("_database", "_timestamp")

    def __init__(
        self,
        database: PersistenceDatabase,
        *,
        timestamp: MicrosecondClock | None = None,
    ) -> None:
        self._database = database
        self._timestamp = timestamp or _utc_epoch_microseconds

    def __repr__(self) -> str:
        return "PersistentInferenceLeaseRegistry(database=<configured>)"

    def ensure_worker(self, worker_id: str) -> None:
        """Register a configured worker as UNKNOWN before serving requests."""

        try:
            with self._database.transaction() as session:
                repositories = PersistenceRepositories(session)
                if repositories.worker_states.get(worker_id) is None:
                    observed_at = self._timestamp()
                    repositories.worker_states.add(
                        {
                            "worker_id": worker_id,
                            "state": "UNKNOWN",
                            "observed_at": None,
                            "state_changed_at": observed_at,
                            "updated_at": observed_at,
                            "idle_since": None,
                            "last_ready_at": None,
                            "last_error_class": None,
                            "last_error_code": None,
                            "version": 1,
                        }
                    )
        except PersistenceError:
            raise InferenceLeaseAcquireError from None

    async def acquire(self, lease_id: str, worker_id: str, ttl_seconds: int) -> None:
        try:
            await asyncio.to_thread(self._acquire, lease_id, worker_id, ttl_seconds)
        except PersistenceError:
            raise InferenceLeaseAcquireError from None

    async def renew(self, lease_id: str, ttl_seconds: int) -> None:
        try:
            await asyncio.to_thread(self._renew, lease_id, ttl_seconds)
        except PersistenceError:
            raise InferenceLeaseRenewalError from None

    async def release(self, lease_id: str, reason: str) -> None:
        try:
            await asyncio.to_thread(self._release, lease_id, reason)
        except PersistenceError:
            raise InferenceLeaseReleaseError from None

    def _acquire(self, lease_id: str, worker_id: str, ttl_seconds: int) -> None:
        with self._database.transaction() as session:
            LeaseLifecycle(PersistenceRepositories(session).leases).acquire(
                lease_id=lease_id,
                worker_id=worker_id,
                owner="llm-gateway",
                purpose="inference",
                ttl_seconds=ttl_seconds,
            )

    def _renew(self, lease_id: str, ttl_seconds: int) -> None:
        with self._database.transaction() as session:
            LeaseLifecycle(PersistenceRepositories(session).leases).heartbeat(
                lease_id=lease_id,
                ttl_seconds=ttl_seconds,
            )

    def _release(self, lease_id: str, reason: str) -> None:
        with self._database.transaction() as session:
            LeaseLifecycle(PersistenceRepositories(session).leases).release(
                lease_id=lease_id,
                reason=reason,
            )


class InferenceLeaseSession:
    """Hold one persistent lease and renew it for the full enclosed usage."""

    __slots__ = (
        "_heartbeat_interval",
        "_heartbeat_task",
        "_lease_id",
        "_owner_task",
        "_registry",
        "_renewal_failed",
        "_stop",
        "_ttl_seconds",
        "_worker_id",
    )

    def __init__(
        self,
        registry: InferenceLeaseRegistry,
        worker_id: str,
        ttl_seconds: int,
        heartbeat_interval_seconds: float,
    ) -> None:
        self._registry = registry
        self._worker_id = worker_id
        self._ttl_seconds = ttl_seconds
        self._heartbeat_interval = heartbeat_interval_seconds
        self._lease_id = str(uuid.uuid4())
        self._stop = asyncio.Event()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._owner_task: asyncio.Task[object] | None = None
        self._renewal_failed = False

    @property
    def lease_id(self) -> str:
        return self._lease_id

    async def __aenter__(self) -> InferenceLeaseSession:
        await self._registry.acquire(self._lease_id, self._worker_id, self._ttl_seconds)
        owner = asyncio.current_task()
        if owner is None:  # pragma: no cover - asyncio always owns a running coroutine
            await self._registry.release(self._lease_id, "startup-error")
            raise InferenceLeaseAcquireError from None
        self._owner_task = owner
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object,
    ) -> bool:
        self._stop.set()
        heartbeat = self._heartbeat_task
        if heartbeat is not None:
            await heartbeat

        reason = "completed" if exception_type is None else "cancelled"
        try:
            await self._registry.release(self._lease_id, reason)
        except Exception:
            raise InferenceLeaseReleaseError from None
        if self._renewal_failed:
            raise InferenceLeaseRenewalError from None
        return False

    def ensure_healthy(self) -> None:
        if self._renewal_failed:
            raise InferenceLeaseRenewalError from None

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._heartbeat_interval,
                )
            except TimeoutError:
                pass
            if self._stop.is_set():
                return
            try:
                await self._registry.renew(self._lease_id, self._ttl_seconds)
            except Exception:
                self._renewal_failed = True
                owner = self._owner_task
                if owner is not None:
                    owner.cancel()
                return


def _utc_epoch_microseconds() -> int:
    return time.time_ns() // 1_000
