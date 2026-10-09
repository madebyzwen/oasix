from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import oasix.config.secrets as secrets_module
from oasix.config import (
    BootstrapSettings,
    RuntimeConfigurationError,
    SecretResolutionError,
    load_startup_configuration,
)
from oasix.config.models import SecretReference
from oasix.config.secrets import FileSecretSource


def test_resolves_secret_and_masks_every_debug_representation(
    valid_config_data: dict[str, Any], write_config: Any, secret_directory: Path
) -> None:
    sentinel = "NEVER-EXPOSE-THIS-SECRET"
    (secret_directory / "llm_api_token").write_text(sentinel + "\n", encoding="utf-8")

    loaded = load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(valid_config_data),
            secrets_directory=secret_directory,
        )
    )
    reference = SecretReference(source="file", name="llm_api_token")

    assert loaded.secrets.get(reference).get_secret_value() == sentinel
    assert sentinel not in repr(loaded)
    assert sentinel not in repr(loaded.secrets)
    assert sentinel not in repr(loaded.secrets.get(reference))


def test_missing_secret_fails_startup_without_exposing_other_values(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    monkeypatch: Any,
    production_traceback_locals: Any,
) -> None:
    sentinel = "ANOTHER-SECRET-THAT-MUST-STAY-HIDDEN"
    (secret_directory / "llm_api_token").write_text(sentinel, encoding="utf-8")
    (secret_directory / "worker_ssh_key").unlink()
    resolved_references: list[str] = []
    original_resolve = FileSecretSource.resolve

    def tracking_resolve(
        source: FileSecretSource,
        reference: SecretReference,
    ) -> Any:
        value = original_resolve(source, reference)
        resolved_references.append(reference.name)
        return value

    monkeypatch.setattr(FileSecretSource, "resolve", tracking_resolve)

    with pytest.raises(SecretResolutionError) as captured:
        load_startup_configuration(
            BootstrapSettings(
                config_file=write_config(valid_config_data),
                secrets_directory=secret_directory,
            )
        )

    assert "llm_api_token" in resolved_references
    assert "worker_ssh_key" in str(captured.value)
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in production_traceback_locals(captured.value)


def test_empty_secret_fails_startup(
    valid_config_data: dict[str, Any], write_config: Any, secret_directory: Path
) -> None:
    (secret_directory / "llm_api_token").write_text("\n", encoding="utf-8")

    with pytest.raises(SecretResolutionError, match="ist leer"):
        load_startup_configuration(
            BootstrapSettings(
                config_file=write_config(valid_config_data),
                secrets_directory=secret_directory,
            )
        )


def test_rejects_secret_reference_path_traversal() -> None:
    with pytest.raises(ValueError):
        SecretReference(source="file", name="../outside")


def test_missing_secret_directory_fails_without_exception_context(tmp_path: Path) -> None:
    with pytest.raises(SecretResolutionError) as captured:
        FileSecretSource(tmp_path / "missing-secrets")

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_rejects_symlink_escaping_secret_directory(tmp_path: Path, secret_directory: Path) -> None:
    sentinel = "OUTSIDE-SECRET-CONTENT"
    outside = tmp_path / "outside-secret"
    outside.write_text(sentinel, encoding="utf-8")
    (secret_directory / "escaped_secret").symlink_to(outside)
    source = FileSecretSource(secret_directory)
    reference = SecretReference(source="file", name="escaped_secret")

    with pytest.raises(SecretResolutionError) as captured:
        source.resolve(reference)

    assert "verlässt das erlaubte Verzeichnis" in str(captured.value)
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


def test_reads_opened_descriptor_when_symlink_target_changes(
    tmp_path: Path,
    secret_directory: Path,
    monkeypatch: Any,
) -> None:
    inside = secret_directory / "inside_secret"
    inside.write_text("inside-value", encoding="utf-8")
    outside = tmp_path / "outside-secret"
    outside.write_text("outside-value", encoding="utf-8")
    link = secret_directory / "changing_link"
    link.symlink_to(inside)
    original_descriptor_path = secrets_module._descriptor_path

    def swap_link_after_open(file_descriptor: int) -> Path | None:
        opened_path = original_descriptor_path(file_descriptor)
        link.unlink()
        link.symlink_to(outside)
        return opened_path

    monkeypatch.setattr(secrets_module, "_descriptor_path", swap_link_after_open)
    source = FileSecretSource(secret_directory)

    secret = source.resolve(SecretReference(source="file", name="changing_link"))

    assert secret.get_secret_value() == "inside-value"


def test_rejects_replaced_secret_directory(tmp_path: Path, secret_directory: Path) -> None:
    source = FileSecretSource(secret_directory)
    original_directory = tmp_path / "original-secrets"
    secret_directory.rename(original_directory)
    secret_directory.mkdir()
    (secret_directory / "llm_api_token").write_text("replacement-value", encoding="utf-8")

    with pytest.raises(SecretResolutionError, match="nicht sicher verifiziert"):
        source.resolve(SecretReference(source="file", name="llm_api_token"))


def test_fails_closed_when_descriptor_path_cannot_be_verified(
    secret_directory: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(secrets_module, "_descriptor_path", lambda _descriptor: None)
    source = FileSecretSource(secret_directory)

    with pytest.raises(SecretResolutionError, match="nicht sicher verifiziert"):
        source.resolve(SecretReference(source="file", name="llm_api_token"))


def test_allows_symlink_remaining_inside_secret_directory(secret_directory: Path) -> None:
    (secret_directory / "internal_link").symlink_to(secret_directory / "llm_api_token")
    source = FileSecretSource(secret_directory)

    secret = source.resolve(SecretReference(source="file", name="internal_link"))

    assert secret.get_secret_value() == "api-token-value"


def test_invalid_runtime_input_is_not_retained_by_exception(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    production_traceback_locals: Any,
) -> None:
    sentinel = "INLINE-SECRET-MUST-NOT-LEAK"
    valid_config_data["workers"]["worker-primary"]["services"]["llm"]["token"] = sentinel

    with pytest.raises(RuntimeConfigurationError) as captured:
        load_startup_configuration(
            BootstrapSettings(
                config_file=write_config(valid_config_data),
                secrets_directory=secret_directory,
            )
        )

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert sentinel not in production_traceback_locals(captured.value)


def test_invalid_secret_bytes_are_not_retained_by_exception(
    tmp_path: Path,
    secret_directory: Path,
    production_traceback_locals: Any,
) -> None:
    sentinel = "BINARY-SECRET-THAT-MUST-NOT-LEAK"
    (secret_directory / "binary_secret").write_bytes(sentinel.encode("ascii") + b"\xff")
    source = FileSecretSource(secret_directory)
    reference = SecretReference(source="file", name="binary_secret")

    with pytest.raises(SecretResolutionError) as captured:
        source.resolve(reference)

    assert sentinel not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert sentinel not in production_traceback_locals(captured.value)
