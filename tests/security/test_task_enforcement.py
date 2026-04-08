import pytest

from flux.task import task
from flux.security.identity import FluxIdentity
from flux.security.errors import TaskAuthorizationError


class TestAuthExempt:
    def test_default_auth_exempt_is_false(self):
        @task
        async def my_task():
            return 1

        assert my_task.auth_exempt is False

    def test_auth_exempt_with_options(self):
        @task.with_options(auth_exempt=True)
        async def my_task():
            return 1

        assert my_task.auth_exempt is True

    def test_instance_with_options_preserves_auth_exempt(self):
        @task.with_options(auth_exempt=True)
        async def my_task():
            return 1

        new_task = my_task._with_options(name="renamed")
        assert new_task.auth_exempt is True


class TestExecutionContextIdentity:
    def test_identity_defaults_to_none(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(
            workflow_id="wf-1",
            workflow_name="test",
        )
        assert ctx.identity is None

    def test_identity_can_be_set(self):
        from flux.domain.execution_context import ExecutionContext

        identity = FluxIdentity(subject="alice@acme.com", roles=frozenset({"operator"}))
        ctx = ExecutionContext(
            workflow_id="wf-1",
            workflow_name="test",
        )
        ctx.set_identity(identity)
        assert ctx.identity.subject == "alice@acme.com"
