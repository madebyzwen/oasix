from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from oasix.config import BootstrapSettings, RuntimeConfig
from oasix.persistence import (
    PersistenceConfigurationError,
    PersistenceInitializationError,
    PersistenceIntegrityError,
    PersistenceLockingError,
    initialize_persistence,
)
from oasix.persistence import database as database_module
from oasix.persistence.database import (
    SQLITE_MAX_OVERFLOW,
    SQLITE_POOL_SIZE,
    SQLITE_POOL_TIMEOUT_SECONDS,
)


def _runtime(data: dict[str, Any]) -> RuntimeConfig:
    return RuntimeConfig.model_validate(data)


def _bootstrap(tmp_path: Path, secret_directory: Path) -> BootstrapSettings:
    return BootstrapSettings(
        config_file=tmp_path / "unused-runtime.yaml",
        secrets_directory=secret_directory,
    )


def test_persistence_initialization_rejects_legacy_runtime_safely(
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    legacy_runtime = SimpleNamespace(schema_version=1)

    with pytest.raises(PersistenceConfigurationError, match="Version 2") as captured:
        initialize_persistence(legacy_runtime, _bootstrap(tmp_path, secret_directory))  # type: ignore[arg-type]

    assert repr(legacy_runtime) not in str(captured.value)


def test_initializes_verified_sqlite_pragmas_and_bounded_pool(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database = initialize_persistence(
        _runtime(valid_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    try:
        with database.connection() as connection:
            raw_connection = connection.connection.driver_connection
            assert raw_connection.autocommit is False
            assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert connection.execute(text("PRAGMA synchronous")).scalar_one() == 2
            assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000
            assert connection.execute(text("SELECT json_valid('{}')")).scalar_one() == 1
        assert SQLITE_POOL_SIZE == 5
        assert SQLITE_MAX_OVERFLOW == 0
        assert SQLITE_POOL_TIMEOUT_SECONDS == 5
    finally:
        database.close()


def test_persistence_accepts_additive_runtime_schema_version_three(
    auth_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database = initialize_persistence(
        _runtime(auth_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    database.close()


def test_persistence_accepts_additive_runtime_schema_version_four(
    gateway_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database = initialize_persistence(
        _runtime(gateway_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    database.close()


def test_missing_sqlite_json_function_fails_closed(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_cursor: sqlite3.Cursor) -> None:
        raise sqlite3.OperationalError("SENTINEL-PRIVATE-JSON-FAILURE")

    monkeypatch.setattr(database_module, "_verify_required_sqlite_json", unavailable)

    with pytest.raises(PersistenceInitializationError) as captured:
        initialize_persistence(
            _runtime(valid_config_data),
            _bootstrap(tmp_path, secret_directory),
        )

    assert "SENTINEL-PRIVATE-JSON-FAILURE" not in str(captured.value)
    assert "SENTINEL-PRIVATE-JSON-FAILURE" not in repr(captured.value)


def test_applies_configured_busy_timeout(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    valid_config_data["persistence"]["busy_timeout_ms"] = 1_234

    database = initialize_persistence(
        _runtime(valid_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    try:
        with database.connection() as connection:
            assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 1_234
    finally:
        database.close()


def test_busy_timeout_raises_safe_locking_error_without_retry(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    valid_config_data["persistence"]["busy_timeout_ms"] = 25
    database = initialize_persistence(
        _runtime(valid_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    try:
        with database.connection() as connection, connection.begin():
            connection.execute(text("CREATE TABLE lock_test (value INTEGER NOT NULL)"))

        with database.connection() as locking_connection, locking_connection.begin():
            locking_connection.execute(text("INSERT INTO lock_test (value) VALUES (1)"))
            with pytest.raises(PersistenceLockingError) as captured:
                with database.transaction() as session:
                    session.execute(text("INSERT INTO lock_test (value) VALUES (2)"))
            locking_connection.rollback()

        assert "database is locked" not in str(captured.value).lower()
        assert "INSERT" not in repr(captured.value)
        with database.connection() as connection:
            assert connection.execute(text("SELECT count(*) FROM lock_test")).scalar_one() == 0
    finally:
        database.close()


def test_enforces_foreign_keys_on_distinct_connections(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database = initialize_persistence(
        _runtime(valid_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    try:
        with database.connection() as first, database.connection() as second:
            first_raw = first.connection.driver_connection
            second_raw = second.connection.driver_connection
            assert first_raw is not second_raw
            assert first.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert second.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

        with database.connection() as connection, connection.begin():
            connection.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            connection.execute(
                text(
                    "CREATE TABLE child ("
                    "id INTEGER PRIMARY KEY, "
                    "parent_id INTEGER NOT NULL REFERENCES parent(id)"
                    ")"
                )
            )

        with pytest.raises(IntegrityError):
            with database.connection() as connection, connection.begin():
                connection.execute(text("INSERT INTO child (id, parent_id) VALUES (1, 999)"))
    finally:
        database.close()


def test_transaction_commits_on_success_and_rolls_back_on_error(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database = initialize_persistence(
        _runtime(valid_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    try:
        with database.connection() as connection, connection.begin():
            connection.execute(text("CREATE TABLE test_values (value TEXT NOT NULL)"))

        with database.transaction() as session:
            assert session.expire_on_commit is False
            session.execute(text("INSERT INTO test_values (value) VALUES ('committed')"))

        with pytest.raises(RuntimeError, match="rollback marker"):
            with database.transaction() as session:
                session.execute(text("INSERT INTO test_values (value) VALUES ('rolled-back')"))
                raise RuntimeError("rollback marker")

        with database.session() as session:
            values = session.execute(text("SELECT value FROM test_values ORDER BY value")).scalars()
            assert list(values) == ["committed"]
    finally:
        database.close()


def test_sql_error_representation_hides_bound_parameters(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database = initialize_persistence(
        _runtime(valid_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    sentinel = "SENTINEL-PRIVATE-SQL-PARAMETER"
    try:
        with database.connection() as connection, connection.begin():
            connection.execute(text("CREATE TABLE unique_values (value TEXT UNIQUE NOT NULL)"))
            connection.execute(
                text("INSERT INTO unique_values (value) VALUES (:value)"),
                {"value": sentinel},
            )

        with pytest.raises(PersistenceIntegrityError) as captured:
            with database.transaction() as session:
                session.execute(
                    text("INSERT INTO unique_values (value) VALUES (:value)"),
                    {"value": sentinel},
                )

        assert sentinel not in str(captured.value)
        assert sentinel not in repr(captured.value)
    finally:
        database.close()


def test_reopens_existing_database_after_engine_disposal(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    runtime = _runtime(valid_config_data)
    bootstrap = _bootstrap(tmp_path, secret_directory)
    first = initialize_persistence(runtime, bootstrap)
    with first.connection() as connection, connection.begin():
        connection.execute(text("CREATE TABLE restart_value (value INTEGER NOT NULL)"))
        connection.execute(text("INSERT INTO restart_value (value) VALUES (42)"))
    first.close()

    second = initialize_persistence(runtime, bootstrap)
    try:
        with second.connection() as connection:
            assert connection.execute(text("SELECT value FROM restart_value")).scalar_one() == 42
    finally:
        second.close()


def test_initialization_does_not_create_application_schema(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database = initialize_persistence(
        _runtime(valid_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    try:
        with database.connection() as connection:
            tables = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            ).scalars()
            assert list(tables) == []
    finally:
        database.close()


def test_rejects_missing_parent_directory(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    valid_config_data["persistence"]["database_path"] = str(
        tmp_path / "missing-parent" / "oasix.sqlite3"
    )

    with pytest.raises(PersistenceConfigurationError, match="nicht verfügbar"):
        initialize_persistence(
            _runtime(valid_config_data),
            _bootstrap(tmp_path, secret_directory),
        )


def test_rejects_database_path_inside_secret_directory(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    valid_config_data["persistence"]["database_path"] = str(secret_directory / "oasix.sqlite3")

    with pytest.raises(PersistenceConfigurationError, match="Secret-Quelle"):
        initialize_persistence(
            _runtime(valid_config_data),
            _bootstrap(tmp_path, secret_directory),
        )


def test_rejects_symlink_database_target(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database_path = Path(valid_config_data["persistence"]["database_path"])
    target = tmp_path / "outside.sqlite3"
    target.touch(mode=0o600)
    target.chmod(0o600)
    database_path.symlink_to(target)

    with pytest.raises(PersistenceConfigurationError, match="symbolischer Link"):
        initialize_persistence(
            _runtime(valid_config_data),
            _bootstrap(tmp_path, secret_directory),
        )


def test_rejects_non_regular_database_target(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database_path = Path(valid_config_data["persistence"]["database_path"])
    database_path.mkdir(mode=0o700)

    with pytest.raises(PersistenceConfigurationError, match="reguläre Datei"):
        initialize_persistence(
            _runtime(valid_config_data),
            _bootstrap(tmp_path, secret_directory),
        )


@pytest.mark.parametrize("target_kind", ["directory", "file"])
def test_rejects_unsafe_database_permissions(
    target_kind: str,
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database_path = Path(valid_config_data["persistence"]["database_path"])
    if target_kind == "directory":
        database_path.parent.chmod(0o755)
    else:
        database_path.touch(mode=0o644)
        database_path.chmod(0o644)

    with pytest.raises(PersistenceConfigurationError, match="Berechtigungen"):
        initialize_persistence(
            _runtime(valid_config_data),
            _bootstrap(tmp_path, secret_directory),
        )


def test_creates_database_with_restrictive_permissions(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database_path = Path(valid_config_data["persistence"]["database_path"])

    database = initialize_persistence(
        _runtime(valid_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    try:
        assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    finally:
        database.close()


def test_closed_database_rejects_new_resources(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database = initialize_persistence(
        _runtime(valid_config_data),
        _bootstrap(tmp_path, secret_directory),
    )
    database.close()

    with pytest.raises(PersistenceInitializationError, match="geschlossen"):
        with database.connection():
            pass


def test_path_failures_do_not_expose_private_path(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
    production_traceback_locals: Any,
) -> None:
    sentinel = "SENTINEL-PRIVATE-DATABASE-PATH"
    unsafe_directory = tmp_path / sentinel
    unsafe_directory.mkdir(mode=0o755)
    unsafe_directory.chmod(0o755)
    valid_config_data["persistence"]["database_path"] = str(unsafe_directory / "oasix.sqlite3")

    with pytest.raises(PersistenceConfigurationError) as captured:
        initialize_persistence(
            _runtime(valid_config_data),
            _bootstrap(tmp_path, secret_directory),
        )

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in production_traceback_locals(captured.value)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses owner write permissions")
def test_rejects_non_writable_database_directory(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    database_directory = Path(valid_config_data["persistence"]["database_path"]).parent
    database_directory.chmod(0o500)
    try:
        with pytest.raises(PersistenceConfigurationError):
            initialize_persistence(
                _runtime(valid_config_data),
                _bootstrap(tmp_path, secret_directory),
            )
    finally:
        database_directory.chmod(0o700)
