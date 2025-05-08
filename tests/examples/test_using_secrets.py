from __future__ import annotations

from examples.using_secrets import SECRET_NAME
from examples.using_secrets import SECRET_VALUE
from examples.using_secrets import using_secrets
from flux.errors import ExecutionError
from flux.secret_managers import SecretManager


def test_should_succeed():
    secret_manager = SecretManager.current()
    secret_manager.save(SECRET_NAME, SECRET_VALUE)

    ctx = using_secrets.run()
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert ctx.output == SECRET_VALUE
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = using_secrets.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail():
    # ensure there is no secret set
    secret_manager = SecretManager.current()
    secret_manager.remove(SECRET_NAME)

    ctx = using_secrets.run()
    assert ctx.finished and ctx.failed, "The workflow should have failed."
    assert isinstance(ctx.output, ExecutionError)
    assert isinstance(ctx.output.inner_exception, ValueError)
    assert ctx.output.inner_exception.args == (
        f"The following secrets were not found: ['{SECRET_NAME}']",
    )
