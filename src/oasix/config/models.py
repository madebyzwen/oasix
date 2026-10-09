"""Strict, hardware-independent runtime configuration models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

type Identifier = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9_-]*$"),
]
type NonBlankString = Annotated[StrictStr, StringConstraints(min_length=1)]
type Port = Annotated[StrictInt, Field(ge=1, le=65535)]
type PositiveSeconds = Annotated[StrictInt, Field(gt=0)]
type NonNegativeSeconds = Annotated[StrictInt, Field(ge=0)]
type PositiveLimit = Annotated[StrictInt, Field(ge=1)]
type NonNegativeFloat = Annotated[StrictFloat, Field(ge=0.0)]
type PositiveMultiplier = Annotated[StrictFloat, Field(ge=1.0)]
type JitterRatio = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
type StatusCode = Annotated[StrictInt, Field(ge=100, le=599)]
type BusyTimeoutMilliseconds = Annotated[StrictInt, Field(ge=1, le=60_000)]
type PositiveInterval = Annotated[StrictFloat, Field(gt=0.0)]


class StrictModel(BaseModel):
    """Forbid silent typos and prevent replacement of validated fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SecretReference(StrictModel):
    """A reference to a value held by an external secret provider."""

    source: Literal["file"]
    name: Annotated[
        StrictStr,
        StringConstraints(
            min_length=1,
            max_length=255,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        ),
    ]

    @field_validator("name")
    @classmethod
    def reject_relative_path_names(cls, value: str) -> str:
        if value in {".", ".."}:
            raise ValueError("relative path names are not allowed")
        return value


class ClientPermission(StrEnum):
    """Explicit capabilities which a configured client may request."""

    INFERENCE = "inference"
    ADMINISTRATION = "administration"


