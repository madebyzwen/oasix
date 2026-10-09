"""Safe post-migration integrity checks for the Alembic environment."""

from __future__ import annotations

from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from oasix.persistence.errors import PersistenceMigrationError

EXPECTED_SCHEMA_REVISION = "0001_a2_2"


def verify_database_integrity(connection: Connection) -> None:
    """Verify SQLite structural integrity without exposing row contents."""

    try:
        foreign_key_violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone()
        integrity_result = connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()
    except SQLAlchemyError:
        raise PersistenceMigrationError(
            "Datenbankintegrität konnte nach der Migration nicht verifiziert werden."
        ) from None

    if foreign_key_violations is not None or integrity_result != "ok":
        raise PersistenceMigrationError(
            "Datenbankintegrität ist nach der Migration nicht gewährleistet."
        )


def verify_schema_revision(connection: Connection) -> None:
    """Read and verify the single compatible Alembic revision without migrating."""

    try:
        version_table_exists = connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
        ).scalar_one_or_none()
        if version_table_exists is None:
            raise PersistenceMigrationError(
                "Datenbankschema besitzt keine kompatible Alembic-Revision."
            )
        revisions = (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars().all()
        )
    except PersistenceMigrationError:
        raise
    except SQLAlchemyError:
        raise PersistenceMigrationError(
            "Datenbankrevision konnte nicht sicher verifiziert werden."
        ) from None

    if revisions != [EXPECTED_SCHEMA_REVISION]:
        raise PersistenceMigrationError(
            "Datenbankschema besitzt keine kompatible Alembic-Revision."
        )
