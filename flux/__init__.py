# ruff: noqa: F403
# ruff: noqa: E402
from __future__ import annotations

from flux.logging import configure_logging, get_logger

configure_logging()

# First import the core domain classes to avoid circular imports
from flux.domain.execution_context import ExecutionContext
from flux.domain.events import *

# Then import the rest of the modules
from flux.decorators import task
from flux.decorators import workflow
from flux.encoders import *
from flux.output_storage import *
from flux.secret_managers import *
from flux.tasks import *
from flux.catalogs import *
from flux.context_managers import *

logger = get_logger("flux")

__all__ = [
    "task",
    "workflow",
    "ExecutionContext",
]
