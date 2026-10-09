"""Alembic environment using only the validated OASIX persistence source."""

from __future__ import annotations

from sqlalchemy.engine import Connection

from alembic import context
from oasix.config import load_startup_configuration
from oasix.persistence import PersistenceError, PersistenceMigrationError
from oasix.persistence.database import initialize_persistence
from oasix.persistence.migrations import verify_database_integrity
from oasix.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def _run_with_connection(connection: Connection) -> None:
    try:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        verify_database_integrity(connection)
    except PersistenceError:
        raise
    except Exception:
        raise PersistenceMigrationError(
            "Datenbankmigration konnte nicht sicher ausgeführt werden."
        ) from None


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _run_with_connection(supplied_connection)
        return

    loaded = load_startup_configuration()
    with initialize_persistence(loaded.runtime, loaded.bootstrap) as database:
        with database.connection() as connection:
            _run_with_connection(connection)


if context.is_offline_mode():
    raise PersistenceMigrationError(
        "Offline-Migrationen ohne validierte Datenbankquelle werden nicht unterstützt."
    )

run_migrations_online()
