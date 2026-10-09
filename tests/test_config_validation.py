from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oasix.config import (
    BootstrapSettings,
    RuntimeConfigurationError,
    load_startup_configuration,
)


def _load(data: dict[str, Any], write_config: Any, secret_directory: Path) -> None:
    load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(data),
            secrets_directory=secret_directory,
        )
    )


@pytest.mark.parametrize(
    ("mutate", "expected_path"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.pop("policies"), "policies"),
        (lambda data: data.update(active_worker="missing-worker"), "<root>"),
        (
            lambda data: data["workers"]["worker-primary"]["connection"]["ssh"].update(port=70000),
            "workers.<worker-id>.connection.ssh.port",
        ),
        (
            lambda data: data["workers"]["worker-primary"]["power"]["wake"].update(
                mac_address="not-a-mac"
            ),
            "workers.<worker-id>.power.wake.wol.mac_address",
        ),
        (
            lambda data: data["workers"]["worker-primary"]["services"]["llm"].update(
                endpoint="not-a-url"
            ),
            "workers.<worker-id>.services.<service-id>.endpoint",
        ),
        (
            lambda data: data["policies"]["retry"]["job"].update(max_attempts=0),
            "policies.retry.job.max_attempts",
        ),
        (
            lambda data: data["policies"]["retry"]["job"].update(
                initial_delay_seconds=30.0, max_delay_seconds=10.0
            ),
            "policies.retry.job",
        ),
    ],
)
def test_rejects_invalid_runtime_values(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    mutate: Any,
    expected_path: str,
) -> None:
    mutate(valid_config_data)

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert expected_path in str(captured.value)


def test_rejects_unknown_fields(
    valid_config_data: dict[str, Any], write_config: Any, secret_directory: Path
) -> None:
    valid_config_data["workers"]["worker-primary"]["inline_password"] = "do-not-display"

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    message = str(captured.value)
    assert "<unknown-field>" in message
    assert "inline_password" not in message
    assert "do-not-display" not in message


def test_rejects_ssh_sleep_without_ssh_connection(
    valid_config_data: dict[str, Any], write_config: Any, secret_directory: Path
) -> None:
    valid_config_data["workers"]["worker-primary"]["connection"]["ssh"] = None

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert "workers.<worker-id>" in str(captured.value)


def test_rejects_unknown_sleep_method(
    valid_config_data: dict[str, Any], write_config: Any, secret_directory: Path
) -> None:
    valid_config_data["workers"]["worker-primary"]["power"]["sleep"] = {
        "method": "hardcoded_application_command"
    }

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert "power.sleep" in str(captured.value)


def test_rejects_duplicate_yaml_keys(tmp_path: Path, secret_directory: Path) -> None:
    config_path = tmp_path / "duplicate.yaml"
    config_path.write_text(
        "schema_version: 1\nschema_version: 1\nactive_worker: worker-primary\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeConfigurationError) as captured:
        load_startup_configuration(
            BootstrapSettings(
                config_file=config_path,
                secrets_directory=secret_directory,
            )
        )

    assert "ungültiges YAML" in str(captured.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://SENTINEL-URL-SECRET@service.example.invalid",
        "https://service-user:SENTINEL-URL-SECRET@service.example.invalid",
        "https://service.example.invalid?token=SENTINEL-URL-SECRET",
        "https://service.example.invalid#SENTINEL-URL-SECRET",
    ],
)
def test_rejects_sensitive_service_url_components_without_echoing_url(
    endpoint: str,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    production_traceback_locals: Any,
) -> None:
    sentinel = "SENTINEL-URL-SECRET"
    valid_config_data["workers"]["worker-primary"]["services"]["llm"]["endpoint"] = endpoint

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert "workers.<worker-id>.services.<service-id>.endpoint" in str(captured.value)
    assert endpoint not in str(captured.value)
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in production_traceback_locals(captured.value)


def test_allows_legitimate_service_api_path(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    endpoint = "https://service.example.invalid/api/v1/inference"
    valid_config_data["workers"]["worker-primary"]["services"]["llm"]["endpoint"] = endpoint

    loaded = load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(valid_config_data),
            secrets_directory=secret_directory,
        )
    )

    service = loaded.runtime.active_worker_profile.services["llm"]
    assert service.endpoint.path == "/api/v1/inference"


@pytest.mark.parametrize(
    "mac_address",
    [
        "00:11:22:33:44:55",
        "02:00:00:00:00:01",
    ],
    ids=["globally-administered", "locally-administered"],
)
def test_accepts_unicast_wake_on_lan_mac_addresses(
    mac_address: str,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    valid_config_data["workers"]["worker-primary"]["power"]["wake"]["mac_address"] = mac_address

    loaded = load_startup_configuration(
        BootstrapSettings(
            config_file=write_config(valid_config_data),
            secrets_directory=secret_directory,
        )
    )

    assert loaded.runtime.active_worker_profile.power.wake.mac_address == mac_address


@pytest.mark.parametrize(
    "mac_address",
    [
        "FF:FF:FF:FF:FF:FF",
        "01:00:5E:00:00:01",
        "33:33:00:00:00:01",
    ],
    ids=["broadcast", "ipv4-multicast", "ipv6-multicast"],
)
def test_rejects_non_unicast_wake_on_lan_mac_addresses(
    mac_address: str,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    valid_config_data["workers"]["worker-primary"]["power"]["wake"]["mac_address"] = mac_address

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert "workers.<worker-id>.power.wake.wol.mac_address" in str(captured.value)


@pytest.mark.parametrize(
    "readiness_path",
    [
        "/health?token=SENTINEL-READINESS-SECRET",
        "/health#SENTINEL-READINESS-SECRET",
    ],
)
def test_rejects_sensitive_readiness_url_components_without_echoing_input(
    readiness_path: str,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    production_traceback_locals: Any,
) -> None:
    sentinel = "SENTINEL-READINESS-SECRET"
    valid_config_data["workers"]["worker-primary"]["services"]["llm"]["readiness"]["path"] = (
        readiness_path
    )

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    assert readiness_path not in str(captured.value)
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in production_traceback_locals(captured.value)


@pytest.mark.parametrize("dynamic_key_kind", ["worker", "service", "unknown-field"])
def test_redacts_dynamic_mapping_keys_from_validation_errors(
    dynamic_key_kind: str,
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    sentinel = "SENTINEL_SECRET_MAPPING_KEY"
    worker = valid_config_data["workers"]["worker-primary"]
    if dynamic_key_kind == "worker":
        valid_config_data["workers"] = {sentinel: worker}
    elif dynamic_key_kind == "service":
        worker["services"] = {sentinel: worker["services"]["llm"]}
    else:
        worker[sentinel] = "value"

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(valid_config_data, write_config, secret_directory)

    message = str(captured.value)
    assert sentinel not in message
    assert "<worker-id>" in message or "<service-id>" in message or "<unknown-field>" in message


def test_invalid_utf8_configuration_is_reported_without_retaining_bytes(
    tmp_path: Path,
    secret_directory: Path,
    production_traceback_locals: Any,
) -> None:
    sentinel = "SENTINEL-BINARY-CONFIG-SECRET"
    config_path = tmp_path / "invalid-utf8.yaml"
    config_path.write_bytes(sentinel.encode("ascii") + b"\xff")

    with pytest.raises(RuntimeConfigurationError) as captured:
        load_startup_configuration(
            BootstrapSettings(
                config_file=config_path,
                secrets_directory=secret_directory,
            )
        )

    assert "ungültiges UTF-8" in str(captured.value)
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert sentinel not in production_traceback_locals(captured.value)
