from __future__ import annotations

import os
import re
from pathlib import Path
from threading import Lock
from typing import Any, Literal

import tomli
from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from flux.observability.config import ObservabilityConfig


class BaseConfig(BaseModel):
    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class WorkersConfig(BaseConfig):
    """Configuration for workflow executor."""

    bootstrap_token: str | None = Field(
        default=None,
        description=(
            "Token for bootstrapping workers. When unset on the server, one is "
            "auto-generated and persisted at <home>/bootstrap-token on first start; "
            "retrieve it with 'flux server bootstrap-token'. Workers must always "
            "be supplied an explicit value."
        ),
    )

    bootstrap_token_enabled: bool = Field(
        default=True,
        description=(
            "Accept the shared bootstrap token on POST /workers/register. "
            "Disable once a fleet has migrated to one-time join tokens "
            "('flux server join-token') so the fleet-wide secret stops "
            "being a registration credential."
        ),
    )
    join_token_ttl: int = Field(
        default=3600,
        description=(
            "Default lifetime in seconds for one-time worker join tokens "
            "minted via 'flux server join-token' or POST "
            "/admin/workers/join-tokens"
        ),
    )

    server_url: str = Field(
        default="http://localhost:8000",
        description="Default server URL to connect to",
    )
    default_timeout: int = Field(default=0, description="Default task timeout in seconds")
    http_timeout: int = Field(
        default=30,
        description="Timeout in seconds for worker HTTP calls to the server (0 disables)",
    )
    checkpoint_retry_max_delay: int = Field(
        default=30,
        description="Backoff cap in seconds between checkpoint send retries",
    )
    terminal_checkpoint_deadline: int = Field(
        default=300,
        description=(
            "Max seconds to keep retrying a terminal (finished-state) checkpoint "
            "before giving up and leaving the execution to the server reaper"
        ),
    )
    retry_attempts: int = Field(default=3, description="Default number of retry attempts")
    retry_delay: int = Field(default=1, description="Default delay between retries in seconds")
    retry_backoff: int = Field(default=2, description="Default backoff multiplier for retries")
    heartbeat_interval: int = Field(default=10, description="Seconds between server ping events")
    heartbeat_timeout: int = Field(
        default=30,
        description="Seconds before a worker is considered stale",
    )
    reconnect_max_delay: int = Field(
        default=60,
        description="Max backoff cap in seconds for worker reconnect",
    )
    eviction_grace_period: int = Field(
        default=30,
        description="Seconds to wait after marking worker stale before evicting",
    )
    offline_ttl: int = Field(
        default=7200,
        description="Seconds to keep offline workers in memory before pruning",
    )
    module_cache_ttl: int = Field(
        default=300,
        description="Seconds to cache compiled workflow modules (0 to disable)",
    )
    module_cache_max_size: int = Field(
        default=64,
        description=(
            "Maximum number of compiled workflow modules kept in the cache; "
            "the least-recently-used entry is evicted beyond this "
            "(0 = unbounded, the legacy behavior)"
        ),
    )
    runners: list[str] = Field(
        default=["inprocess", "subprocess"],
        description=(
            "Runners enabled on this worker, advertised at registration; "
            "workflows declaring runner=... only dispatch to workers that "
            "advertise it"
        ),
    )
    default_runner: str = Field(
        default="subprocess",
        description=(
            "Runner used when a workflow does not declare one. 'subprocess' "
            "(the default) isolates each execution in its own process; "
            "'inprocess' runs it on the worker's event loop — lower latency, "
            "no fault isolation"
        ),
    )
    subprocess_term_grace: float = Field(
        default=10.0,
        description=(
            "Seconds the subprocess runner waits after SIGTERM for a child "
            "to finish its cancellation handling before SIGKILL"
        ),
    )
    subprocess_memory_limit: int = Field(
        default=0,
        description=(
            "Address-space limit in bytes applied to each runner child "
            "process (Linux only, 0 = unlimited)"
        ),
    )
    docker_image: str = Field(
        default="",
        description=(
            "Image the docker runner launches per execution; must have "
            "flux-core installed at a worker-compatible version. Required "
            "when 'docker' is in runners"
        ),
    )
    docker_network: str = Field(
        default="",
        description="Docker network for runner containers (empty = docker default)",
    )
    docker_memory: str = Field(
        default="",
        description="Per-container memory limit, docker syntax (e.g. '512m'; empty = unlimited)",
    )
    docker_cpus: float = Field(
        default=0.0,
        description="Per-container CPU limit (docker --cpus; 0 = unlimited)",
    )
    docker_extra_args: list[str] = Field(
        default=[],
        description=(
            "Extra arguments inserted into 'docker run' before the image "
            "(e.g. volumes, env vars, --user)"
        ),
    )
    loop_lag_threshold: float = Field(
        default=1.0,
        ge=0,
        description=(
            "Event-loop lag in seconds beyond which a health probe counts as "
            "a breach; three consecutive breaches mark the worker unhealthy "
            "(it declines new work and advertises the state on heartbeats "
            "until three clean probes). 0 disables self-health monitoring"
        ),
    )
    loop_lag_probe_interval: float = Field(
        default=1.0,
        gt=0,
        description="Seconds between event-loop lag probes",
    )
    metrics_provider: str | None = Field(
        default=None,
        description=(
            "Dotted path ('package.module:callable') to a sync or async "
            "callable returning dict[str, float]. The worker advertises the "
            "snapshot on heartbeat pongs; routing policies read it through "
            "'metric:*' selectors"
        ),
    )
    metrics_interval: float = Field(
        default=10.0,
        gt=0,
        description="Seconds between metrics-provider refreshes",
    )
    builtin_metrics: bool = Field(
        default=True,
        description=(
            "Publish the built-in 'flux.*' worker metrics (loop lag, load, "
            "failure/crash rates, durations, cpu/memory, ...) on heartbeats "
            "so routing policies can rank on them without a metrics_provider"
        ),
    )
    transient_fast_path: bool = Field(
        default=True,
        description=(
            "Execute call() targets that are transient workflow objects "
            "in-process (same worker, no dispatch round-trip, no execution "
            "row) — the mesh fast path. Disable to force every call() "
            "through the server"
        ),
    )
    max_concurrent_executions: int = Field(
        default=16,
        description=(
            "Capacity the worker advertises at registration: the server never "
            "assigns more than this many concurrent executions to it "
            "(0 = unlimited, the legacy behavior)"
        ),
    )
    drain_timeout: int = Field(
        default=60,
        description=(
            "Seconds a stopping worker waits for running executions to finish "
            "before cancelling them (0 = cancel immediately)"
        ),
    )
    register_rate_limit: str = Field(
        default="30/minute",
        description=(
            "Per-client-IP rate limit for POST /workers/register, guarding the "
            "shared bootstrap token against online brute force. slowapi syntax "
            "(e.g. '30/minute'); empty string disables. Raise it for large "
            "fleets restarting behind a shared NAT."
        ),
    )


