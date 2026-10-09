"""Minimal bootstrap settings used to locate external configuration sources."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BootstrapSettings(BaseSettings):
    """Locate runtime configuration and secrets without defining runtime behavior."""

    model_config = SettingsConfigDict(
        env_prefix="OASIX_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    config_file: Path = Field(
        description="Path to the external runtime YAML file",
        repr=False,
    )
    secrets_directory: Path = Field(
        default=Path("/run/secrets"),
        description="Directory containing externally provisioned secret files",
        repr=False,
    )
