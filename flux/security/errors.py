from __future__ import annotations


class AuthenticationError(Exception):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message)
        self.message = message


class AuthorizationError(Exception):
    def __init__(
        self,
        message: str = "Authorization denied",
        required_permission: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.required_permission = required_permission


class TaskAuthorizationError(AuthorizationError):
    def __init__(
        self,
        task_name: str,
        task_id: str,
        subject: str,
        required_permission: str,
    ):
        super().__init__(
            message=(
                f"Task '{task_name}' requires permission "
                f"'{required_permission}' but subject "
                f"'{subject}' does not have it."
            ),
            required_permission=required_permission,
        )
        self.task_name = task_name
        self.task_id = task_id
        self.subject = subject
