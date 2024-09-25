class ExecutionException(Exception):

    _inner_exception: Exception = None

    def __init__(self, inner_exception: Exception):
        self._inner_exception = inner_exception

    @property
    def inner_exception(self) -> Exception:
        return self._inner_exception


class RetryException(ExecutionException):

    _retry_attempts: int
    _retry_delay: int

    def __init__(
        self, inner_exception: Exception, retry_attempts: int, retry_delay: int
    ):
        super().__init__(inner_exception)
        self._retry_attempts = retry_attempts
        self._retry_delay = retry_delay

    @property
    def retry_attempts(self) -> int:
        return self._retry_attempts

    @property
    def retry_delay(self) -> int:
        return self._retry_delay


class WorkflowNotFoundException(ExecutionException):

    _name: str

    def __init__(self, name: str):
        super().__init__(None)
        self._name = name