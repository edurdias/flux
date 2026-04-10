import pickle

import pytest

from flux.task import task


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


class TestExecutionContextIdentityRemoved:
    def test_context_has_no_identity_attribute(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        assert not hasattr(ctx, "_identity"), "ExecutionContext still has _identity field"
        assert not hasattr(ctx, "identity"), "ExecutionContext still has identity property"

    def test_context_has_no_set_identity_method(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        assert not hasattr(ctx, "set_identity"), "ExecutionContext still has set_identity method"

    def test_context_has_no_auth_token_field(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        assert not hasattr(ctx, "_auth_token"), "ExecutionContext still has _auth_token field"
        assert not hasattr(ctx, "auth_token"), "ExecutionContext still has auth_token property"

    def test_context_has_no_identity_subject_field(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        assert not hasattr(ctx, "_identity_subject")


class TestExecutionContextExecToken:
    def test_exec_token_defaults_to_none(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        assert ctx.exec_token is None

    def test_exec_token_can_be_set(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        ctx.set_exec_token("exec.tok.123")
        assert ctx.exec_token == "exec.tok.123"

    def test_exec_token_not_serialized_to_plain_json(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        ctx.set_exec_token("exec.tok.secret")
        data = ctx.to_dict()
        assert "exec_token" not in data, "exec_token must not appear in DB checkpoint"

    def test_exec_token_survives_getstate_setstate(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        ctx.set_exec_token("exec.tok.dispatch")
        restored = pickle.loads(pickle.dumps(ctx))
        assert restored.exec_token is None


class TestFluxEncoderExecToken:
    def test_flux_encoder_includes_exec_token_in_dispatch_payload(self):
        import json

        from flux.domain.execution_context import ExecutionContext
        from flux.utils import FluxEncoder

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        ctx.set_exec_token("dispatch.token.xyz")

        payload = {"workflow": "some-workflow", "context": ctx, "exec_token": ctx.exec_token}
        encoded = json.dumps(payload, cls=FluxEncoder)
        data = json.loads(encoded)
        assert data["exec_token"] == "dispatch.token.xyz"


class TestNoCtxIdentityCallSites:
    def test_no_ctx_identity_in_task_py(self):
        import pathlib

        src = pathlib.Path("flux/task.py").read_text()
        assert "ctx.identity" not in src, "flux/task.py still references ctx.identity"
        assert "set_identity" not in src, "flux/task.py still calls set_identity"

    def test_no_auth_token_in_task_py(self):
        import pathlib

        src = pathlib.Path("flux/task.py").read_text()
        assert "ctx.auth_token" not in src, "flux/task.py still references ctx.auth_token"
        assert "set_auth_token" not in src, "flux/task.py still calls set_auth_token"


class TestExecutionContextEventSubjectStamping:
    def test_schedule_event_subject_is_none_by_default(self):
        from flux.domain.execution_context import ExecutionContext
        from flux.worker_registry import WorkerInfo

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        worker = WorkerInfo(name="worker-1")
        ctx.schedule(worker)

        scheduled_events = [e for e in ctx.events if e.type.value == "WORKFLOW_SCHEDULED"]
        assert len(scheduled_events) == 1
        assert scheduled_events[0].subject is None

    def test_context_has_no_get_subject_method(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        assert not hasattr(ctx, "_get_subject"), "ExecutionContext still has _get_subject helper"


class TestNoLegacyIdentityTests:
    def test_execution_context_has_no_legacy_identity_api(self):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        legacy_attrs = ["identity", "set_identity", "auth_token", "set_auth_token", "_get_subject"]
        for attr in legacy_attrs:
            assert not hasattr(ctx, attr), f"ExecutionContext still has legacy attribute: {attr}"
