"""A parent-provided settings snapshot for runner children (issue #241).

Everything the child's execution path reads from ``Configuration.get()`` —
logging setup, the per-task auth check, cache and storage paths, schedule
tolerances, AI provider descriptors — costs a pydantic import the sandbox
otherwise never needs (~190ms, paid per execution on container runners).
The parent snapshots the relevant slice of its real settings into the child
environment; the child installs a plain-namespace stand-in as
``sys.modules["flux.config"]`` before anything resolves the module, so every
downstream ``Configuration.get().settings.x`` works unmodified and pydantic
never loads.

Security: the snapshot is an ALLOWLIST. The child environment is
deliberately sanitized (no bootstrap token, no ``FLUX_SECURITY__*``, no
database URL), and a full ``model_dump()`` would smuggle those credentials
right back in. Only the keys below travel; of the security tree, exactly
``auth.enabled`` — the boolean the task gate branches on.

Compatibility: no snapshot in the environment (an older parent, or a
worker/image version skew) means nothing is installed and the child imports
the real ``flux.config`` as before.
"""

from __future__ import annotations

import json
import os
import sys
import types

CHILD_SETTINGS_ENV = "FLUX_CHILD_SETTINGS"

# What the child's execution path reads, and nothing else. A missing setting
# fails loudly in _Section.__getattr__ with instructions, so extending this
# is a one-line change caught by the parity test.
_TOP_LEVEL = (
    "log_level",
    "log_format",
    "log_date_format",
    "serializer",
    "home",
    "cache_path",
    "local_storage_path",
)
_WORKERS = ("server_url", "default_timeout", "transient_fast_path")
_SCHEDULING = ("schedule_check_tolerance", "once_schedule_tolerance")


def snapshot() -> str:
    """Parent side: the allowlisted slice of the live settings, as JSON."""
    from flux.config import Configuration

    settings = Configuration.get().settings
    data: dict = {name: getattr(settings, name) for name in _TOP_LEVEL}
    data["workers"] = {name: getattr(settings.workers, name) for name in _WORKERS}
    data["scheduling"] = {name: getattr(settings.scheduling, name) for name in _SCHEDULING}
    data["security"] = {
        "auth": {"enabled": settings.security.auth.enabled},
        # Explicitly keyless: the child must never hold the encryption key
        # (its environment strips FLUX_SECURITY__* for the same reason), and
        # integrity signing no-ops without one — exactly the pre-snapshot
        # behavior of a sanitized child reading real config. The None is the
        # deliberate absence of the secret, stated rather than implied.
        "encryption": {"encryption_key": None},
    }
    # Provider descriptors carry key NAMES (env var / secret store), never
    # key values — agent tasks in the child resolve the values themselves
    # through the parent pipe.
    data["ai"] = settings.ai.model_dump()
    return json.dumps(data, default=str)


class _Section:
    """Attribute access over the snapshot dict, sections nested."""

    def __init__(self, data: dict, path: str):
        self._data = data
        self._path = path

    def __getattr__(self, name: str):
        try:
            value = self._data[name]
        except KeyError:
            raise AttributeError(
                f"setting '{self._path}.{name}' is not in the child snapshot; "
                f"add it to the allowlist in flux/runners/child_settings.py",
            ) from None
        if isinstance(value, dict):
            return _Section(value, f"{self._path}.{name}")
        return value


class _Shim:
    """Stands in for flux.config.Configuration inside the child."""

    _instance: _Shim | None = None

    def __init__(self, data: dict):
        self.settings = _Section(data, "settings")

    @classmethod
    def get(cls) -> _Shim:
        if cls._instance is None:
            raise RuntimeError(
                "child settings shim installed but never initialized — "
                "install_from_env() is the only supported entry point",
            )
        return cls._instance

    def override(self, **kwargs):
        raise RuntimeError(
            "Configuration.override() is unavailable in a runner child: its "
            "settings are a read-only snapshot provided by the worker",
        )

    def reset(self):
        raise RuntimeError(
            "Configuration.reset() is unavailable in a runner child: its "
            "settings are a read-only snapshot provided by the worker",
        )

    def reload(self):
        raise RuntimeError(
            "Configuration.reload() is unavailable in a runner child: its "
            "settings are a read-only snapshot provided by the worker",
        )


def install_from_env() -> bool:
    """Child side: install the flux.config stand-in if a snapshot is present.

    Must run before anything imports flux.config — in practice, first thing
    in the child's main(). Returns whether the stand-in was installed.
    """
    raw = os.environ.get(CHILD_SETTINGS_ENV)
    if not raw:
        return False
    if "flux.config" in sys.modules:  # pragma: no cover - defensive
        # Too late to shim: the real module (and pydantic) already loaded.
        return False
    data = json.loads(raw)
    shim = _Shim(data)
    _Shim._instance = shim
    module = types.ModuleType("flux.config")
    module.Configuration = _Shim  # type: ignore[attr-defined]

    def _missing(name: str):
        raise AttributeError(
            f"flux.config.{name} is unavailable in a runner child: the "
            "sandbox sees a parent-provided settings snapshot, not the "
            "config machinery (issue #241)",
        )

    module.__getattr__ = _missing  # type: ignore[attr-defined]
    sys.modules["flux.config"] = module
    # Bind the parent-package attribute the way a real import would, so
    # attribute-style access (flux.config.X) reaches the shim instead of
    # falling into the flux package's lazy-resolution machinery.
    flux_pkg = sys.modules.get("flux")
    if flux_pkg is not None:
        flux_pkg.config = module  # type: ignore[attr-defined]
    return True
