from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from flux.domain.execution_context import ExecutionContext
from flux.tasks.call import call


class TestCallAsyncMode:
    @pytest.mark.asyncio
    async def test_async_mode_returns_execution_id(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "execution_id": "exec_123",
            "workflow_id": "wf_abc",
            "workflow_name": "test_wf",
            "state": "RUNNING",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        ctx = ExecutionContext(workflow_id="wf1", workflow_name="test", execution_id="exec_0")
        token = ExecutionContext.set(ctx)
        try:
            with patch("httpx.Client", return_value=mock_client):
                result = await call("my_workflow", {"key": "value"}, mode="async")
        finally:
            ExecutionContext.reset(token)

        assert result == "exec_123"
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert "/run/async" in call_url

    @pytest.mark.asyncio
    async def test_sync_mode_is_default(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "execution_id": "exec_123",
            "workflow_id": "wf_abc",
            "workflow_name": "test_wf",
            "input": None,
            "state": "COMPLETED",
            "events": [],
            "requests": [],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        ctx = ExecutionContext(workflow_id="wf1", workflow_name="test", execution_id="exec_0")
        token = ExecutionContext.set(ctx)
        try:
            with patch("httpx.Client", return_value=mock_client):
                await call("my_workflow", None, mode="sync")
        finally:
            ExecutionContext.reset(token)

        call_url = mock_client.post.call_args[0][0]
        assert "/run/sync" in call_url
