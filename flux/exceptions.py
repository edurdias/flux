class ExecutionException(Exception):

    _inner_exception: Exception = None

    def __init__(self, inner_exception: Exception = None):
        self._inner_exception = inner_exception

    @property
    def inner_exception(self) -> Exception:
        return self._inner_exception


class RetryException(ExecutionException):

    _attempts: int
    _delay: int
    _backoff: int

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


class WorkflowNotFoundException(ExecutionException):

    _name: str

    def __init__(self, name: str):
        super().__init__(None)
        self._name = name
