"""Descriptor-based validation for the externally configured SQLite path."""

from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from oasix.config import BootstrapSettings, PersistenceSettings
from oasix.persistence.errors import PersistenceConfigurationError

type _PathStatus = Literal[
    "ok",
    "relative",
    "parent_unavailable",
    "parent_not_directory",
    "unsafe_permissions",
    "not_writable",
    "secret_collision",
    "symlink",
    "not_regular",
    "changed",
    "unavailable",
]


@dataclass(frozen=True, repr=False)
class PreparedDatabasePath:
    """Canonical reserved database path with identities hidden from representations."""

    path: Path = field(repr=False)
    parent_identity: tuple[int, int] = field(repr=False)
    file_identity: tuple[int, int] = field(repr=False)

    def __repr__(self) -> str:
        return "PreparedDatabasePath(<external-file>)"


def prepare_database_path(
    settings: PersistenceSettings,
    bootstrap: BootstrapSettings,
) -> PreparedDatabasePath:
    """Validate and reserve the configured path without exposing it on failure."""

    prepared, status = _prepare_database_path(
        settings.database_path,
        bootstrap.secrets_directory,
    )
    if prepared is not None:
        return prepared
    _raise_path_error(status)


def database_path_identity_matches(prepared: PreparedDatabasePath) -> bool:
    """Verify that parent and target still identify the objects validated earlier."""

    try:
        parent_status = os.stat(prepared.path.parent, follow_symlinks=False)
        file_status = os.stat(prepared.path, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISDIR(parent_status.st_mode)
        and stat.S_ISREG(file_status.st_mode)
        and (parent_status.st_dev, parent_status.st_ino) == prepared.parent_identity
        and (file_status.st_dev, file_status.st_ino) == prepared.file_identity
    )


def _prepare_database_path(
    database_path: Path,
    secrets_directory: Path,
) -> tuple[PreparedDatabasePath | None, _PathStatus]:
    if not database_path.is_absolute():
        return None, "relative"

    try:
        parent = database_path.parent.resolve(strict=True)
        secret_root = secrets_directory.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "parent_unavailable"

    name = database_path.name
    if not name or name in {".", ".."}:
        return None, "unavailable"
    candidate = parent / name
    if candidate == secret_root or candidate.is_relative_to(secret_root):
        return None, "secret_collision"

    directory_descriptor: int | None = None
    target_descriptor: int | None = None
    probe_name: str | None = None
    probe_created = False
    try:
        directory_flags = os.O_RDONLY
        directory_flags |= getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        directory_descriptor = os.open(parent, directory_flags)
        parent_status = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(parent_status.st_mode):
            return None, "parent_not_directory"
        if not _has_secure_directory_permissions(parent_status):
            return None, "unsafe_permissions"

        probe_name = f".oasix-write-probe-{uuid.uuid4().hex}"
        probe_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        probe_flags |= getattr(os, "O_CLOEXEC", 0)
        probe_flags |= getattr(os, "O_NOFOLLOW", 0)
        probe_descriptor = os.open(
            probe_name,
            probe_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        probe_created = True
        os.close(probe_descriptor)
        os.unlink(probe_name, dir_fd=directory_descriptor)
        probe_created = False

        try:
            target_status = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            target_status = None

        target_flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        target_flags |= getattr(os, "O_NOFOLLOW", 0)
        if target_status is None:
            target_flags |= os.O_CREAT | os.O_EXCL
            target_descriptor = os.open(
                name,
                target_flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            os.fchmod(target_descriptor, 0o600)
        else:
            if stat.S_ISLNK(target_status.st_mode):
                return None, "symlink"
            if not stat.S_ISREG(target_status.st_mode):
                return None, "not_regular"
            if not _has_secure_file_permissions(target_status):
                return None, "unsafe_permissions"
            target_descriptor = os.open(name, target_flags, dir_fd=directory_descriptor)

        opened_status = os.fstat(target_descriptor)
        current_status = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(opened_status.st_mode):
            return None, "not_regular"
        if not _has_secure_file_permissions(opened_status):
            return None, "unsafe_permissions"
        if (opened_status.st_dev, opened_status.st_ino) != (
            current_status.st_dev,
            current_status.st_ino,
        ):
            return None, "changed"

        return (
            PreparedDatabasePath(
                path=candidate,
                parent_identity=(parent_status.st_dev, parent_status.st_ino),
                file_identity=(opened_status.st_dev, opened_status.st_ino),
            ),
            "ok",
        )
    except PermissionError:
        return None, "not_writable"
    except (OSError, RuntimeError):
        return None, "unavailable"
    finally:
        if target_descriptor is not None:
            try:
                os.close(target_descriptor)
            except OSError:
                pass
        if probe_created and probe_name is not None and directory_descriptor is not None:
            try:
                os.unlink(probe_name, dir_fd=directory_descriptor)
            except OSError:
                pass
        if directory_descriptor is not None:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


def _has_secure_directory_permissions(status: os.stat_result) -> bool:
    return status.st_uid == os.geteuid() and stat.S_IMODE(status.st_mode) == 0o700


def _has_secure_file_permissions(status: os.stat_result) -> bool:
    return status.st_uid == os.geteuid() and stat.S_IMODE(status.st_mode) == 0o600


def _raise_path_error(status: _PathStatus) -> None:
    messages = {
        "relative": "Datenbankpfad muss absolut sein.",
        "parent_unavailable": "Datenbankverzeichnis ist nicht verfügbar.",
        "parent_not_directory": "Datenbank-Elternpfad ist kein Verzeichnis.",
        "unsafe_permissions": "Datenbankpfad besitzt keine sicheren Berechtigungen.",
        "not_writable": "Datenbankverzeichnis ist nicht sicher beschreibbar.",
        "secret_collision": "Datenbankpfad darf nicht in der Secret-Quelle liegen.",
        "symlink": "Datenbankziel darf kein symbolischer Link sein.",
        "not_regular": "Datenbankziel muss eine reguläre Datei sein.",
        "changed": "Datenbankpfad hat sich während der Prüfung verändert.",
        "unavailable": "Datenbankpfad konnte nicht sicher geprüft werden.",
    }
    raise PersistenceConfigurationError(messages.get(status, messages["unavailable"]))
