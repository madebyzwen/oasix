from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oasix.config import BootstrapSettings, RuntimeConfigurationError, load_startup_configuration


def _load(
    data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> Any:
    return load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(data),
            secrets_directory=secret_directory,
        )
    )


def test_loads_runtime_configuration_version_two_with_persistence(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    loaded = _load(valid_config_data, write_config, secret_directory)

    assert loaded.runtime.schema_version == 2
    assert loaded.runtime.persistence.database_path.is_absolute()
    assert loaded.runtime.persistence.busy_timeout_ms == 5000
    assert loaded.bootstrap.secrets_directory == secret_directory
    assert "database_path" not in repr(loaded.runtime.persistence)
    assert str(loaded.bootstrap.config_file) not in repr(loaded)


def test_rejects_version_one_without_interpreting_it_as_version_two(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    valid_config_data["schema_version"] = 1
    valid_config_data.pop("persistence")

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert "schema_version" in str(captured.value)
    assert "persistence" in str(captured.value)


@pytest.mark.parametrize("missing_field", ["persistence", "database_path"])
def test_rejects_missing_persistence_fields(
    missing_field: str,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    if missing_field == "persistence":
        valid_config_data.pop("persistence")
    else:
        valid_config_data["persistence"].pop("database_path")

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert "persistence" in str(captured.value)


def test_rejects_unknown_persistence_field_without_revealing_it(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    sentinel = "SENTINEL_SECRET_PERSISTENCE_FIELD"
    valid_config_data["persistence"][sentinel] = "hidden-value"

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert "persistence.<unknown-field>" in str(captured.value)
    assert sentinel not in str(captured.value)
    assert "hidden-value" not in str(captured.value)


def test_rejects_relative_database_path_without_echoing_it(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    sentinel = "SENTINEL-PRIVATE-RELATIVE-PATH"
    valid_config_data["persistence"]["database_path"] = f"relative/{sentinel}.sqlite3"

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert "persistence.database_path" in str(captured.value)
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


@pytest.mark.parametrize("busy_timeout_ms", [0, 60_001])
def test_rejects_out_of_range_busy_timeout(
    busy_timeout_ms: int,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    valid_config_data["persistence"]["busy_timeout_ms"] = busy_timeout_ms

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert "persistence.busy_timeout_ms" in str(captured.value)


def test_uses_default_busy_timeout_when_field_is_omitted(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    valid_config_data["persistence"].pop("busy_timeout_ms")

    loaded = _load(valid_config_data, write_config, secret_directory)

    assert loaded.runtime.persistence.busy_timeout_ms == 5000