class ClientIdentitySettings(StrictModel):
    """Secret references and explicit permissions for one generic API client."""

    key_secrets: Annotated[tuple[SecretReference, ...], Field(min_length=1)] = Field(repr=False)
    permissions: Annotated[tuple[ClientPermission, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def reject_duplicates(self) -> ClientIdentitySettings:
        if len(self.key_secrets) != len(set(self.key_secrets)):
            raise ValueError("client key secret references must be unique")
        if len(self.permissions) != len(set(self.permissions)):
            raise ValueError("client permissions must be unique")
        return self


class ClientAuthenticationSettings(StrictModel):
    """Configured API clients; credentials remain external secret references."""

    clients: Annotated[Mapping[Identifier, ClientIdentitySettings], Field(min_length=1)]

    @field_validator("clients")
    @classmethod
    def freeze_clients(
        cls, value: Mapping[str, ClientIdentitySettings]
    ) -> Mapping[str, ClientIdentitySettings]:
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def reject_shared_secret_references(self) -> ClientAuthenticationSettings:
        references = [
            reference for client in self.clients.values() for reference in client.key_secrets
        ]
        if len(references) != len(set(references)):
            raise ValueError("client key secret references must not be shared")
        return self


class SshConnection(StrictModel):
    port: Port
    user: NonBlankString
    private_key: SecretReference
    known_hosts: SecretReference

    @field_validator("user")
    @classmethod
    def reject_whitespace_only_user(cls, value: str) -> str:
        if value.isspace():
            raise ValueError("SSH user must not be whitespace")
        return value


class WorkerConnection(StrictModel):
    host: NonBlankString
    ssh: SshConnection | None

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if value.isspace() or any(character.isspace() for character in value):
            raise ValueError("host must not contain whitespace")
        return value


class NoWake(StrictModel):
    method: Literal["none"]


class WakeOnLan(StrictModel):
    method: Literal["wol"]
    mac_address: StrictStr
    broadcast_host: NonBlankString
    port: Port

    @field_validator("mac_address")
    @classmethod
    def validate_mac_address(cls, value: str) -> str:
        if re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", value) is None:
            raise ValueError("invalid MAC address")
        address = bytes.fromhex(value.replace(":", ""))
        if address == b"\xff" * 6:
            raise ValueError("broadcast MAC address is not allowed")
        if address[0] & 0x01:
            raise ValueError("multicast MAC address is not allowed")
        return value

    @field_validator("broadcast_host")
    @classmethod
    def validate_broadcast_host(cls, value: str) -> str:
        if value.isspace() or any(character.isspace() for character in value):
            raise ValueError("broadcast host must not contain whitespace")
        return value


WakeSettings = Annotated[NoWake | WakeOnLan, Field(discriminator="method")]


class NoSleep(StrictModel):
    method: Literal["none"]


class SshCommandSleep(StrictModel):
    method: Literal["ssh_command"]
    command: Annotated[tuple[NonBlankString, ...], Field(min_length=1)]

    @field_validator("command")
    @classmethod
    def reject_blank_command_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(argument.isspace() for argument in value):
            raise ValueError("sleep command arguments must not be whitespace")
        return value


SleepSettings = Annotated[NoSleep | SshCommandSleep, Field(discriminator="method")]


class PowerSettings(StrictModel):
    wake: WakeSettings
    sleep: SleepSettings


class NoAuthentication(StrictModel):
    method: Literal["none"]


class BearerAuthentication(StrictModel):
    method: Literal["bearer"]
    secret: SecretReference


class HeaderAuthentication(StrictModel):
    method: Literal["header"]
    header_name: Annotated[
        StrictStr,
        StringConstraints(
            min_length=1,
            max_length=128,
            pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$",
        ),
    ]
    secret: SecretReference


AuthenticationSettings = Annotated[
    NoAuthentication | BearerAuthentication | HeaderAuthentication,
    Field(discriminator="method"),
]


class HttpReadinessProbe(StrictModel):
    type: Literal["http"]
    method: Literal["GET", "HEAD"]
    path: StrictStr
    expected_status_codes: Annotated[frozenset[StatusCode], Field(min_length=1)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or any(character.isspace() for character in value)
            or "?" in value
            or "#" in value
        ):
            raise ValueError(
                "readiness path must be an absolute URL path without query or fragment"
            )
        return value


ReadinessProbe = Annotated[HttpReadinessProbe, Field(discriminator="type")]


class ServiceSettings(StrictModel):
    kind: Identifier
    enabled: StrictBool
    endpoint: AnyHttpUrl
    capabilities: tuple[Identifier, ...] = ()
    auth: AuthenticationSettings
    readiness: ReadinessProbe

    @field_validator("endpoint")
    @classmethod
    def reject_endpoint_credentials_and_parameters(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username is not None or value.password is not None:
            raise ValueError("service endpoint must not contain URL user information")
        if value.query is not None:
            raise ValueError("service endpoint must not contain query parameters")
        if value.fragment is not None:
            raise ValueError("service endpoint must not contain a fragment")
        return value

    @field_validator("capabilities")
    @classmethod
    def reject_duplicate_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("service capabilities must be unique")
        return value


class WorkerProfile(StrictModel):
    connection: WorkerConnection
    power: PowerSettings
    services: Annotated[Mapping[Identifier, ServiceSettings], Field(min_length=1)]

    @field_validator("services")
    @classmethod
    def freeze_services(cls, value: Mapping[str, ServiceSettings]) -> Mapping[str, ServiceSettings]:
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def require_ssh_for_ssh_sleep(self) -> WorkerProfile:
        if self.power.sleep.method == "ssh_command" and self.connection.ssh is None:
            raise ValueError("SSH connection is required for ssh_command sleep")
        return self


class JitterSettings(StrictModel):
    ratio: JitterRatio


class RetryPolicy(StrictModel):
    max_attempts: PositiveLimit
    initial_delay_seconds: NonNegativeFloat
    multiplier: PositiveMultiplier
    max_delay_seconds: NonNegativeFloat
    jitter: JitterSettings | None

    @model_validator(mode="after")
    def validate_delay_range(self) -> RetryPolicy:
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("maximum retry delay must not be less than the initial delay")
        return self


class RetryPolicies(StrictModel):
    job: RetryPolicy
    wake: RetryPolicy


class ConcurrencyLimits(StrictModel):
    max_concurrent_jobs: PositiveLimit
    max_concurrent_inference_requests: PositiveLimit


class InferencePolicy(StrictModel):
    """Bounded request and persistent lease timing for interactive inference."""

    request_timeout_seconds: PositiveSeconds
    lease_ttl_seconds: PositiveSeconds
    heartbeat_interval_seconds: PositiveInterval

    @model_validator(mode="after")
    def require_heartbeat_before_expiry(self) -> InferencePolicy:
        if self.heartbeat_interval_seconds >= self.lease_ttl_seconds:
            raise ValueError("lease heartbeat interval must be shorter than its TTL")
        return self


class Policies(StrictModel):
    idle_timeout_seconds: PositiveSeconds
    readiness_timeout_seconds: PositiveSeconds
    force_sleep_grace_period_seconds: NonNegativeSeconds
    retry: RetryPolicies
    concurrency: ConcurrencyLimits
    inference: InferencePolicy | None = None


class PersistenceSettings(StrictModel):
    """Validated runtime settings for the local SQLite persistence file."""

    database_path: Path = Field(repr=False)
    busy_timeout_ms: BusyTimeoutMilliseconds = 5_000

    @field_validator("database_path")
    @classmethod
    def require_absolute_database_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("database path must be absolute")
        return value


class RuntimeConfig(StrictModel):
    """Fully validated runtime behavior; no bootstrap source locations are included."""

    schema_version: Literal[2, 3, 4]
    active_worker: Identifier
    workers: Annotated[Mapping[Identifier, WorkerProfile], Field(min_length=1)]
    policies: Policies
    persistence: PersistenceSettings
    client_auth: ClientAuthenticationSettings | None = None

    @field_validator("workers")
    @classmethod
    def freeze_workers(cls, value: Mapping[str, WorkerProfile]) -> Mapping[str, WorkerProfile]:
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def require_active_worker_profile(self) -> RuntimeConfig:
        if self.active_worker not in self.workers:
            raise ValueError("active_worker must reference a configured worker profile")
        return self

    @model_validator(mode="after")
    def require_versioned_client_auth(self) -> RuntimeConfig:
        if self.schema_version == 2 and self.client_auth is not None:
            raise ValueError("client_auth requires runtime schema version 3")
        if self.schema_version in {3, 4} and self.client_auth is None:
            raise ValueError("runtime schema version 3 or 4 requires client_auth")
        if self.schema_version in {2, 3} and self.policies.inference is not None:
            raise ValueError("inference policy requires runtime schema version 4")
        if self.schema_version == 4 and self.policies.inference is None:
            raise ValueError("runtime schema version 4 requires inference policy")
        return self

    @model_validator(mode="after")
    def separate_client_and_provider_secret_references(self) -> RuntimeConfig:
        if self.client_auth is None:
            return self

        client_references = {
            reference
            for client in self.client_auth.clients.values()
            for reference in client.key_secrets
        }
        provider_references: set[SecretReference] = set()
        for worker in self.workers.values():
            if worker.connection.ssh is not None:
                provider_references.add(worker.connection.ssh.private_key)
                provider_references.add(worker.connection.ssh.known_hosts)
            for service in worker.services.values():
                if service.auth.method != "none":
                    provider_references.add(service.auth.secret)
        if client_references & provider_references:
            raise ValueError("client and provider secret references must be separate")
        return self

    @property
    def active_worker_profile(self) -> WorkerProfile:
        return self.workers[self.active_worker]
