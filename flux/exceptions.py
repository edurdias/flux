from typing import Literal


class ExecutionException(Exception):

    def __init__(self, inner_exception: Exception = None, message: str = None):
        super().__init__(message)
        self._inner_exception = inner_exception

    @property
    def inner_exception(self) -> Exception:
        return self._inner_exception


class RetryException(ExecutionException):

    def __init__(
        self, inner_exception: Exception, attempts: int, delay: int, backoff: int
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


class TimeoutException(ExecutionException):

    def __init__(
        self, type: Literal["Workflow", "Task"], name: str, id: str, timeout: int
    ):
        super().__init__(None, f"{type} {name} ({id}) timed out ({timeout}s).")
        self._timeout = timeout

    @property
    def timeout(self) -> int:
        return self._timeout


class WorkflowNotFoundException(ExecutionException):

    def __init__(self, name: str):
        super().__init__(None)
        self._name = name