class RetentionConfig(BaseConfig):
    """Configuration for execution-history retention."""

    enabled: bool = Field(
        default=False,
        description=(
            "Delete terminal executions (and their events/approvals/sessions) "
            "older than retention_days. Off by default so upgrades never "
            "silently remove history; enable it in production or the "
            "executions/execution_events tables grow without bound."
        ),
    )
    retention_days: int = Field(
        default=30,
        description="Age (days since last event) after which terminal executions are deleted",
    )
    sweep_interval: int = Field(
        default=3600,
        description="Seconds between retention sweeps",
    )
    batch_size: int = Field(
        default=500,
        description="Executions deleted per transaction during a sweep",
    )


class DispatchConfig(BaseConfig):
    """Configuration for server-side execution dispatch."""

    mode: Literal["poll", "event"] = Field(
        default="poll",
        description=(
            "Dispatch strategy. 'poll' runs the legacy per-worker query loop "
            "(~5 queries per worker per 0.5s). 'event' runs one dispatcher task "
            "per replica that batch-claims work on wakeups (LISTEN/NOTIFY on "
            "PostgreSQL) — the scalable mode for large worker fleets."
        ),
    )
    batch_size: int = Field(
        default=64,
        description="Max executions claimed per dispatcher wakeup (event mode)",
    )
    fallback_interval: float = Field(
        default=15.0,
        description=(
            "Dispatcher safety-net tick in seconds (event mode); covers missed "
            "notifications, which are wakeups only and carry no data"
        ),
    )


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
        default="streamable-http",
        description="Transport protocol for MCP (stdio, streamable-http, sse)",
    )


class EncryptionConfig(BaseConfig):
    """Security-related configuration."""

    encryption_key: str | None = Field(
        default=None,
        description="Encryption key for sensitive data",
    )


