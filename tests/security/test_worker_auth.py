from unittest.mock import AsyncMock

from flux.worker import WorkflowExecutionRequest


class TestWorkerExecTokenPassthrough:
    def test_execution_request_reads_exec_token_from_payload(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-1",
                "state": "SCHEDULED",
                "events": [],
            },
            "exec_token": "exec.tok.dispatch",
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert request.exec_token == "exec.tok.dispatch"

    def test_execution_request_no_exec_token(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-1",
                "state": "SCHEDULED",
                "events": [],
            },
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert request.exec_token is None

    def test_execution_request_has_no_auth_token_field(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-1",
                "state": "SCHEDULED",
                "events": [],
            },
            "auth_token": "should-be-ignored",
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert not hasattr(
            request,
            "auth_token",
        ), "WorkflowExecutionRequest still has auth_token field"


class TestWorkerSetsExecTokenOnContext:
    def test_from_json_sets_exec_token_on_context(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-42",
                "state": "SCHEDULED",
                "events": [],
            },
            "exec_token": "exec.tok.worker-side",
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert (
            request.context.exec_token == "exec.tok.worker-side"
        ), "exec_token was not propagated to the ExecutionContext"

    def test_from_json_no_exec_token_context_exec_token_is_none(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-43",
                "state": "SCHEDULED",
                "events": [],
            },
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert request.context.exec_token is None
