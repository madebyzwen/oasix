"""Transport-neutral bearer authentication and explicit client authorization."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets as system_secrets
from dataclasses import dataclass

from oasix.auth.errors import (
    ClientAuthConfigurationError,
    ClientAuthenticationError,
    ClientAuthorizationError,
)
from oasix.config.models import ClientPermission, RuntimeConfig, SecretReference
from oasix.config.secrets import ResolvedSecrets

_MAX_AUTHORIZATION_HEADER_CHARACTERS = 8_192
_MAX_BEARER_TOKEN_CHARACTERS = 4_096
_BEARER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9\-._~+/]+=*", re.ASCII)


@dataclass(frozen=True, slots=True, repr=False)
class _Credential:
    digest: bytes
    client_id: str
    permissions: frozenset[ClientPermission]


@dataclass(frozen=True, slots=True)
class AuthenticatedClient:
    """Typed, immutable result of successful client authentication."""

    client_id: str
    permissions: frozenset[ClientPermission]

    def __repr__(self) -> str:
        permissions = ", ".join(sorted(permission.value for permission in self.permissions))
        return f"AuthenticatedClient(client_id={self.client_id!r}, permissions={{{permissions}}})"


class ClientAuthenticator:
    """Authenticate configured bearer keys without retaining their plaintext."""

    __slots__ = ("_credentials", "_pepper")

    def __init__(self, credentials: tuple[_Credential, ...], pepper: bytes) -> None:
        self._credentials = credentials
        self._pepper = pepper

    def __repr__(self) -> str:
        return f"ClientAuthenticator(credentials={len(self._credentials)})"

    def authenticate(self, authorization_header: str | None) -> AuthenticatedClient:
        """Return a typed identity or one uniform, non-revealing error."""

        try:
            return self._authenticate(authorization_header)
        except ClientAuthenticationError:
            authorization_header = None
        raise ClientAuthenticationError from None

    def _authenticate(self, authorization_header: str | None) -> AuthenticatedClient:
        token = _parse_bearer_header(authorization_header)
        candidate_digest = _digest_token(self._pepper, token)
        matched_credential: _Credential | None = None
        match_count = 0
        for credential in self._credentials:
            matched = hmac.compare_digest(candidate_digest, credential.digest)
            match_count += int(matched)
            if matched:
                matched_credential = credential

        token = ""
        authorization_header = None
        if match_count != 1 or matched_credential is None:
            raise ClientAuthenticationError
        return AuthenticatedClient(
            matched_credential.client_id,
            matched_credential.permissions,
        )


class ClientAuthorizer:
    """Require one explicit capability from an authenticated identity."""

    __slots__ = ()

    def require(
        self,
        identity: AuthenticatedClient,
        permission: ClientPermission,
    ) -> None:
        if type(identity) is not AuthenticatedClient:
            raise ClientAuthenticationError
        if type(permission) is not ClientPermission or permission not in identity.permissions:
            raise ClientAuthorizationError


def create_client_authenticator(
    runtime: RuntimeConfig,
    resolved_secrets: ResolvedSecrets,
) -> ClientAuthenticator:
    """Build startup auth state, rejecting ambiguous or shared credentials."""

    try:
        return _create_client_authenticator(runtime, resolved_secrets)
    except (ClientAuthConfigurationError, KeyError, UnicodeEncodeError):
        runtime = None  # type: ignore[assignment]
        resolved_secrets = None  # type: ignore[assignment]
    raise ClientAuthConfigurationError from None


def _create_client_authenticator(
    runtime: RuntimeConfig,
    resolved_secrets: ResolvedSecrets,
) -> ClientAuthenticator:
    if runtime.schema_version != 3 or runtime.client_auth is None:
        raise ClientAuthConfigurationError

    pepper = system_secrets.token_bytes(32)
    credentials: list[_Credential] = []
    seen_digests: list[bytes] = []
    provider_digests = _provider_secret_digests(runtime, resolved_secrets, pepper)

    for client_id, client in runtime.client_auth.clients.items():
        permissions = frozenset(client.permissions)
        for reference in client.key_secrets:
            secret = resolved_secrets.get(reference)
            token = secret.get_secret_value()
            if not _is_valid_bearer_token(token):
                raise ClientAuthConfigurationError
            digest = _digest_token(pepper, token)
            token = ""
            if any(hmac.compare_digest(digest, seen) for seen in seen_digests):
                raise ClientAuthConfigurationError
            if any(hmac.compare_digest(digest, provider) for provider in provider_digests):
                raise ClientAuthConfigurationError
            seen_digests.append(digest)
            credentials.append(
                _Credential(
                    digest=digest,
                    client_id=client_id,
                    permissions=permissions,
                )
            )

    if not credentials:
        raise ClientAuthConfigurationError
    return ClientAuthenticator(tuple(credentials), pepper)


def _provider_secret_digests(
    runtime: RuntimeConfig,
    resolved_secrets: ResolvedSecrets,
    pepper: bytes,
) -> tuple[bytes, ...]:
    references: set[SecretReference] = set()
    for worker in runtime.workers.values():
        if worker.connection.ssh is not None:
            references.add(worker.connection.ssh.private_key)
            references.add(worker.connection.ssh.known_hosts)
        for service in worker.services.values():
            if service.auth.method != "none":
                references.add(service.auth.secret)

    digests: list[bytes] = []
    for reference in references:
        secret = resolved_secrets.get(reference)
        value = secret.get_secret_value()
        digests.append(_digest_token(pepper, value))
        value = ""
    return tuple(digests)


def _parse_bearer_header(authorization_header: str | None) -> str:
    if (
        type(authorization_header) is not str
        or len(authorization_header) > _MAX_AUTHORIZATION_HEADER_CHARACTERS
    ):
        raise ClientAuthenticationError
    scheme, separator, token = authorization_header.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not _is_valid_bearer_token(token):
        raise ClientAuthenticationError
    return token


def _is_valid_bearer_token(token: str) -> bool:
    return (
        type(token) is str
        and 0 < len(token) <= _MAX_BEARER_TOKEN_CHARACTERS
        and _BEARER_TOKEN_PATTERN.fullmatch(token) is not None
    )


def _digest_token(pepper: bytes, token: str) -> bytes:
    return hmac.digest(pepper, token.encode("utf-8"), hashlib.sha256)
