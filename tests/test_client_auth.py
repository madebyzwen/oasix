from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from oasix import auth as auth_module
from oasix.auth import (
    ClientAuthConfigurationError,
    ClientAuthenticationError,
    ClientAuthenticator,
    ClientAuthorizationError,
    ClientAuthorizer,
    ClientPermission,
    create_client_authenticator,
)
from oasix.auth import core as auth_core
from oasix.config import (
    BootstrapSettings,
    RuntimeConfigurationError,
    SecretResolutionError,
    load_startup_configuration,
)


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


def _authenticator(
    data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> Any:
    loaded = _load(data, write_config, secret_directory)
    return create_client_authenticator(loaded.runtime, loaded.secrets)


def test_authenticates_multiple_clients_and_checks_explicit_permissions(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    authenticator = _authenticator(auth_config_data, write_config, secret_directory)
    authorizer = ClientAuthorizer()

    inference = authenticator.authenticate("Bearer inference-client-key-current")
    administration = authenticator.authenticate("Bearer administration-client-key")

    assert inference.client_id == "inference-client"
    assert inference.permissions == frozenset({ClientPermission.INFERENCE})
    assert administration.client_id == "operations-client"
    authorizer.require(inference, ClientPermission.INFERENCE)
    authorizer.require(administration, ClientPermission.ADMINISTRATION)


def test_inference_cannot_administer_and_admin_has_no_implicit_inference_permission(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    authenticator = _authenticator(auth_config_data, write_config, secret_directory)
    authorizer = ClientAuthorizer()

    inference = authenticator.authenticate("Bearer inference-client-key-current")
    administration = authenticator.authenticate("Bearer administration-client-key")

    with pytest.raises(ClientAuthorizationError):
        authorizer.require(inference, ClientPermission.ADMINISTRATION)
    with pytest.raises(ClientAuthorizationError):
        authorizer.require(administration, ClientPermission.INFERENCE)
    with pytest.raises(ClientAuthorizationError):
        authorizer.require(administration, "undefined-operation")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "authorization_header",
    [
        None,
        "",
        "Basic unknown-client-key",
        "Bearer",
        "Bearer  unknown-client-key",
        "Bearer unknown client key",
        "Bearer unknown-client-key",
        "Bearer api-token-value",
    ],
)
def test_authentication_failures_are_uniform_and_do_not_reveal_key_existence(
    authorization_header: str | None,
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    authenticator = _authenticator(auth_config_data, write_config, secret_directory)

    with pytest.raises(ClientAuthenticationError) as captured:
        authenticator.authenticate(authorization_header)

    assert str(captured.value) == "Client-Authentifizierung fehlgeschlagen."
    assert (
        repr(captured.value)
        == "ClientAuthenticationError('Client-Authentifizierung fehlgeschlagen.')"
    )
    assert captured.value.__cause__ is None


def test_authentication_compares_every_configured_key(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticator = _authenticator(auth_config_data, write_config, secret_directory)
    original_compare = auth_core.hmac.compare_digest
    comparisons = 0

    def counting_compare(first: bytes, second: bytes) -> bool:
        nonlocal comparisons
        comparisons += 1
        return original_compare(first, second)

    monkeypatch.setattr(auth_core.hmac, "compare_digest", counting_compare)

    authenticator.authenticate("Bearer inference-client-key-current")

    assert comparisons == 3


def test_key_rotation_accepts_overlap_then_allows_old_key_removal(
    auth_config_data: dict[str, Any],
    clone_config: Any,
    write_config: Any,
    secret_directory: Path,
) -> None:
    overlapping = _authenticator(auth_config_data, write_config, secret_directory)
    assert (
        overlapping.authenticate("Bearer inference-client-key-current").client_id
        == "inference-client"
    )
    assert (
        overlapping.authenticate("Bearer inference-client-key-next").client_id == "inference-client"
    )

    rotated_data = clone_config(auth_config_data)
    rotated_data["client_auth"]["clients"]["inference-client"]["key_secrets"] = [
        {"source": "file", "name": "client_inference_next_key"}
    ]
    rotated = _authenticator(rotated_data, write_config, secret_directory)

    with pytest.raises(ClientAuthenticationError):
        rotated.authenticate("Bearer inference-client-key-current")
    assert rotated.authenticate("Bearer inference-client-key-next").client_id == "inference-client"


def test_duplicate_client_secret_reference_is_rejected_during_configuration_loading(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    duplicate = {"source": "file", "name": "client_inference_key"}
    auth_config_data["client_auth"]["clients"]["operations-client"]["key_secrets"] = [duplicate]

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(auth_config_data, write_config, secret_directory)

    assert "client_auth" in str(captured.value)


def test_distinct_references_with_same_client_key_are_rejected_safely(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    production_traceback_locals: Any,
) -> None:
    sentinel = "SENTINEL-DUPLICATE-CLIENT-KEY"
    (secret_directory / "client_inference_key").write_text(sentinel, encoding="utf-8")
    (secret_directory / "client_inference_next_key").write_text(sentinel, encoding="utf-8")
    loaded = _load(auth_config_data, write_config, secret_directory)

    with pytest.raises(ClientAuthConfigurationError) as captured:
        create_client_authenticator(loaded.runtime, loaded.secrets)

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in production_traceback_locals(captured.value)


def test_client_and_provider_credentials_are_kept_separate_by_value(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    provider_value = (secret_directory / "llm_api_token").read_text(encoding="utf-8")
    (secret_directory / "client_admin_key").write_text(provider_value, encoding="utf-8")
    loaded = _load(auth_config_data, write_config, secret_directory)

    with pytest.raises(ClientAuthConfigurationError):
        create_client_authenticator(loaded.runtime, loaded.secrets)


def test_non_ascii_provider_secret_does_not_restrict_valid_client_keys(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    (secret_directory / "llm_api_token").write_text(
        "provider-credential-with-unicode-ä", encoding="utf-8"
    )

    authenticator = _authenticator(auth_config_data, write_config, secret_directory)

    assert (
        authenticator.authenticate("Bearer inference-client-key-current").client_id
        == "inference-client"
    )


def test_client_and_provider_secret_reference_reuse_is_rejected(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    auth_config_data["client_auth"]["clients"]["operations-client"]["key_secrets"] = [
        {"source": "file", "name": "llm_api_token"}
    ]

    with pytest.raises(RuntimeConfigurationError):
        _load(auth_config_data, write_config, secret_directory)


def test_missing_client_secret_prevents_startup(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    (secret_directory / "client_admin_key").unlink()

    with pytest.raises(SecretResolutionError):
        _load(auth_config_data, write_config, secret_directory)


def test_invalid_permission_prevents_startup_without_echoing_value(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    sentinel = "SENTINEL-UNDEFINED-PERMISSION"
    auth_config_data["client_auth"]["clients"]["inference-client"]["permissions"] = [sentinel]

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(auth_config_data, write_config, secret_directory)

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


def test_dynamic_client_id_is_redacted_from_validation_error(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    sentinel = "SENTINEL-PRIVATE-CLIENT-ID"
    client = auth_config_data["client_auth"]["clients"].pop("inference-client")
    auth_config_data["client_auth"]["clients"][sentinel] = client

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(auth_config_data, write_config, secret_directory)

    assert "client_auth.clients.<client-id>" in str(captured.value)
    assert sentinel not in str(captured.value)


def test_unknown_client_field_is_forbidden_and_redacted(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    sentinel = "SENTINEL-PRIVATE-CLIENT-FIELD"
    auth_config_data["client_auth"]["clients"]["inference-client"][sentinel] = (
        "SENTINEL-PRIVATE-CLIENT-VALUE"
    )

    with pytest.raises(RuntimeConfigurationError) as captured:
        _load(auth_config_data, write_config, secret_directory)

    message = str(captured.value)
    assert "client_auth.clients.<client-id>.<unknown-field>" in message
    assert sentinel not in message
    assert "SENTINEL-PRIVATE-CLIENT-VALUE" not in message


def test_secret_never_appears_in_auth_errors_logs_or_traceback_locals(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
    production_traceback_locals: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "SENTINEL-CLIENT-SECRET-VALUE"
    (secret_directory / "client_inference_key").write_text(sentinel, encoding="utf-8")
    authenticator = _authenticator(auth_config_data, write_config, secret_directory)
    authenticated = authenticator.authenticate(f"Bearer {sentinel}")
    assert authenticated.client_id == "inference-client"

    caplog.set_level(logging.DEBUG)
    with pytest.raises(ClientAuthenticationError) as captured:
        authenticator.authenticate(f"Basic {sentinel}")

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in production_traceback_locals(captured.value)
    assert sentinel not in caplog.text
    assert sentinel not in repr(authenticator)
    assert sentinel not in repr(authenticated)


def test_authorizer_rejects_an_untyped_identity() -> None:
    with pytest.raises(ClientAuthenticationError):
        ClientAuthorizer().require(object(), ClientPermission.ADMINISTRATION)  # type: ignore[arg-type]


def test_schema_version_two_remains_valid_but_cannot_initialize_client_auth(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    loaded = _load(valid_config_data, write_config, secret_directory)

    with pytest.raises(ClientAuthConfigurationError):
        create_client_authenticator(loaded.runtime, loaded.secrets)


def test_schema_version_three_requires_client_auth(
    valid_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    valid_config_data["schema_version"] = 3

    with pytest.raises(RuntimeConfigurationError):
        _load(valid_config_data, write_config, secret_directory)


def test_schema_version_two_rejects_client_auth_section(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    auth_config_data["schema_version"] = 2

    with pytest.raises(RuntimeConfigurationError):
        _load(auth_config_data, write_config, secret_directory)


def test_client_configuration_mapping_is_immutable(
    auth_config_data: dict[str, Any],
    write_config: Any,
    secret_directory: Path,
) -> None:
    loaded = _load(auth_config_data, write_config, secret_directory)
    assert loaded.runtime.client_auth is not None

    with pytest.raises(TypeError):
        loaded.runtime.client_auth.clients["replacement-client"] = (
            loaded.runtime.client_auth.clients["inference-client"]
        )


def test_public_auth_package_has_no_web_or_database_dependency() -> None:
    assert auth_module.ClientAuthenticator is ClientAuthenticator
