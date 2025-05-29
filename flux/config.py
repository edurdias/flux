from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

import tomli
from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class BaseConfig(BaseModel):
    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class WorkersConfig(BaseConfig):
    """Configuration for workflow executor."""

    bootstrap_token: str = Field(
        default_factory=lambda: uuid4().hex,
        description="Token for bootstrapping workers",
    )

    server_url: str = Field(
        default="http://localhost:8000",
        description="Default server URL to connect to",
    )
    default_timeout: int = Field(default=0, description="Default task timeout in seconds")
    retry_attempts: int = Field(default=3, description="Default number of retry attempts")
    retry_delay: int = Field(default=1, description="Default delay between retries in seconds")
    retry_backoff: int = Field(default=2, description="Default backoff multiplier for retries")


class MCPConfig(BaseConfig):
    """Configuration for MCP server."""

    name: str = Field(default="flux-workflows", description="Name for the MCP server")

    host: str = Field(default="localhost", description="Host to bind the MCP server to")
    port: int = Field(default=8080, description="Port for the MCP server")
    server_url: str = Field(
        default="http://localhost:8000",
        description="Default server URL to connect to",
    )
    transport: Literal["stdio", "streamable-http", "sse"] = Field(
        default="sse",
        description="Transport protocol for MCP (stdio, streamable-http, sse)",
    )


class EncryptionConfig(BaseConfig):
    """Security-related configuration."""

    encryption_key: str | None = Field(
        default=None,
        description="Encryption key for sensitive data",
    )


class FluxConfig(BaseSettings):
    """Main configuration class for Flux framework."""

    model_config = SettingsConfigDict(
        env_prefix="FLUX_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    debug: bool = Field(default=False, description="Enable debug mode")
    log_level: str = Field(default="INFO", description="Logging level")
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log message format",
    )
    log_date_format: str = Field(
        default="%Y-%m-%d %H:%M:%S",
        description="Date format in log messages",
    )
    server_port: int = Field(default=8000, description="Port for the server")
    server_host: str = Field(default="localhost", description="Host for the server")
    home: str = Field(default=".flux", description="Home directory for Flux")
    cache_path: str = Field(default=".cache", description="Path for cache directory")
    local_storage_path: str = Field(default=".data", description="Path for local storage directory")
    serializer: str = Field(default="pkl", description="Default serializer (json or pkl)")
    database_url: str = Field(default="sqlite:///.flux/flux.db", description="Database URL")

    workers: WorkersConfig = Field(default_factory=WorkersConfig)
    security: EncryptionConfig = Field(default_factory=EncryptionConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)

    @field_validator("serializer")
    def validate_serializer(cls, v: str) -> str:
        if v not in ["json", "pkl"]:
            raise ValueError("Serializer must be either 'json' or 'pkl'")
        return v

    @classmethod
    def load(cls) -> FluxConfig:
        """
        Load configuration from multiple sources in order of precedence:
        1. Environment variables
        2. flux.toml
        3. pyproject.toml
        4. Default values
        """
        # Try to load from flux.toml
        config = cls._load_from_config()

        # Try to load from pyproject.toml
        config = {**config, **cls._load_from_pyproject()}

        # Create instance with both flux.toml, pyproject.toml and env vars
        # Environment variables will take precedence over flux.toml and pyproject.toml
        return cls(**config)

    @staticmethod
    def _load_from_pyproject() -> dict[str, Any]:
        """Load configuration from pyproject.toml if available."""
        return FluxConfig._load_from_toml("pyproject.toml", ["tool", "flux"])

    @staticmethod
    def _load_from_config() -> dict[str, Any]:
        """Load configuration from flux.toml if available."""
        return FluxConfig._load_from_toml("flux.toml", ["flux"])

    @staticmethod
    def _load_from_toml(file_name: str, keys: list[str]) -> dict[str, Any]:
        """Load configuration from a TOML file if available."""
        file_path = Path(file_name)
        if not file_path.exists():
            return {}

        try:
            with open(file_path, "rb") as f:
                config = tomli.load(f)
                for key in keys:
                    config = config.get(key, {})
                return config
        except Exception:
            return {}


class Configuration:
    """
    Configuration manager for Flux framework.
    Implements thread-safe singleton pattern.
    """

    _instance: Configuration | None = None
    _lock: Lock = Lock()
    _config: FluxConfig | None = None

    def __new__(cls) -> Configuration:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # Double-checking pattern
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Initialize only if not already initialized
        if self._config is None:
            with self._lock:
                if self._config is None:  # Double-checking pattern
                    self._config = FluxConfig.load()

    @property
    def settings(self) -> FluxConfig:
        """Get the current configuration settings."""
        if self._config is None:
            with self._lock:
                if self._config is None:
                    self._config = FluxConfig.load()
        return self._config

    def reload(self) -> None:
        """Reload configuration from sources."""
        with self._lock:
            self._config = FluxConfig.load()

    def override(self, **kwargs) -> None:
        """
        Override specific configuration values.
        Useful for testing or temporary changes.
        """
        with self._lock:
            if self._config is None:
                self._config = FluxConfig.load()

            # Create a new config with the overrides
            config_dict = self._config.model_dump()
            self._update_nested_dict(config_dict, kwargs)
            self._config = FluxConfig(**config_dict)

    def reset(self) -> None:
        """Reset configuration to default values."""
        with self._lock:
            self._config = None

    def _update_nested_dict(self, d: dict, u: dict) -> None:
        """Recursively update nested dictionary."""
        for k, v in u.items():
            if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                self._update_nested_dict(d[k], v)
            else:
                d[k] = v

    @staticmethod
    def get() -> Configuration:
        """Get the current configuration settings."""
        return Configuration()
