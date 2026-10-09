"""Safe post-migration integrity checks for the Alembic environment."""

from __future__ import annotations

from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from oasix.persistence.errors import PersistenceMigrationError


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