class SchedulingConfig(BaseConfig):
    """Configuration for workflow scheduling."""

    poll_interval: float = Field(
        default=30.0,
        description="Interval in seconds between scheduler polls for due schedules",
    )
    schedule_check_tolerance: float = Field(
        default=1.0,
        description="Time tolerance in seconds for cron schedule matching",
    )
    once_schedule_tolerance: float = Field(
        default=60.0,
        description="Time tolerance in seconds for one-time schedule matching",
    )
    auto_schedule_enabled: bool = Field(
        default=True,
        description="Enable automatic schedule creation from workflow decorator",
    )
    auto_schedule_suffix: str = Field(
        default="_auto",
        description="Suffix for auto-created schedule names",
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
    server_max_body_size: int = Field(
        default=64 * 1024 * 1024,
        description=(
            "Maximum HTTP request body size in bytes accepted by the server "
            "(413 beyond it); checkpoint, run-input, and progress bodies are "
            "otherwise unbounded dill payloads read into memory. 0 disables "
            "the limit."
        ),
    )
    server_host: str = Field(default="localhost", description="Host for the server")
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        description=(
            "Allowed CORS origins. '*' permits any origin; browsers reject '*' "
            "together with credentials, so credentials are forced off for wildcard."
        ),
    )
    cors_allow_credentials: bool = Field(
        default=False,
        description=(
            "Allow credentials (cookies/Authorization) in CORS requests. Ignored "
            "(treated as False) when cors_allow_origins contains '*'."
        ),
    )
    home: str = Field(default=".flux", description="Home directory for Flux")
    cache_path: str = Field(default=".cache", description="Path for cache directory")
    local_storage_path: str = Field(default=".data", description="Path for local storage directory")
    serializer: str = Field(default="pkl", description="Default serializer (json or pkl)")
    database_url: str = Field(
        default="sqlite:///.flux/flux.db",
        description="Database URL with environment variable support",
    )
    database_type: Literal["sqlite", "postgresql"] = Field(
        default="sqlite",
        description="Database backend type",
    )
    database_pool_size: int = Field(
        default=20,
        description="Database connection pool size (PostgreSQL only)",
    )
    database_max_overflow: int = Field(
        default=20,
        description="Maximum pool overflow (PostgreSQL only)",
    )
    database_executor_threads: int = Field(
        default=16,
        description=(
            "Size of the server's thread pool for blocking database calls "
            "(asyncio.to_thread). Size it at or below the connection pool so "
            "threads never block waiting for a connection. 0 keeps the "
            "asyncio default executor."
        ),
    )
    database_pool_timeout: int = Field(
        default=30,
        description="Database connection timeout in seconds",
    )
    database_pool_recycle: int = Field(
        default=3600,
        description="Connection recycle time in seconds",
    )
    database_health_check_interval: int = Field(
        default=300,
        description="Health check interval in seconds",
    )

    workers: WorkersConfig = Field(default_factory=WorkersConfig)
    security: SecurityConfig = Field(
        default_factory=lambda: __import__(
            "flux.security.config",
            fromlist=["SecurityConfig"],
        ).SecurityConfig(),
    )  # type: ignore[name-defined]
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    dispatch: DispatchConfig = Field(default_factory=DispatchConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @field_validator("database_url")
    def interpolate_database_url(cls, v: str) -> str:
        """Interpolate environment variables in database URL"""
        # Pattern to match ${VAR_NAME} or $VAR_NAME
        pattern = r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)"

        def replace_var(match):
            var_name = match.group(1) or match.group(2)
            return os.getenv(var_name, match.group(0))

        return re.sub(pattern, replace_var, v)

    @field_validator("database_type")
    def infer_database_type(cls, v: str, info) -> str:
        """Auto-infer database type from URL if not explicitly set"""
        if v == "sqlite":  # Default case
            database_url = info.data.get("database_url", "")
            if database_url.startswith(("postgresql://", "postgresql+")):
                return "postgresql"
        return v

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
        # Lower precedence: pyproject.toml [tool.flux]
        config = cls._load_from_pyproject()

        # Higher precedence: flux.toml overrides pyproject.toml
        config = {**config, **cls._load_from_config()}

        # Pydantic-settings gives init kwargs higher priority than env vars.
        # To honour the documented precedence (env vars beat config files), drop
        # leaf keys from the toml config that are explicitly set via env vars
        # (FLUX_<SECTION>__<KEY> format), so pydantic-settings reads those
        # specific fields from env vars instead of the toml values.
        env_prefix = "FLUX_"
        env_delimiter = "__"

        def drop_env_overrides(d: dict, path: str = "") -> dict:
            result: dict = {}
            for k, v in d.items():
                env_key = f"{env_prefix}{path}{k}".upper()
                if isinstance(v, dict):
                    nested = drop_env_overrides(v, f"{path}{k}{env_delimiter}")
                    if nested:
                        result[k] = nested
                elif env_key not in os.environ:
                    result[k] = v
            return result

        filtered_config = drop_env_overrides(config)

        # Create instance with toml values as fallback; env vars are applied by pydantic-settings
        return cls(**filtered_config)

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


from flux.security.config import SecurityConfig  # noqa: E402

SecurityConfig.model_rebuild()
FluxConfig.model_rebuild()
