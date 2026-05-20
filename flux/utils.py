from __future__ import annotations

import logging
import sys
import inspect
import json
import re
import uuid
from datetime import datetime
from datetime import timedelta
from enum import Enum
from importlib import import_module as imodule
from importlib import util
from pathlib import Path
from types import GeneratorType
from typing import Any
from collections.abc import Callable

from flux.errors import ExecutionError


def maybe_awaitable(func: Any | None) -> Any:
    if func is None:

        async def none_wrapper():
            return None

        return none_wrapper()

    if inspect.isawaitable(func):
        return func

    async def wrapper():
        return func

    return wrapper()


def make_hashable(item):
    if isinstance(item, dict):
        return tuple(sorted((k, make_hashable(v)) for k, v in item.items()))
    elif isinstance(item, list):
        return tuple(make_hashable(i) for i in item)
    elif isinstance(item, set):
        return frozenset(make_hashable(i) for i in item)
    elif type(item).__name__ == "pandas.DataFrame":
        return tuple(map(tuple, item.itertuples(index=False)))
    elif is_hashable(item):
        return item
    else:
        return str(item)


def is_hashable(obj) -> bool:
    try:
        hash(obj)
        return True
    except TypeError:
        return False


def to_json(obj):
    return json.dumps(obj, indent=4, cls=FluxEncoder)


def import_module(name: str) -> Any:
    return imodule(name)


def import_module_from_file(path: str) -> Any:
    file_path = Path(path)

    if file_path.is_dir():
        file_path = file_path / "__init__.py"
    elif file_path.suffix != ".py":
        raise ValueError(f"Invalid module path: {file_path}")

    spec = util.spec_from_file_location("workflow_module", file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot find module at {file_path}.")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_value(value: str | None) -> Any:
    """Parse a string value into the correct Python type.

    Supports:
    - None, null, empty string -> None
    - true/false -> bool
    - integers -> int
    - floats -> float
    - valid JSON -> parsed JSON
    - everything else -> str

    Args:
        value: The value to parse

    Returns:
        The parsed value in its correct type
    """
    if value is None or value.lower() in ("none", "null") or value == "":
        return None

    if value.lower() == "true":
        return True

    if value.lower() == "false":
        return False

    if value.lower() == "nan":
        return float("nan")
    if value.lower() == "infinity" or value.lower() == "inf":
        return float("inf")
    if value.lower() == "-infinity" or value.lower() == "-inf":
        return float("-inf")

    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass

    try:
        return json.loads(value)
    except Exception as ex:  # noqa: F841
        pass

    return value


class FluxEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()

        from flux import ExecutionContext

        if isinstance(obj, ExecutionContext):
            return {
                "workflow_id": obj.workflow_id,
                "workflow_namespace": obj.workflow_namespace,
                "workflow_name": obj.workflow_name,
                "execution_id": obj.execution_id,
                "input": obj.input,
                "output": obj.output,
                "state": obj.state,
                "events": obj.events,
            }

        if isinstance(obj, ExecutionError):
            obj = obj.inner_exception if obj.inner_exception else obj
            return {"type": type(obj).__name__, "message": str(obj)}

        if isinstance(obj, Exception):
            return {"type": type(obj).__name__, "message": str(obj)}

        if inspect.isclass(type(obj)) and isinstance(obj, Callable):
            return type(obj).__name__

        if isinstance(obj, Callable):
            return obj.__name__

        if isinstance(obj, GeneratorType):
            return str(obj)

        if isinstance(obj, timedelta):
            return obj.total_seconds()

        if isinstance(obj, uuid.UUID):
            return str(obj)

        if hasattr(obj, "__dict__"):
            return obj.__dict__

        return str(obj)


def get_func_args(func: Callable, args: tuple) -> dict:
    arg_names = inspect.getfullargspec(func).args
    arg_values: list[Any] = []

    for arg in args:
        if inspect.isclass(type(arg)) and type(arg).__name__ == "workflow":
            arg_values.append(arg.name)
        elif inspect.isclass(type(arg)) and isinstance(arg, Callable):  # type: ignore[arg-type]
            arg_values.append(arg)
        elif isinstance(arg, Callable):  # type: ignore[arg-type]
            arg_values.append(arg.__name__)
        elif isinstance(arg, list):
            arg_values.append(tuple(arg))
        else:
            arg_values.append(arg)

    return dict(zip(arg_names, arg_values))


def configure_logging():
    """Configure logging for the Flux framework.

    Returns:
        logging.Logger: The configured root logger.
    """
    # Import Configuration only when needed
    from flux.config import Configuration

    settings = Configuration.get().settings

    # Configure root logger for flux
    root_logger = logging.getLogger("flux")
    root_logger.setLevel(settings.log_level)
    root_logger.handlers = []  # Clear any existing handlers

    # Create formatter
    formatter = logging.Formatter(settings.log_format, datefmt=settings.log_date_format)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    return root_logger


def get_logger(name, parent="flux"):
    """Get a logger for a specific component that inherits from the parent logger.

    Args:
        name: The name of the component or module (can be __name__)
        parent: The parent logger name (default: "flux")

    Returns:
        A configured logger instance
    """
    # If name already starts with the parent prefix, don't add it again
    if name.startswith(f"{parent}."):
        logger_name = name
    elif name == parent:
        logger_name = name
    else:
        logger_name = f"{parent}.{name}"

    # Get or create the logger
    logger = logging.getLogger(logger_name)

    # The logger will inherit level and handlers from the parent logger
    # due to the hierarchical nature of the logging system
    return logger


_DURATION_PATTERN = re.compile(r"^(\d+)([smhdw])$")
_DURATION_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_duration(s: str) -> timedelta:
    """Parse a friendly duration like '5m', '24h', '7d' into a timedelta.

    Accepts a positive integer followed by one of: s, m, h, d, w.
    Raises ValueError for anything else (no decimals, no negatives, no zero).
    """
    if not s:
        raise ValueError("Empty duration string")
    m = _DURATION_PATTERN.match(s)
    if not m:
        raise ValueError(f"Invalid duration: {s!r} — expected '<int><s|m|h|d|w>'")
    value = int(m.group(1))
    if value == 0:
        raise ValueError(f"Invalid duration: {s!r} — zero is not a valid duration")
    unit = _DURATION_UNITS[m.group(2)]
    return timedelta(**{unit: value})


_ISO8601_DURATION_PATTERN = re.compile(
    r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$",
)


def parse_iso8601_duration(s: str) -> timedelta:
    """Parse a minimal ISO-8601 duration subset (e.g. PT1H, P7D, PT30M)."""
    if not s:
        raise ValueError("Empty duration string")
    m = _ISO8601_DURATION_PATTERN.match(s)
    if not m or s in ("P", "PT"):
        raise ValueError(f"Invalid ISO-8601 duration: {s}")
    days, hours, minutes, seconds = (int(x or 0) for x in m.groups())
    if days == 0 and hours == 0 and minutes == 0 and seconds == 0:
        raise ValueError(f"Invalid ISO-8601 duration: {s}")
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
