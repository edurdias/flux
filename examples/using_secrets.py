from __future__ import annotations

from typing import Any

from flux import task
from flux import workflow
from flux.secret_managers import SecretManager

SECRET_NAME = "example"
SECRET_VALUE = "super secret"


@task.with_options(secret_requests=[SECRET_NAME])
def task_with_secrets(secrets: dict[str, Any] = {}):  # Secrets are not stored in events
    # Do not print a secret value, this is just an example.
    print(f"Secret {SECRET_NAME} = {secrets[SECRET_NAME]}")
    return secrets[SECRET_NAME]


@workflow
def using_secrets():
    return (yield task_with_secrets())


if __name__ == "__main__":  # pragma: no cover
    # set the secret prior to use
    secret_manager = SecretManager.current()
    secret_manager.save(SECRET_NAME, SECRET_VALUE)

    ctx = using_secrets.run()
    print(ctx.to_json())
