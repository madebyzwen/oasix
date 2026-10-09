from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, PrimaryKeyConstraint, inspect
from sqlalchemy.dialects import sqlite

from oasix.persistence import Attempt, Base, ControlState, Job, JobEvent, Lease, WorkerState

EXPECTED_TABLES = {
    "attempts",
    "control_state",
    "job_events",
    "jobs",
    "leases",
    "worker_states",
}

EXPECTED_RELATIONSHIPS = {
    Job: {"parent", "children", "attempts", "leases", "events"},
    Attempt: {"job", "worker", "leases", "events"},
    Lease: {"worker", "job", "attempt"},
    WorkerState: {"attempts", "leases"},
    JobEvent: {"job", "attempt"},
    ControlState: set(),
}


def test_metadata_contains_exactly_six_domain_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_models_expose_documented_relationships() -> None:
    for model, expected in EXPECTED_RELATIONSHIPS.items():
        assert set(inspect(model).relationships.keys()) == expected


def test_migrated_columns_match_model_metadata(
    migrated_database: Any,
) -> None:
    dialect = sqlite.dialect()
    with migrated_database.connection() as connection:
        inspector = inspect(connection)
        for table_name, table in Base.metadata.tables.items():
            actual_columns = {
                column["name"]: column for column in inspector.get_columns(table_name)
            }
            assert set(actual_columns) == set(table.columns.keys())
            for column in table.columns:
                actual = actual_columns[column.name]
                assert actual["nullable"] is column.nullable
                assert actual["type"].compile(dialect=dialect) == column.type.compile(
                    dialect=dialect
                )


def test_migrated_constraint_and_index_names_match_metadata(
    migrated_database: Any,
) -> None:
    with migrated_database.connection() as connection:
        inspector = inspect(connection)
        for table_name, table in Base.metadata.tables.items():
            expected_checks = {
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            }
            expected_foreign_keys = {
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, ForeignKeyConstraint)
            }
            expected_primary_key = next(
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, PrimaryKeyConstraint)
            )
            expected_uniques = {
                constraint.name
                for constraint in table.constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            }
            expected_indexes = {index.name for index in table.indexes}

            assert {item["name"] for item in inspector.get_check_constraints(table_name)} == (
                expected_checks
            )
            actual_foreign_keys = inspector.get_foreign_keys(table_name)
            assert {item["name"] for item in actual_foreign_keys} == expected_foreign_keys
            assert {item["options"].get("ondelete") for item in actual_foreign_keys} <= {"RESTRICT"}
            assert inspector.get_pk_constraint(table_name)["name"] == expected_primary_key
            assert {item["name"] for item in inspector.get_unique_constraints(table_name)} == (
                expected_uniques
            )
            assert {item["name"] for item in inspector.get_indexes(table_name)} == expected_indexes


def test_all_foreign_keys_use_delete_restrict() -> None:
    for table in Base.metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            assert constraint.ondelete == "RESTRICT"
