from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oasix.config import (
    BootstrapSettings,
    RuntimeConfigurationError,
    load_startup_configuration,
)


def _load(data: dict[str, Any], write_config: Any, secret_directory: Path) -> Any:
    return load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(data),
            secrets_directory=secret_directory,
        )
    )


def test_runtime_schema_four_requires_explicit_inference_policy(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    gateway_config_data["policies"].pop("inference")

    with pytest.raises(RuntimeConfigurationError):
        _load(gateway_config_data, write_config, secret_directory)


def test_runtime_schema_three_rejects_gateway_policy_without_silent_upgrade(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    gateway_config_data["schema_version"] = 3

    with pytest.raises(RuntimeConfigurationError):
        _load(gateway_config_data, write_config, secret_directory)


@pytest.mark.parametrize("heartbeat_interval", [10.0, 11.0])
def test_heartbeat_interval_must_be_shorter_than_lease_ttl(
    heartbeat_interval: float,
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    gateway_config_data["policies"]["inference"]["heartbeat_interval_seconds"] = heartbeat_interval

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(gateway_config_data, write_config, secret_directory)

    assert "policies.inference" in str(captured.value)


def test_runtime_schema_four_loads_complete_gateway_policy(
    gateway_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    loaded = _load(gateway_config_data, write_config, secret_directory)

    assert loaded.runtime.schema_version == 4
    assert loaded.runtime.policies.inference is not None
    assert loaded.runtime.policies.inference.request_timeout_seconds == 30
    assert loaded.runtime.policies.inference.lease_ttl_seconds == 10
    assert loaded.runtime.policies.inference.heartbeat_interval_seconds == 2.0
