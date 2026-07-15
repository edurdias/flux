from __future__ import annotations

from typing import Any
from typing import Literal
from typing import TypeVar

T = TypeVar("T", bound=Any)


class ExecutionError(Exception):
    def __init__(
        self,
        inner_exception: Exception | None = None,
        message: str | None = None,
    ):
        super().__init__(message)
        self._message = message
        self._inner_exception = inner_exception

    @property
    def inner_exception(self) -> Exception | None:
        return self._inner_exception

    @property
    def message(self) -> str | None:
        return self._message


class RetryError(ExecutionError):
    def __init__(
        self,
        inner_exception: Exception,
        attempts: int,
        delay: int,
        backoff: int,
    ):
        super().__init__(inner_exception)
        self._attempts = attempts
        self._delay = delay
        self._backoff = backoff

    @property
    def retry_attempts(self) -> int:
        return self._attempts

    @property
    def retry_delay(self) -> int:
        return self._delay


class ExecutionTimeoutError(ExecutionError):
    def __init__(
        self,
        type: Literal["Workflow", "Task"],
        name: str,
        id: str,
        timeout: int,
    ):
        super().__init__(
            message=f"{type} {name} ({id}) timed out ({timeout}s).",
        )
        self._type = type
        self._name = name
        self._id = id
        self._timeout = timeout

    @property
    def timeout(self) -> int:
        return self._timeout

    def __reduce__(self):
        return (self.__class__, (self._type, self._name, self._id, self._timeout))


class PauseRequested(ExecutionError):
    def __init__(self, name: str, output: Any = None):
        super().__init__(
            message="Pause Requested.",
        )
        self._name = name
        self._output = output

    @property
    def name(self) -> str:
        return self._name

    @property
    def output(self) -> Any:
        return self._output


class WorkflowCatalogError(ExecutionError):
    def __init__(self, message: str):
        super().__init__(message=message)


class TaskNotFoundError(ExecutionError):
    def __init__(self):
        super().__init__(
            message="Task not found.",
        )


class WorkflowNotFoundError(ExecutionError):
    def __init__(self, name: str, module_name: str | None = None):
        super().__init__(
            message=f"Workflow '{name}' not found {f'in module {module_name}.' if module_name else ''}",
        )


class WorkflowAlreadyExistError(ExecutionError):
    def __init__(self, name: str):
        super().__init__(message=f"Workflow '{name}' already exists.")


class ExecutionContextNotFoundError(ExecutionError):
    def __init__(self, execution_id: str | None):
        super().__init__(
            message=f"Execution context '{execution_id}' not found.",
        )


class WorkerNotFoundError(ExecutionError):
    def __init__(self, name: str):
        super().__init__(message=f"Worker '{name}' not found.")


class DatabaseConnectionError(Exception):
    """Database connection related errors"""

    def __init__(
        self,
        message: str,
        database_type: str,
        original_error: Exception | None = None,
    ):
        self.database_type = database_type
        self.original_error = original_error
        super().__init__(message)


class PostgreSQLConnectionError(DatabaseConnectionError):
    """PostgreSQL-specific connection errors"""

    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message, "postgresql", original_error)


class StaleClaimError(Exception):
    """A checkpoint arrived from a worker whose claim was superseded.

    Raised when the checkpoint's claim generation does not match the
    execution row's current generation — the execution was unclaimed (e.g.
    by the eviction reaper after a network partition) and reassigned. The
    fenced worker must abort its local copy; the new claim owns the row.
    """

    def __init__(self, execution_id: str, expected: int = -1, actual: int = -1):
        # Defaults cover the worker side, which learns only *that* it was
        # fenced (409 from the server), not the row's current generation.
        self.execution_id = execution_id
        super().__init__(
            f"Stale claim for execution {execution_id}: checkpoint carries "
            f"generation {expected} but the row is at {actual}; the execution "
            f"was reassigned",
        )


class WorkerProcessCrashed(Exception):
    """A runner's child process died without reporting a result.

    Covers segfaults, OOM kills, and hard exits (``os._exit``) of the
    subprocess executing a workflow. The worker maps this to the framework's
    durability semantics: durable executions are released back to the server
    for re-dispatch (deterministic replay resumes from the last persisted
    events), transient executions fail terminally (at-most-once).
    """

    def __init__(
        self,
        execution_id: str,
        exit_code: int | None = None,
        last_context=None,
    ):
        self.execution_id = execution_id
        self.exit_code = exit_code
        # Latest context rebuilt from the child's checkpoint frames, if any —
        # lets the crash handler fail/release from the freshest known state.
        self.last_context = last_context
        signal_hint = ""
        if exit_code is not None and exit_code < 0:
            signal_hint = f" (signal {-exit_code})"
        super().__init__(
            f"Worker subprocess for execution {execution_id} exited with "
            f"code {exit_code}{signal_hint} before reporting a result",
        )


class ExecutionTimedOut(Exception):
    """An execution exceeded its runner's wall-clock ceiling and was killed.

    Deliberately NOT a WorkerProcessCrashed: the crash path releases durable
    executions for re-dispatch, but a timeout is a policy violation the next
    attempt would deterministically repeat — the worker maps this to a
    terminal FAILED for both durabilities to avoid an infinite re-dispatch
    loop.
    """

    def __init__(
        self,
        execution_id: str,
        timeout_seconds: float,
        last_context=None,
    ):
        self.execution_id = execution_id
        self.timeout_seconds = timeout_seconds
        self.last_context = last_context
        super().__init__(
            f"Execution {execution_id} exceeded the runner's execution "
            f"timeout ({timeout_seconds:g}s) and was killed",
        )


class RunnerNotAvailableError(ExecutionError):
    """An execution requested a runner this worker does not have enabled."""

    def __init__(self, runner: str, available: list[str]):
        self.runner = runner
        self.available = available
        super().__init__(
            message=(
                f"Runner '{runner}' is not enabled on this worker "
                f"(enabled: {', '.join(available) or 'none'}). Enable it via "
                f"[flux.workers] runners or register the workflow without "
                f"runner='{runner}'."
            ),
        )


class TransientDurabilityError(ExecutionError):
    """A transient execution reached a feature that requires durability.

    Pause, approvals, and cross-worker resume all need persisted state that a
    transient execution deliberately does not have. Fail loudly instead of
    silently losing an approval or stranding a pause.
    """

    def __init__(self, execution_id: str, feature: str):
        self.execution_id = execution_id
        self.feature = feature
        super().__init__(
            message=(
                f"Transient execution {execution_id} attempted to use '{feature}', "
                f"which requires durability. Register the workflow without "
                f"durability='transient' to use it."
            ),
        )
