from __future__ import annotations

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock

from flux.console.client import FluxClient


@pytest.fixture
def client():
    return FluxClient("http://localhost:8000")


class TestFluxClientInit:
    def test_creates_with_server_url(self):
        client = FluxClient("http://localhost:9000")
        assert client.server_url == "http://localhost:9000"

    def test_creates_http_client(self):
        client = FluxClient("http://localhost:8000")
        assert client._http_client is not None


class TestFluxClientHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "healthy", "version": "0.7.0"}
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.health_check()
        assert result["status"] == "healthy"
        client._http_client.get.assert_called_once_with("/health")

    @pytest.mark.asyncio
    async def test_health_check_connection_error(self, client):
        client._http_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        result = await client.health_check()
        assert result is None


class TestFluxClientWorkflows:
    @pytest.mark.asyncio
    async def test_list_workflows(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"name": "wf1"}, {"name": "wf2"}]
        mock_response.raise_for_status = MagicMock()
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.list_workflows()
        assert len(result) == 2
        client._http_client.get.assert_called_once_with("/workflows")

    @pytest.mark.asyncio
    async def test_get_workflow(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "wf1", "version": 3}
        mock_response.raise_for_status = MagicMock()
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.get_workflow("wf1")
        assert result["name"] == "wf1"
        client._http_client.get.assert_called_once_with("/workflows/wf1")

    @pytest.mark.asyncio
    async def test_get_workflow_versions(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"version": 1}, {"version": 2}]
        mock_response.raise_for_status = MagicMock()
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.get_workflow_versions("wf1")
        assert len(result) == 2
        client._http_client.get.assert_called_once_with("/workflows/wf1/versions")


class TestFluxClientExecutions:
    @pytest.mark.asyncio
    async def test_list_executions(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"executions": [], "total": 0, "limit": 50, "offset": 0}
        mock_response.raise_for_status = MagicMock()
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.list_executions()
        assert result["total"] == 0
        client._http_client.get.assert_called_once_with(
            "/executions",
            params={"limit": 50, "offset": 0},
        )

    @pytest.mark.asyncio
    async def test_list_executions_with_filters(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"executions": [], "total": 0, "limit": 50, "offset": 0}
        mock_response.raise_for_status = MagicMock()
        client._http_client.get = AsyncMock(return_value=mock_response)

        await client.list_executions(workflow_name="wf1", state="RUNNING", limit=10, offset=5)
        client._http_client.get.assert_called_once_with(
            "/executions",
            params={"limit": 10, "offset": 5, "workflow_name": "wf1", "state": "RUNNING"},
        )

    @pytest.mark.asyncio
    async def test_get_execution(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"execution_id": "abc", "state": "COMPLETED"}
        mock_response.raise_for_status = MagicMock()
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.get_execution("abc", detailed=True)
        assert result["execution_id"] == "abc"
        client._http_client.get.assert_called_once_with(
            "/executions/abc",
            params={"detailed": True},
        )


class TestFluxClientWorkers:
    @pytest.mark.asyncio
    async def test_list_workers(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"name": "worker-1"}]
        mock_response.raise_for_status = MagicMock()
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.list_workers()
        assert len(result) == 1
        client._http_client.get.assert_called_once_with("/workers")


class TestFluxClientSchedules:
    @pytest.mark.asyncio
    async def test_list_schedules(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": "s1", "name": "daily"}]
        mock_response.raise_for_status = MagicMock()
        client._http_client.get = AsyncMock(return_value=mock_response)

        result = await client.list_schedules()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_pause_schedule(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "paused"}
        mock_response.raise_for_status = MagicMock()
        client._http_client.post = AsyncMock(return_value=mock_response)

        await client.pause_schedule("s1")
        client._http_client.post.assert_called_once_with("/schedules/s1/pause")

    @pytest.mark.asyncio
    async def test_resume_schedule(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "active"}
        mock_response.raise_for_status = MagicMock()
        client._http_client.post = AsyncMock(return_value=mock_response)

        await client.resume_schedule("s1")
        client._http_client.post.assert_called_once_with("/schedules/s1/resume")

    @pytest.mark.asyncio
    async def test_delete_schedule(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        client._http_client.delete = AsyncMock(return_value=mock_response)

        await client.delete_schedule("s1")
        client._http_client.delete.assert_called_once_with("/schedules/s1")


class TestFluxClientActions:
    @pytest.mark.asyncio
    async def test_run_workflow(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"execution_id": "new-exec", "state": "SCHEDULED"}
        mock_response.raise_for_status = MagicMock()
        client._http_client.post = AsyncMock(return_value=mock_response)

        result = await client.run_workflow("wf1", input_data={"key": "val"})
        assert result["execution_id"] == "new-exec"
        client._http_client.post.assert_called_once_with(
            "/workflows/wf1/run/async",
            json={"key": "val"},
        )

    @pytest.mark.asyncio
    async def test_cancel_execution(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"state": "CANCELLING"}
        mock_response.raise_for_status = MagicMock()
        client._http_client.get = AsyncMock(return_value=mock_response)

        await client.cancel_execution("wf1", "exec-1")
        client._http_client.get.assert_called_once_with("/workflows/wf1/cancel/exec-1")
