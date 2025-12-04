# ruff: noqa: E402
from __future__ import annotations

from flux.utils import get_logger
from flux.utils import configure_logging

configure_logging()

# First import the core domain classes to avoid circular imports
from flux.domain.events import ExecutionEvent, ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext

# Then import the rest of the modules
from flux.task import task, TaskMetadata
from flux.workflow import workflow

# Output storage
from flux.output_storage import (
    OutputStorageReference,
    OutputStorage,
    InlineOutputStorage,
    LocalFileStorage,
)

# Secret managers
from flux.secret_managers import SecretManager, SQLiteSecretManager

# Built-in tasks
from flux.tasks import (
    now,
    uuid4,
    choice,
    randint,
    randrange,
    parallel,
    sleep,
    call,
    pipeline,
    pause,
    Graph,
)

# Catalogs
from flux.catalogs import WorkflowInfo, WorkflowCatalog, DatabaseWorkflowCatalog

# Context managers
from flux.context_managers import ContextManager, SQLiteContextManager

# Scheduling
from flux.domain.schedule import cron, interval, once, Schedule, ScheduleType, ScheduleStatus
from flux.schedule_manager import create_schedule_manager

logger = get_logger("flux")

__all__ = [
    # Core
    "task",
    "workflow",
    "TaskMetadata",
    # Events and execution context
    "ExecutionEvent",
    "ExecutionState",
    "ExecutionEventType",
    "ExecutionContext",
    # Scheduling
    "cron",
    "interval",
    "once",
    "Schedule",
    "ScheduleType",
    "ScheduleStatus",
    "create_schedule_manager",
    # Output storage
    "OutputStorageReference",
    "OutputStorage",
    "InlineOutputStorage",
    "LocalFileStorage",
    # Secret managers
    "SecretManager",
    "SQLiteSecretManager",
    # Built-in tasks
    "now",
    "uuid4",
    "choice",
    "randint",
    "randrange",
    "parallel",
    "sleep",
    "call",
    "pipeline",
    "pause",
    "Graph",
    # Catalogs
    "WorkflowInfo",
    "WorkflowCatalog",
    "DatabaseWorkflowCatalog",
    # Context managers
    "ContextManager",
    "SQLiteContextManager",
]
