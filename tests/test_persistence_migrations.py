from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from alembic import command
from oasix.config import BootstrapSettings, RuntimeConfig
from oasix.persistence import (
    EXPECTED_SCHEMA_REVISION,
    Base,
    PersistenceMigrationError,
    PersistenceRepositories,
    initialize_persistence,
)
from oasix.persistence.migrations import verify_database_integrity, verify_schema_revision

EXPECTED_ALL_TABLES = {
    "alembic_version",
    "attempts",
    "control_state",
    "job_events",
    "jobs",
    "leases",
    "worker_states",
}
EXPECTED_REVISION = EXPECTED_SCHEMA_REVISION


def _run_command(config: Config, database: Any, operation: str, revision: str) -> None:
    with database.connection() as connection:
        config.attributes["connection"] = connection
        getattr(command, operation)(config, revision)
    config.attributes.pop("connection", None)


def test_upgrade_creates_exact_schema_singleton_and_revision(
    migrated_database: Any,
    alembic_config: Config,
) -> None:
    scripts = ScriptDirectory.from_config(alembic_config)
    assert scripts.get_heads() == [EXPECTED_REVISION]

    with migrated_database.connection() as connection:
        assert set(inspect(connection).get_table_names()) == EXPECTED_ALL_TABLES
        singleton = connection.execute(text("SELECT * FROM control_state")).mappings().one()
        assert singleton["singleton_id"] == 1
        assert singleton["dispatch_mode"] == "PAUSED_RECOVERY"
        assert singleton["recovery_status"] == "REQUIRED"
        assert singleton["process_instance_id"] is None
        assert singleton["started_at"] is None
        assert singleton["updated_at"] > 0
        assert singleton["last_clean_shutdown_at"] is None
        assert singleton["version"] == 1
        assert MigrationContext.configure(connection).get_current_revision() == EXPECTED_REVISION


def test_repeated_upgrade_is_idempotent(
    migrated_database: Any,
    alembic_config: Config,
) -> None:
    with migrated_database.connection() as connection:
        schema_before = connection.execute(
            text(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        ).all()
        singleton_before = connection.execute(text("SELECT * FROM control_state")).one()

    _run_command(alembic_config, migrated_database, "upgrade", "head")

    with migrated_database.connection() as connection:
        schema_after = connection.execute(
            text(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        ).all()
        singleton_after = connection.execute(text("SELECT * FROM control_state")).one()
    assert schema_after == schema_before
    assert singleton_after == singleton_before


def test_downgrade_to_base_and_reupgrade(
    migrated_database: Any,
    alembic_config: Config,
) -> None:
    _run_command(alembic_config, migrated_database, "downgrade", "base")
    with migrated_database.connection() as connection:
        assert inspect(connection).get_table_names() == ["alembic_version"]
        assert MigrationContext.configure(connection).get_current_revision() is None

    _run_command(alembic_config, migrated_database, "upgrade", "head")
    with migrated_database.connection() as connection:
        assert set(inspect(connection).get_table_names()) == EXPECTED_ALL_TABLES
        assert connection.execute(text("SELECT count(*) FROM control_state")).scalar_one() == 1
        assert MigrationContext.configure(connection).get_current_revision() == EXPECTED_REVISION


def test_integrity_checks_and_foreign_keys_on_distinct_connections(
    migrated_database: Any,
) -> None:
    with migrated_database.connection() as first, migrated_database.connection() as second:
        assert first.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert second.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert first.execute(text("PRAGMA foreign_key_check")).all() == []
        assert first.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
        verify_database_integrity(first)
        verify_schema_revision(second)


def test_revision_check_rejects_missing_version_table_without_creating_it(
    persistence_database: Any,
) -> None:
    with persistence_database.connection() as connection:
        with pytest.raises(PersistenceMigrationError, match="Revision"):
            verify_schema_revision(connection)
        assert "alembic_version" not in inspect(connection).get_table_names()


@pytest.mark.parametrize("revision", ["0000_stale", "unknown_revision"])
def test_revision_check_rejects_incompatible_revision_before_domain_writes(
    migrated_database: Any,
    revision: str,
) -> None:
    with migrated_database.connection() as connection, connection.begin():
        connection.execute(
            text("UPDATE alembic_version SET version_num = :revision"), {"revision": revision}
        )

    with pytest.raises(PersistenceMigrationError, match="Revision"):
        with migrated_database.transaction() as session:
            PersistenceRepositories(session)

    with migrated_database.connection() as connection:
        assert connection.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == revision
        )


def test_integrity_failure_is_reported_without_row_contents(migrated_database: Any) -> None:
    sentinel = "SENTINEL-PRIVATE-BROKEN-REFERENCE"
    with migrated_database.connection() as connection:
        raw_connection = connection.connection.driver_connection
        previous_autocommit = raw_connection.autocommit
        try:
            raw_connection.autocommit = True
            raw_connection.execute("PRAGMA foreign_keys=OFF")
            raw_connection.execute(
                "INSERT INTO attempts ("
                "attempt_id, job_id, attempt_number, worker_id, status, "
                "created_at, updated_at, version"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "10000000-0000-4000-8000-000000000099",
                    sentinel,
                    1,
                    "missing-worker",
                    "PENDING",
                    1,
                    1,
                    1,
                ),
            )
            raw_connection.execute("PRAGMA foreign_keys=ON")
        finally:
            raw_connection.autocommit = previous_autocommit

        with pytest.raises(PersistenceMigrationError) as captured:
            verify_database_integrity(connection)

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


def test_migrated_data_persists_after_engine_recreation_and_in_subprocess(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
    alembic_config: Config,
) -> None:
    runtime = RuntimeConfig.model_validate(valid_config_data)
    bootstrap = BootstrapSettings(
        config_file=tmp_path / "unused.yaml",
        secrets_directory=secret_directory,
    )
    first = initialize_persistence(runtime, bootstrap)
    _run_command(alembic_config, first, "upgrade", "head")
    with first.transaction() as session:
        session.execute(
            Base.metadata.tables["worker_states"].insert(),
            {
                "worker_id": "worker-restart",
                "state": "UNKNOWN",
                "state_changed_at": 1,
                "updated_at": 1,
                "version": 1,
            },
        )
    first.close()

    second = initialize_persistence(runtime, bootstrap)
    try:
        with second.connection() as connection:
            assert (
                connection.execute(
                    text("SELECT state FROM worker_states WHERE worker_id = 'worker-restart'")
                ).scalar_one()
                == "UNKNOWN"
            )
    finally:
        second.close()

    database_path = valid_config_data["persistence"]["database_path"]
    script = (
        "import sqlite3, sys; "
        "connection = sqlite3.connect(sys.argv[1]); "
        "row = connection.execute("
        "\"SELECT state FROM worker_states WHERE worker_id = 'worker-restart'\""
        ").fetchone(); "
        "connection.close(); "
        "raise SystemExit(0 if row == ('UNKNOWN',) else 1)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, database_path],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_actual_alembic_cli_uses_validated_external_configuration(
    valid_config_data: dict[str, Any],
    tmp_path: Path,
    secret_directory: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(yaml.safe_dump(valid_config_data), encoding="utf-8")
    environment = os.environ.copy()
    environment["OASIX_CONFIG_FILE"] = str(config_path)
    environment["OASIX_SECRETS_DIRECTORY"] = str(secret_directory)

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=repository_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    database_path = Path(valid_config_data["persistence"]["database_path"])
    assert database_path.exists()
