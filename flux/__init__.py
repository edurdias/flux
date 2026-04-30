# ruff: noqa: F403
# ruff: noqa: E402
from __future__ import annotations

import importlib as _importlib
import logging as _logging
import sys as _sys
import types as _types
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flux.domain.events import ExecutionEvent as ExecutionEvent
    from flux.domain.events import ExecutionEventType as ExecutionEventType
    from flux.domain.events import ExecutionState as ExecutionState
    from flux.domain.execution_context import ExecutionContext as ExecutionContext
    from flux.domain.schedule import Schedule as Schedule
    from flux.domain.schedule import ScheduleStatus as ScheduleStatus
    from flux.domain.schedule import ScheduleType as ScheduleType
    from flux.domain.schedule import cron as cron
    from flux.domain.schedule import interval as interval
    from flux.domain.schedule import once as once
    from flux.schedule_manager import create_schedule_manager as create_schedule_manager
    from flux.task import TaskMetadata as TaskMetadata
    from flux.task import task as task
    from flux.tasks.call import call as call
    from flux.tasks.pause import pause as pause
    from flux.workflow import workflow as workflow

_logging.getLogger("flux").addHandler(_logging.NullHandler())

__all__ = [
    "call",
    "pause",
    "task",
    "workflow",
    "TaskMetadata",
    "ExecutionEvent",
    "ExecutionState",
    "ExecutionEventType",
    "ExecutionContext",
    "cron",
    "interval",
    "once",
    "Schedule",
    "ScheduleType",
    "ScheduleStatus",
    "create_schedule_manager",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ExecutionEvent": ("flux.domain.events", "ExecutionEvent"),
    "ExecutionEventType": ("flux.domain.events", "ExecutionEventType"),
    "ExecutionState": ("flux.domain.events", "ExecutionState"),
    "ExecutionContext": ("flux.domain.execution_context", "ExecutionContext"),
    "call": ("flux.tasks.call", "call"),
    "pause": ("flux.tasks.pause", "pause"),
    "task": ("flux.task", "task"),
    "TaskMetadata": ("flux.task", "TaskMetadata"),
    "workflow": ("flux.workflow", "workflow"),
    "cron": ("flux.domain.schedule", "cron"),
    "interval": ("flux.domain.schedule", "interval"),
    "once": ("flux.domain.schedule", "once"),
    "Schedule": ("flux.domain.schedule", "Schedule"),
    "ScheduleType": ("flux.domain.schedule", "ScheduleType"),
    "ScheduleStatus": ("flux.domain.schedule", "ScheduleStatus"),
    "create_schedule_manager": ("flux.schedule_manager", "create_schedule_manager"),
}

# Names where a submodule (flux.task, flux.workflow) collides with an
# attribute we want to export.  When Python's import machinery sets e.g.
# flux.__dict__["task"] = <module flux.task>, a plain module-level
# __getattr__ is never called again for that name.  The custom module
# class below intercepts these via __getattr__ on the *instance*.
_SUBMODULE_OVERRIDES: dict[str, tuple[str, str]] = {
    "task": ("flux.task", "task"),
    "workflow": ("flux.workflow", "workflow"),
}

_WILDCARD_MODULES = (
    "flux.encoders",
    "flux.output_storage",
    "flux.secret_managers",
    "flux.tasks",
    "flux.catalogs",
    "flux.context_managers",
)

_wildcards_loaded = False
_logging_configured = False


def _ensure_logging() -> None:
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True
    from flux.utils import configure_logging

    configure_logging()


def _load_wildcards() -> None:
    global _wildcards_loaded
    if _wildcards_loaded:
        return
    _wildcards_loaded = True
    for mod_path in _WILDCARD_MODULES:
        mod = _importlib.import_module(mod_path)
        for name in getattr(mod, "__all__", dir(mod)):
            if not name.startswith("_"):
                globals()[name] = getattr(mod, name)


def _resolve(name: str):
    """Resolve a lazy import by name, returning the value or raising AttributeError."""
    if name == "logger":
        _ensure_logging()
        from flux.utils import get_logger

        val = get_logger("flux")
        return val
    if name in _LAZY_IMPORTS:
        _ensure_logging()
        mod_path, attr_name = _LAZY_IMPORTS[name]
        val = getattr(_importlib.import_module(mod_path), attr_name)
        return val
    _load_wildcards()
    # After loading wildcards, check if the name was added
    if name in globals():
        return globals()[name]
    raise AttributeError(f"module 'flux' has no attribute {name}")


class _FluxModule(_types.ModuleType):
    """Custom module that resolves submodule/attribute name collisions.

    When ``import flux.task`` runs, Python sets ``flux.__dict__["task"]``
    to the *module*.  A plain module-level ``__getattr__`` is only called
    when the name is *missing* from ``__dict__``, so it can't intercept
    the collision.  This class overrides ``__getattr__`` on the instance,
    which Python calls when normal ``__getattribute__`` doesn't find the
    name — and we make ``__getattribute__`` deliberately raise for the
    colliding names so that ``__getattr__`` gets its chance.
    """

    def __getattribute__(self, name: str):
        # For colliding names, check whether the value in __dict__ is a
        # module (set by the import system) vs. the real attribute we want.
        if name in _SUBMODULE_OVERRIDES:
            val = super().__getattribute__(name)
            if isinstance(val, _types.ModuleType):
                # Resolve to the actual attribute and cache it
                resolved = _resolve(name)
                object.__setattr__(self, name, resolved)
                return resolved
            return val
        return super().__getattribute__(name)

    def __getattr__(self, name: str):
        val = _resolve(name)
        object.__setattr__(self, name, val)
        return val


# Replace this module in sys.modules with our custom class instance.
_this = _sys.modules[__name__]
_mod = _FluxModule(__name__)
_mod.__dict__.update(
    {
        k: v
        for k, v in _this.__dict__.items()
        if not k.startswith("__")
        or k in ("__all__", "__path__", "__file__", "__package__", "__spec__")
    },
)
_sys.modules[__name__] = _mod
