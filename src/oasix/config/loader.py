"""Atomic startup loading for bootstrap settings, YAML, and referenced secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from oasix.config.bootstrap import BootstrapSettings
from oasix.config.errors import BootstrapConfigurationError, RuntimeConfigurationError
from oasix.config.models import RuntimeConfig
from oasix.config.secrets import (
    FileSecretSource,
    ResolvedSecrets,
    collect_secret_references,
    resolve_secrets,
)

_BOOTSTRAP_ENV_PREFIX = "OASIX_"
_SUPPORTED_BOOTSTRAP_ENVIRONMENT_VARIABLES = frozenset(
    {
        "OASIX_CONFIG_FILE",
        "OASIX_SECRETS_DIRECTORY",
    }
)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader which also rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "mapping key is not hashable",
                key_node.start_mark,
            ) from None
        if duplicate:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "duplicate mapping key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, repr=False)
class LoadedConfiguration:
    """Complete startup result. Secret values stay opaque in debug output."""

    runtime: RuntimeConfig
    secrets: ResolvedSecrets

    def __repr__(self) -> str:
        return (
            "LoadedConfiguration("
            f"schema_version={self.runtime.schema_version}, "
            f"active_worker={self.runtime.active_worker!r}, "
            f"secrets={self.secrets!r})"
        )


def _format_location(location: tuple[str | int, ...], error_type: str) -> str:
    safe_parts: list[str] = []
    for index, part in enumerate(location):
        previous = location[index - 1] if index > 0 else None
        if previous == "workers":
            safe_parts.append("<worker-id>")
        elif previous == "services":
            safe_parts.append("<service-id>")
        elif error_type == "extra_forbidden" and index == len(location) - 1:
            safe_parts.append("<unknown-field>")
        elif part == "[key]":
            safe_parts.append("<mapping-key>")
        elif isinstance(part, int):
            safe_parts.append(f"[{part}]")
        else:
            safe_parts.append(str(part))
    return ".".join(safe_parts) or "<root>"


def _safe_validation_details(error: ValidationError) -> str:
    labels = {
        "extra_forbidden": "unbekanntes Feld",
        "greater_than": "Wert liegt unterhalb der erlaubten Grenze",
        "greater_than_equal": "Wert liegt unterhalb der erlaubten Grenze",
        "int_type": "Ganzzahl erwartet",
        "less_than_equal": "Wert liegt oberhalb der erlaubten Grenze",
        "literal_error": "nicht unterstützter Wert",
        "missing": "Pflichtfeld fehlt",
        "string_pattern_mismatch": "Format ist ungültig",
        "string_too_short": "Wert darf nicht leer sein",
        "url_parsing": "URL ist ungültig",
        "value_error": "Wert oder Feldkombination ist ungültig",
    }
    details: list[str] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        error_type = str(item["type"])
        label = labels.get(error_type, "ungültiger Wert")
        details.append(f"{_format_location(item['loc'], error_type)}: {label} [{error_type}]")
    return "; ".join(details)


def _read_utf8_configuration(path: Path) -> str | None:
    """Read configuration without retaining undecodable bytes in a later exception."""

    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _load_yaml(path: Path) -> Any:
    serialized = _read_utf8_configuration(path)
    if serialized is None:
        raise RuntimeConfigurationError(
            "Runtime-Konfigurationsdatei ist nicht lesbar oder enthält ungültiges UTF-8."
        )

    yaml_error_location: str | None = None
    try:
        return yaml.load(serialized, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        yaml_error_location = ""
        if mark is not None:
            yaml_error_location = f" in Zeile {mark.line + 1}, Spalte {mark.column + 1}"
    if yaml_error_location is not None:
        serialized = ""
        raise RuntimeConfigurationError(
            f"Runtime-Konfiguration enthält ungültiges YAML{yaml_error_location}."
        )
    raise AssertionError("YAML loading produced neither a document nor an error")


def _validate_runtime_config(path: Path) -> RuntimeConfig:
    document = _load_yaml(path)
    details: str | None = None
    try:
        return RuntimeConfig.model_validate(document)
    except ValidationError as error:
        details = _safe_validation_details(error)
    if details is not None:
        del document
        raise RuntimeConfigurationError(f"Runtime-Konfiguration ist ungültig: {details}")
    raise AssertionError("runtime validation produced neither a model nor an error")


def _load_bootstrap_settings() -> BootstrapSettings:
    unknown_oasix_variables = sum(
        1
        for name in os.environ
        if name.upper().startswith(_BOOTSTRAP_ENV_PREFIX)
        and name.upper() not in _SUPPORTED_BOOTSTRAP_ENVIRONMENT_VARIABLES
    )
    if unknown_oasix_variables:
        raise BootstrapConfigurationError(
            "Bootstrap-Konfiguration enthält "
            f"{unknown_oasix_variables} unbekannte OASIX-Umgebungsvariable(n)."
        )

    details: str | None = None
    try:
        return BootstrapSettings()
    except ValidationError as error:
        details = _safe_validation_details(error)
    if details is not None:
        raise BootstrapConfigurationError(f"Bootstrap-Konfiguration ist ungültig: {details}")
    raise AssertionError("bootstrap validation produced neither settings nor an error")


def load_startup_configuration(
    bootstrap: BootstrapSettings | None = None,
) -> LoadedConfiguration:
    """Load and validate every startup dependency before returning usable state."""

    selected_bootstrap = bootstrap if bootstrap is not None else _load_bootstrap_settings()
    runtime = _validate_runtime_config(selected_bootstrap.config_file)
    source = FileSecretSource(selected_bootstrap.secrets_directory)
    secrets = resolve_secrets(collect_secret_references(runtime), source)
    return LoadedConfiguration(runtime=runtime, secrets=secrets)
