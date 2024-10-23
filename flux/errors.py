from __future__ import annotations

from typing import Literal


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


class TimeoutError(ExecutionError):
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
        self._timeout = timeout

    @property
    def timeout(self) -> int:
        return self._timeout


class WorkflowPausedError(ExecutionError):
    def __init__(self, reference: str):
        super().__init__(
            message=f"Workflow paused. Task reference: {reference}",
        )
        self._reference = reference

    @property
    def reference(self) -> str:
        return self._reference


class WorkflowCatalogError(ExecutionError):
    def __init__(self, message: str):
        super().__init__(message=message)


class WorkflowNotFoundError(ExecutionError):
    def __init__(self, name: str):
        super().__init__(message=f"Workflow '{name}' not found.")


class ExecutionContextNotFoundError(ExecutionError):
    def __init__(self, execution_id: str):
        super().__init__(
            message=f"Execution context '{execution_id}' not found.",
        )
