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


class TestFluxClientListWorkflows:
    @pytest.mark.asyncio
    async def test_list_workflows(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"name": "wf-a", "namespace": "default"},
            {"name": "wf-b", "namespace": "billing"},
        ]
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.list_workflows()
        assert len(result) == 2
        assert result[0]["name"] == "wf-a"
        client._http_client.get.assert_called_once_with("/workflows")

    @pytest.mark.asyncio
    async def test_list_workflows_returns_empty_list(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.list_workflows()
        assert result == []
        client._http_client.get.assert_called_once_with("/workflows")


class TestFluxClientListExecutions:
    @pytest.mark.asyncio
    async def test_list_executions_no_filter(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"items": [], "total": 0}
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.list_executions()
        assert result["total"] == 0
        client._http_client.get.assert_called_once_with(
            "/executions",
            params={"limit": 50, "offset": 0},
        )

    @pytest.mark.asyncio
    async def test_list_executions_with_state(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"items": [], "total": 0}
        client._http_client.get = AsyncMock(return_value=mock_response)

        await client.list_executions(state="RUNNING", limit=10, offset=5)
        client._http_client.get.assert_called_once_with(
            "/executions",
            params={"limit": 10, "offset": 5, "state": "RUNNING"},
        )

    @pytest.mark.asyncio
    async def test_list_executions_with_qualified_workflow_ref(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"items": [], "total": 0}
        client._http_client.get = AsyncMock(return_value=mock_response)

        await client.list_executions(workflow_ref="billing/invoice")
        client._http_client.get.assert_called_once_with(
            "/executions",
            params={"limit": 50, "offset": 0, "namespace": "billing", "workflow_name": "invoice"},
        )

    @pytest.mark.asyncio
    async def test_list_executions_with_bare_workflow_ref(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"items": [], "total": 0}
        client._http_client.get = AsyncMock(return_value=mock_response)

        await client.list_executions(workflow_ref="hello")
        client._http_client.get.assert_called_once_with(
            "/executions",
            params={"limit": 50, "offset": 0, "namespace": "default", "workflow_name": "hello"},
        )


class TestFluxClientGetWorkflowVersions:
    @pytest.mark.asyncio
    async def test_get_workflow_versions_qualified_ref(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"version": 1}, {"version": 2}]
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.get_workflow_versions("billing/invoice")
        assert len(result) == 2
        client._http_client.get.assert_called_once_with("/workflows/billing/invoice/versions")

    @pytest.mark.asyncio
    async def test_get_workflow_versions_bare_ref(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"version": 1}]
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.get_workflow_versions("my-workflow")
        assert result[0]["version"] == 1
        client._http_client.get.assert_called_once_with("/workflows/default/my-workflow/versions")


class TestFluxClientErrorHandling:
    @pytest.mark.asyncio
    async def test_get_workflow_404_raises(self, client):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=mock_response,
        )
        client._http_client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_workflow("billing/invoice")

    @pytest.mark.asyncio
    async def test_run_workflow_server_500_raises(self, client):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=mock_response,
        )
        client._http_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            await client.run_workflow_sync("my-workflow", {"key": "val"})
