"""Bounded SQLAlchemy engine, sessions, and transactions for local SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from oasix.config import BootstrapSettings, RuntimeConfig
from oasix.persistence.errors import (
    PersistenceConfigurationError,
    PersistenceConnectionError,
    PersistenceInitializationError,
)
from oasix.persistence.paths import (
    PreparedDatabasePath,
    database_path_identity_matches,
    prepare_database_path,
)

SQLITE_POOL_SIZE = 5
SQLITE_MAX_OVERFLOW = 0
SQLITE_POOL_TIMEOUT_SECONDS = 5

type _InitializationStatus = Literal["ok", "connection", "initialization"]


class _PragmaInitializationError(RuntimeError):
    """Internal marker for a required SQLite setting that did not take effect."""


class PersistenceDatabase:
    """Own one process-scoped engine and create only short-lived sessions."""

    __slots__ = ("_closed", "_engine", "_session_factory")

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(
            bind=engine,
            class_=Session,
            expire_on_commit=False,
        )
        self._closed = False

    def __repr__(self) -> str:
        return f"PersistenceDatabase(closed={self._closed})"

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        """Provide a short-lived connection for infrastructure operations."""

        self._require_open()
        with self._engine.connect() as connection:
            yield connection

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Provide a short-lived session without an implicit commit."""

        self._require_open()
        with self._session_factory() as session:
            yield session

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Commit a short transaction on success and roll it back on failure."""

        self._require_open()
        with self._session_factory() as session, session.begin():
            yield session

    def close(self) -> None:
        """Dispose pooled connections; repeated calls are harmless."""

        if not self._closed:
            self._engine.dispose()
            self._closed = True

    def __enter__(self) -> PersistenceDatabase:
        self._require_open()
        return self

    def __exit__(self, _exception_type: Any, _exception: Any, _traceback: Any) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise PersistenceInitializationError("Persistenzressource ist bereits geschlossen.")


def initialize_persistence(
    runtime: RuntimeConfig,
    bootstrap: BootstrapSettings,
) -> PersistenceDatabase:
    """Validate storage and initialize one fully verified SQLite engine."""

    if runtime.schema_version != 2:
        raise PersistenceConfigurationError(
            "Persistenzinitialisierung erfordert Runtime-Konfiguration Version 2."
        )
    prepared = prepare_database_path(runtime.persistence, bootstrap)
    database, status = _initialize_database(prepared, runtime.persistence.busy_timeout_ms)
    if database is not None:
        return database
    _raise_initialization_error(status)


def _initialize_database(
    prepared: PreparedDatabasePath,
    busy_timeout_ms: int,
) -> tuple[PersistenceDatabase | None, _InitializationStatus]:
    engine: Engine | None = None
    try:
        engine = _create_sqlite_engine(prepared, busy_timeout_ms)
        with engine.connect():
            pass
    except _PragmaInitializationError:
        if engine is not None:
            engine.dispose()
        return None, "initialization"
    except (SQLAlchemyError, sqlite3.Error, OSError):
        if engine is not None:
            engine.dispose()
        return None, "connection"

    if not database_path_identity_matches(prepared):
        engine.dispose()
        return None, "initialization"
    return PersistenceDatabase(engine), "ok"


def _create_sqlite_engine(prepared: PreparedDatabasePath, busy_timeout_ms: int) -> Engine:
    url = URL.create("sqlite+pysqlite", database=str(prepared.path))
    engine = create_engine(
        url,
        connect_args={"autocommit": False},
        hide_parameters=True,
        poolclass=QueuePool,
        pool_size=SQLITE_POOL_SIZE,
        max_overflow=SQLITE_MAX_OVERFLOW,
        pool_timeout=SQLITE_POOL_TIMEOUT_SECONDS,
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
        if not isinstance(dbapi_connection, sqlite3.Connection) or not hasattr(
            dbapi_connection, "autocommit"
        ):
            raise _PragmaInitializationError
        if not database_path_identity_matches(prepared):
            raise _PragmaInitializationError

        previous_autocommit = dbapi_connection.autocommit
        cursor = None
        try:
            dbapi_connection.autocommit = True
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            journal_mode = cursor.fetchone()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            _verify_required_sqlite_json(cursor)

            cursor.execute("PRAGMA foreign_keys")
            foreign_keys = cursor.fetchone()
            cursor.execute("PRAGMA synchronous")
            synchronous = cursor.fetchone()
            cursor.execute("PRAGMA busy_timeout")
            busy_timeout = cursor.fetchone()
        except sqlite3.Error as error:
            raise _PragmaInitializationError from error
        finally:
            if cursor is not None:
                cursor.close()
            dbapi_connection.autocommit = previous_autocommit

        if (
            journal_mode is None
            or str(journal_mode[0]).lower() != "wal"
            or foreign_keys != (1,)
            or synchronous != (2,)
            or busy_timeout != (busy_timeout_ms,)
        ):
            raise _PragmaInitializationError

    return engine


def _verify_required_sqlite_json(cursor: sqlite3.Cursor) -> None:
    """Fail closed unless SQLite's JSON validation function behaves correctly."""

    cursor.execute("SELECT json_valid('{}'), json_valid('{')")
    if cursor.fetchone() != (1, 0):
        raise _PragmaInitializationError


def _raise_initialization_error(status: _InitializationStatus) -> None:
    if status == "connection":
        raise PersistenceConnectionError(
            "SQLite-Verbindung konnte nicht sicher hergestellt werden."
        )
    raise PersistenceInitializationError(
        "Erforderliche SQLite-Einstellungen konnten nicht aktiviert und verifiziert werden."
    )
