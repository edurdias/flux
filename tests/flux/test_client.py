from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from flux.client import FluxClient, DEFAULT_TIMEOUT


class TestFluxClientInit:
    def test_creates_with_server_url(self):
        client = FluxClient("http://localhost:9000")
        assert client.server_url == "http://localhost:9000"

    def test_creates_http_client(self):
        client = FluxClient("http://localhost:8000")
        assert client._http_client is not None

    def test_default_timeout(self):
        client = FluxClient("http://localhost:8000")
        assert client._http_client.timeout.read == DEFAULT_TIMEOUT

    def test_custom_timeout(self):
        client = FluxClient("http://localhost:8000", timeout=30.0)
        assert client._http_client.timeout.read == 30.0

    def test_none_timeout_disables(self):
        client = FluxClient("http://localhost:8000", timeout=None)
        assert client._http_client.timeout.read is None


@pytest.fixture
def client():
    return FluxClient("http://localhost:8000")


class TestFluxClientSyncWorkflow:
    @pytest.mark.asyncio
    async def test_run_workflow_sync(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "execution_id": "exec-1",
            "state": "COMPLETED",
            "output": "done",
        }
        mock_response.raise_for_status = MagicMock()
        client._http_client.post = AsyncMock(return_value=mock_response)

        result = await client.run_workflow_sync("my-workflow", {"key": "val"})
        assert result["execution_id"] == "exec-1"
        assert result["state"] == "COMPLETED"
        client._http_client.post.assert_called_once_with(
            "/workflows/default/my-workflow/run/sync",
            json={"key": "val"},
        )

    @pytest.mark.asyncio
    async def test_run_workflow_sync_no_input(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"execution_id": "exec-2", "state": "COMPLETED"}
        mock_response.raise_for_status = MagicMock()
        client._http_client.post = AsyncMock(return_value=mock_response)

        await client.run_workflow_sync("my-workflow")
        client._http_client.post.assert_called_once_with(
            "/workflows/default/my-workflow/run/sync",
            json=None,
        )

    @pytest.mark.asyncio
    async def test_resume_execution_sync(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "execution_id": "exec-1",
            "state": "COMPLETED",
            "output": "resumed",
        }
        mock_response.raise_for_status = MagicMock()
        client._http_client.post = AsyncMock(return_value=mock_response)

        result = await client.resume_execution_sync(
            "my-workflow",
            "exec-1",
            {"instruction": "continue"},
        )
        assert result["state"] == "COMPLETED"
        client._http_client.post.assert_called_once_with(
            "/workflows/default/my-workflow/resume/exec-1/sync",
            json={"instruction": "continue"},
        )


class TestFluxClientWorkflowRef:
    @pytest.mark.asyncio
    async def test_cancel_execution_qualified_ref(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"execution_id": "e1", "state": "CANCELLED"}
        client._http_client.get = AsyncMock(return_value=mock_response)

        await client.cancel_execution("billing/invoice", "e1")
        client._http_client.get.assert_called_once_with("/workflows/billing/invoice/cancel/e1")

    @pytest.mark.asyncio
    async def test_cancel_execution_bare_name(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"execution_id": "e1", "state": "CANCELLED"}
        client._http_client.get = AsyncMock(return_value=mock_response)

        await client.cancel_execution("hello", "e1")
        client._http_client.get.assert_called_once_with("/workflows/default/hello/cancel/e1")

    @pytest.mark.asyncio
    async def test_resume_async_qualified_ref(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"execution_id": "e1", "state": "RUNNING"}
        client._http_client.post = AsyncMock(return_value=mock_response)

        await client.resume_execution("billing/invoice", "e1", {"key": "val"})
        client._http_client.post.assert_called_once_with(
            "/workflows/billing/invoice/resume/e1/async",
            json={"key": "val"},
        )

    @pytest.mark.asyncio
    async def test_resume_async_bare_name(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"execution_id": "e1", "state": "RUNNING"}
        client._http_client.post = AsyncMock(return_value=mock_response)

        await client.resume_execution("hello", "e1")
        client._http_client.post.assert_called_once_with(
            "/workflows/default/hello/resume/e1/async",
            json=None,
        )
