"""Secret providers and non-revealing storage for resolved values."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from pydantic import SecretStr

from oasix.config.errors import SecretResolutionError
from oasix.config.models import RuntimeConfig, SecretReference

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable outside POSIX platforms
    fcntl = None  # type: ignore[assignment]


class SecretSource(Protocol):
    """Provider-neutral interface for resolving external secret references."""

    def resolve(self, reference: SecretReference) -> SecretStr:
        """Resolve a reference or raise a safe SecretResolutionError."""


class FileSecretSource:
    """Resolve secret files while containing canonical paths below one directory."""

    __slots__ = ("_root", "_root_identity")

    def __init__(self, root: Path) -> None:
        resolution_failed = False
        try:
            resolved_root = root.resolve(strict=True)
        except (OSError, RuntimeError):
            resolution_failed = True
            resolved_root = root
        if resolution_failed:
            raise SecretResolutionError("Secret-Verzeichnis ist nicht verfügbar.")
        try:
            root_status = resolved_root.stat()
        except OSError:
            root_status = None
        if root_status is None or not stat.S_ISDIR(root_status.st_mode):
            raise SecretResolutionError("Secret-Quelle ist kein Verzeichnis.")
        self._root = resolved_root
        self._root_identity = (root_status.st_dev, root_status.st_ino)

    def __repr__(self) -> str:
        return "FileSecretSource(<external-directory>)"

    def resolve(self, reference: SecretReference) -> SecretStr:
        if reference.source != "file":
            raise SecretResolutionError(
                f"Nicht unterstützte Secret-Quelle für Referenz '{reference.name}'."
            )

        value, status = _read_verified_secret(
            self._root,
            self._root_identity,
            reference.name,
        )
        if status == "outside":
            raise SecretResolutionError(
                f"Secret-Referenz '{reference.name}' verlässt das erlaubte Verzeichnis."
            )
        if status == "not_regular":
            raise SecretResolutionError(
                f"Secret-Referenz '{reference.name}' verweist nicht auf eine reguläre Datei."
            )
        if status in {"changed", "unverified"}:
            raise SecretResolutionError(
                f"Secret-Referenz '{reference.name}' kann nicht sicher verifiziert werden."
            )
        if status != "ok" or value is None:
            raise SecretResolutionError(
                f"Secret-Referenz '{reference.name}' kann nicht sicher gelesen werden."
            )

        if not value:
            raise SecretResolutionError(f"Secret-Referenz '{reference.name}' ist leer.")
        return SecretStr(value)


def _descriptor_path(file_descriptor: int) -> Path | None:
    """Return the path of an opened descriptor on supported deployment platforms."""

    if sys.platform.startswith("linux"):
        return Path(os.readlink(f"/proc/self/fd/{file_descriptor}")).resolve(strict=True)
    if sys.platform == "darwin" and fcntl is not None:
        get_path_operation = getattr(fcntl, "F_GETPATH", 50)
        path_buffer = fcntl.fcntl(file_descriptor, get_path_operation, bytes(1024))
        if not isinstance(path_buffer, bytes):
            return None
        encoded_path = path_buffer.split(b"\0", 1)[0]
        return Path(os.fsdecode(encoded_path)).resolve(strict=True) if encoded_path else None
    return None


def _read_verified_secret(
    root: Path,
    expected_root_identity: tuple[int, int],
    name: str,
) -> tuple[str | None, str]:
    """Open, verify, and read the same file descriptor without a check/use path gap."""

    root_descriptor: int | None = None
    secret_descriptor: int | None = None
    try:
        directory_flags = os.O_RDONLY
        directory_flags |= getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        root_descriptor = os.open(root, directory_flags)
        root_status = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_status.st_mode)
            or (root_status.st_dev, root_status.st_ino) != expected_root_identity
        ):
            return None, "changed"

        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        secret_descriptor = os.open(name, file_flags, dir_fd=root_descriptor)
        opened_status = os.fstat(secret_descriptor)
        if not stat.S_ISREG(opened_status.st_mode):
            return None, "not_regular"

        opened_path = _descriptor_path(secret_descriptor)
        if opened_path is None:
            return None, "unverified"
        resolved_opened_path = opened_path.resolve(strict=True)
        if not resolved_opened_path.is_relative_to(root):
            return None, "outside"

        current_status = resolved_opened_path.stat()
        if (current_status.st_dev, current_status.st_ino) != (
            opened_status.st_dev,
            opened_status.st_ino,
        ):
            return None, "changed"

        chunks: list[bytes] = []
        while chunk := os.read(secret_descriptor, 8192):
            chunks.append(chunk)
        value = b"".join(chunks).decode("utf-8").rstrip("\r\n")
        return value, "ok"
    except (OSError, RuntimeError, UnicodeDecodeError):
        return None, "unavailable"
    finally:
        if secret_descriptor is not None:
            try:
                os.close(secret_descriptor)
            except OSError:
                pass
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except OSError:
                pass


class ResolvedSecrets:
    """Immutable resolved secrets with an intentionally opaque representation."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[SecretReference, SecretStr]) -> None:
        self._values = MappingProxyType(dict(values))

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"ResolvedSecrets(count={len(self)})"

    def get(self, reference: SecretReference) -> SecretStr:
        return self._values[reference]


def collect_secret_references(config: RuntimeConfig) -> frozenset[SecretReference]:
    references: set[SecretReference] = set()
    for worker in config.workers.values():
        if worker.connection.ssh is not None:
            references.add(worker.connection.ssh.private_key)
            references.add(worker.connection.ssh.known_hosts)
        for service in worker.services.values():
            if service.auth.method != "none":
                references.add(service.auth.secret)
    return frozenset(references)


def resolve_secrets(references: Iterable[SecretReference], source: SecretSource) -> ResolvedSecrets:
    resolved: dict[SecretReference, SecretStr] = {}
    for reference in sorted(references, key=lambda item: (item.source, item.name)):
        resolved[reference] = source.resolve(reference)
    return ResolvedSecrets(resolved)
