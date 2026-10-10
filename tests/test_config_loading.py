from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from oasix.config import (
    BootstrapConfigurationError,
    BootstrapSettings,
    load_startup_configuration,
)


def test_loads_complete_configuration_and_configured_sleep_command(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    valid_config_data["workers"]["worker-primary"]["power"]["sleep"]["command"] = [
        "custom-power-tool",
        "sleep-now",
    ]
    config_path = write_config(valid_config_data)

    loaded = load_startup_configuration(
        BootstrapSettings(config_file=config_path, secrets_directory=secret_directory)
    )

    assert loaded.runtime.active_worker == "worker-primary"
    assert loaded.runtime.active_worker_profile.power.sleep.command == (
        "custom-power-tool",
        "sleep-now",
    )
    assert len(loaded.secrets) == 3


def test_repository_example_is_a_valid_runtime_configuration(
    secret_directory: Path,
) -> None:
    loaded = load_startup_configuration(
        BootstrapSettings(
            config_file=Path("config/oasix.example.yaml"),
            secrets_directory=secret_directory,
        )
    )

    assert loaded.runtime.schema_version == 4
    assert loaded.runtime.active_worker == "worker-primary"
    assert loaded.runtime.client_auth is not None
    assert len(loaded.runtime.client_auth.clients) == 2
    assert loaded.runtime.policies.inference is not None
    assert loaded.runtime.policies.inference.heartbeat_interval_seconds == 20.0
    assert len(loaded.secrets) == 6


def test_supports_multiple_worker_profiles_and_selects_active_worker(
    valid_config_data: dict[str, Any],
    clone_config: Any,
    write_config: Any,
    secret_directory: Path,
) -> None:
    second_worker = clone_config(valid_config_data["workers"]["worker-primary"])
    second_worker["connection"]["host"] = "replacement-worker.example.invalid"
    valid_config_data["workers"]["worker-secondary"] = second_worker
    valid_config_data["active_worker"] = "worker-secondary"

    loaded = load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(valid_config_data),
            secrets_directory=secret_directory,
        )
    )

    assert len(loaded.runtime.workers) == 2
    assert loaded.runtime.active_worker_profile.connection.host == (
        "replacement-worker.example.invalid"
    )


def test_bootstrap_reads_only_source_locations_from_environment(
    monkeypatch: Any, tmp_path: Path
) -> None:
    config_path = tmp_path / "runtime.yaml"
    secrets_path = tmp_path / "external-secrets"
    monkeypatch.setenv("OASIX_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("OASIX_SECRETS_DIRECTORY", str(secrets_path))
    monkeypatch.setenv("UNRELATED_APPLICATION_SETTING", "ignored")

    bootstrap = BootstrapSettings()

    assert bootstrap.config_file == config_path
    assert bootstrap.secrets_directory == secrets_path
    assert set(type(bootstrap).model_fields) == {"config_file", "secrets_directory"}


def test_foreign_environment_variables_do_not_prevent_startup(
    monkeypatch: Any,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    monkeypatch.setenv("OASIX_CONFIG_FILE", str(write_config(valid_config_data)))
    monkeypatch.setenv("OASIX_SECRETS_DIRECTORY", str(secret_directory))
    monkeypatch.setenv("UNRELATED_APPLICATION_SETTING", "ignored")

    loaded = load_startup_configuration()

    assert loaded.runtime.active_worker == "worker-primary"


def test_unknown_oasix_environment_variable_fails_without_revealing_its_name(
    monkeypatch: Any,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    sentinel = "SENTINEL_SECRET_BOOTSTRAP_TYPO"
    monkeypatch.setenv("OASIX_CONFIG_FILE", str(write_config(valid_config_data)))
    monkeypatch.setenv("OASIX_SECRETS_DIRECTORY", str(secret_directory))
    monkeypatch.setenv(f"OASIX_{sentinel}", "value")

    with pytest.raises(BootstrapConfigurationError) as captured:
        load_startup_configuration()

    assert "unbekannte OASIX-Umgebungsvariable" in str(captured.value)
    assert sentinel not in str(captured.value)


def test_bootstrap_constructor_rejects_unknown_fields(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        BootstrapSettings(
            config_file=tmp_path / "runtime.yaml",
            secrets_directory=tmp_path / "secrets",
            config_flie=tmp_path / "typo.yaml",
        )


def test_runtime_mappings_are_immutable(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    loaded = load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(valid_config_data),
            secrets_directory=secret_directory,
        )
    )
    profile = loaded.runtime.active_worker_profile

    with pytest.raises(TypeError):
        loaded.runtime.workers["worker-secondary"] = profile
    with pytest.raises(TypeError):
        profile.services["replacement-service"] = profile.services["llm"]

    assert tuple(loaded.runtime.workers) == ("worker-primary",)
    assert tuple(profile.services) == ("llm",)


def test_missing_bootstrap_config_source_fails_before_runtime_loading(monkeypatch: Any) -> None:
    monkeypatch.delenv("OASIX_CONFIG_FILE", raising=False)
    monkeypatch.delenv("OASIX_SECRETS_DIRECTORY", raising=False)

    with pytest.raises(BootstrapConfigurationError) as captured:
        load_startup_configuration()

    assert "config_file" in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
