import pytest
from unittest.mock import AsyncMock

from flux.worker import WorkflowExecutionRequest


class TestWorkerTokenPassthrough:
    def test_execution_request_includes_token(self):
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
            "auth_token": "some-jwt-token",
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert request.auth_token == "some-jwt-token"

    def test_execution_request_no_token(self):
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
        assert request.auth_token is None
