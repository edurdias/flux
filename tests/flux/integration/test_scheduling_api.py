"""Integration tests for scheduling API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.domain.schedule import CronSchedule, IntervalSchedule, ScheduleStatus, ScheduleType
from flux.models import ScheduleModel
from flux.server import Server


@pytest.fixture
def server_app():
    """Create a server app for testing."""
    server = Server(host="localhost", port=8000)
    return server._create_api()


@pytest.fixture
def test_client(server_app):
    """Create a test client for the server app."""
    return TestClient(server_app)


@pytest.fixture
def mock_workflow():
    """Mock workflow definition."""
    workflow = MagicMock()
    workflow.id = "workflow-123"
    workflow.name = "test_workflow"
    workflow.version = "1.0"
    return workflow


@pytest.fixture
def mock_schedule_model():
    """Mock schedule model."""
    schedule = MagicMock(spec=ScheduleModel)
    schedule.id = "schedule-123"
    schedule.workflow_id = "workflow-123"
    schedule.workflow_name = "test_workflow"
    schedule.name = "daily_schedule"
    schedule.description = "Test schedule"
    schedule.schedule_type = ScheduleType.CRON
    schedule.status = ScheduleStatus.ACTIVE
    schedule.created_at = datetime.now(timezone.utc)
    schedule.updated_at = datetime.now(timezone.utc)
    schedule.last_run_at = None
    schedule.next_run_at = datetime.now(timezone.utc)
    schedule.run_count = 0
    schedule.failure_count = 0
    return schedule


class TestSchedulingAPICreate:
    """Tests for schedule creation endpoint."""

    @patch("flux.server.create_schedule_manager")
    @patch("flux.server.WorkflowCatalog.create")
    @patch("flux.server.schedule_factory")
    def test_create_schedule_success(
        self,
        mock_schedule_factory,
        mock_catalog_create,
        mock_manager_create,
        test_client,
        mock_workflow,
        mock_schedule_model,
    ):
        """Test successful schedule creation."""
        # Setup mocks
        mock_catalog = MagicMock()
        mock_catalog.get.return_value = mock_workflow
        mock_catalog_create.return_value = mock_catalog

        mock_schedule = CronSchedule("0 9 * * *", "UTC")
        mock_schedule_factory.return_value = mock_schedule

        mock_manager = MagicMock()
        mock_manager.create_schedule.return_value = mock_schedule_model
        mock_manager_create.return_value = mock_manager

        # Make request
        request_data = {
            "workflow_name": "test_workflow",
            "name": "daily_schedule",
            "schedule_config": {"type": "cron", "cron_expression": "0 9 * * *", "timezone": "UTC"},
            "description": "Test schedule",
            "input_data": {"key": "value"},
        }

        response = test_client.post("/schedules", json=request_data)

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "schedule-123"
        assert data["workflow_name"] == "test_workflow"
        assert data["name"] == "daily_schedule"
        assert data["status"] == "active"

        # Verify mocks were called
        mock_catalog.get.assert_called_once_with("test_workflow")
        mock_schedule_factory.assert_called_once()
        mock_manager.create_schedule.assert_called_once()

    @patch("flux.server.create_schedule_manager")
    @patch("flux.server.WorkflowCatalog.create")
    def test_create_schedule_workflow_not_found(
        self,
        mock_catalog_create,
        mock_manager_create,
        test_client,
    ):
        """Test schedule creation fails when workflow not found."""
        # Setup mocks
        mock_catalog = MagicMock()
        mock_catalog.get.return_value = None
        mock_catalog_create.return_value = mock_catalog

        # Make request
        request_data = {
            "workflow_name": "nonexistent_workflow",
            "name": "test_schedule",
            "schedule_config": {"type": "cron", "cron_expression": "0 9 * * *"},
        }

        response = test_client.post("/schedules", json=request_data)

        # Verify response
        assert response.status_code == 404
        assert "not found" in response.text.lower()


class TestSchedulingAPIList:
    """Tests for schedule listing endpoint."""

    @patch("flux.server.create_schedule_manager")
    def test_list_all_schedules(
        self,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test listing all schedules."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.list_schedules.return_value = [mock_schedule_model]
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.get("/schedules")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "schedule-123"
        assert data[0]["workflow_name"] == "test_workflow"

        # Verify mock was called
        mock_manager.list_schedules.assert_called_once_with(
            active_only=True,
            limit=None,
            offset=None,
        )

    @patch("flux.server.create_schedule_manager")
    @patch("flux.server.WorkflowCatalog.create")
    def test_list_schedules_by_workflow(
        self,
        mock_catalog_create,
        mock_manager_create,
        test_client,
        mock_workflow,
        mock_schedule_model,
    ):
        """Test listing schedules filtered by workflow."""
        # Setup mocks
        mock_catalog = MagicMock()
        mock_catalog.get.return_value = mock_workflow
        mock_catalog_create.return_value = mock_catalog

        mock_manager = MagicMock()
        mock_manager.list_schedules.return_value = [mock_schedule_model]
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.get("/schedules?workflow_name=test_workflow")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

        # Verify mocks were called
        mock_catalog.get.assert_called_once_with("test_workflow")
        mock_manager.list_schedules.assert_called_once_with(
            workflow_id="workflow-123",
            active_only=True,
            limit=None,
            offset=None,
        )

    @patch("flux.server.create_schedule_manager")
    def test_list_schedules_include_inactive(
        self,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test listing schedules including inactive ones."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.list_schedules.return_value = [mock_schedule_model]
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.get("/schedules?active_only=false")

        # Verify response
        assert response.status_code == 200

        # Verify mock was called with active_only=False
        mock_manager.list_schedules.assert_called_once_with(
            active_only=False,
            limit=None,
            offset=None,
        )


class TestSchedulingAPIGet:
    """Tests for get schedule endpoint."""

    @patch("flux.server.create_schedule_manager")
    def test_get_schedule_success(
        self,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test getting a specific schedule."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.get_schedule.return_value = mock_schedule_model
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.get("/schedules/schedule-123")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "schedule-123"
        assert data["workflow_name"] == "test_workflow"

        # Verify mock was called
        mock_manager.get_schedule.assert_called_once_with("schedule-123")

    @patch("flux.server.create_schedule_manager")
    def test_get_schedule_not_found(
        self,
        mock_manager_create,
        test_client,
    ):
        """Test getting a non-existent schedule."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.get_schedule.return_value = None
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.get("/schedules/nonexistent")

        # Verify response
        assert response.status_code == 404
        assert "not found" in response.text.lower()


class TestSchedulingAPIUpdate:
    """Tests for schedule update endpoint."""

    @patch("flux.server.create_schedule_manager")
    @patch("flux.server.schedule_factory")
    def test_update_schedule_config(
        self,
        mock_schedule_factory,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test updating schedule configuration."""
        # Setup mocks
        mock_schedule = IntervalSchedule(hours=2, timezone="UTC")
        mock_schedule_factory.return_value = mock_schedule

        mock_manager = MagicMock()
        mock_manager.update_schedule.return_value = mock_schedule_model
        mock_manager_create.return_value = mock_manager

        # Make request
        request_data = {
            "schedule_config": {"type": "interval", "interval_seconds": 7200, "timezone": "UTC"},
        }

        response = test_client.put("/schedules/schedule-123", json=request_data)

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "schedule-123"

        # Verify mocks were called
        mock_schedule_factory.assert_called_once()
        mock_manager.update_schedule.assert_called_once()

    @patch("flux.server.create_schedule_manager")
    def test_update_schedule_description(
        self,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test updating schedule description only."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.update_schedule.return_value = mock_schedule_model
        mock_manager_create.return_value = mock_manager

        # Make request
        request_data = {
            "description": "Updated description",
        }

        response = test_client.put("/schedules/schedule-123", json=request_data)

        # Verify response
        assert response.status_code == 200

        # Verify mock was called with description
        call_kwargs = mock_manager.update_schedule.call_args[1]
        assert call_kwargs["description"] == "Updated description"


class TestSchedulingAPIPauseResume:
    """Tests for pause and resume endpoints."""

    @patch("flux.server.create_schedule_manager")
    def test_pause_schedule(
        self,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test pausing a schedule."""
        # Setup mocks
        mock_schedule_model.status = ScheduleStatus.PAUSED
        mock_schedule_model.id = "schedule-123"

        mock_manager = MagicMock()
        mock_manager.get_schedule.return_value = mock_schedule_model
        mock_manager.pause_schedule.return_value = mock_schedule_model
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.post("/schedules/schedule-123/pause")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "paused"

        # Verify mocks were called - now resolves ID first
        mock_manager.get_schedule.assert_called_once_with("schedule-123")
        mock_manager.pause_schedule.assert_called_once_with("schedule-123")

    @patch("flux.server.create_schedule_manager")
    def test_resume_schedule(
        self,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test resuming a paused schedule."""
        # Setup mocks
        mock_schedule_model.id = "schedule-123"

        mock_manager = MagicMock()
        mock_manager.get_schedule.return_value = mock_schedule_model
        mock_manager.resume_schedule.return_value = mock_schedule_model
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.post("/schedules/schedule-123/resume")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "active"

        # Verify mocks were called - now resolves ID first
        mock_manager.get_schedule.assert_called_once_with("schedule-123")
        mock_manager.resume_schedule.assert_called_once_with("schedule-123")


class TestSchedulingAPIDelete:
    """Tests for schedule deletion endpoint."""

    @patch("flux.server.create_schedule_manager")
    def test_delete_schedule_success(
        self,
        mock_manager_create,
        test_client,
    ):
        """Test deleting a schedule."""
        # Setup mocks
        mock_schedule = MagicMock()
        mock_schedule.id = "schedule-123"

        mock_manager = MagicMock()
        mock_manager.get_schedule.return_value = mock_schedule
        mock_manager.delete_schedule.return_value = True
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.delete("/schedules/schedule-123")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

        # Verify mocks were called - now resolves ID first
        mock_manager.get_schedule.assert_called_once_with("schedule-123")
        mock_manager.delete_schedule.assert_called_once_with("schedule-123")

    @patch("flux.server.create_schedule_manager")
    def test_delete_schedule_not_found(
        self,
        mock_manager_create,
        test_client,
    ):
        """Test deleting a non-existent schedule."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.delete_schedule.return_value = False
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.delete("/schedules/nonexistent")

        # Verify response
        assert response.status_code == 404
        assert "not found" in response.text.lower()


class TestSchedulingAPIErrorHandling:
    """Tests for error handling in scheduling API."""

    @patch("flux.server.create_schedule_manager")
    @patch("flux.server.WorkflowCatalog.create")
    @patch("flux.server.schedule_factory")
    def test_create_schedule_with_invalid_config(
        self,
        mock_schedule_factory,
        mock_catalog_create,
        mock_manager_create,
        test_client,
        mock_workflow,
    ):
        """Test schedule creation with invalid schedule configuration."""
        # Setup mocks
        mock_catalog = MagicMock()
        mock_catalog.get.return_value = mock_workflow
        mock_catalog_create.return_value = mock_catalog

        # Make schedule_factory raise an error
        mock_schedule_factory.side_effect = ValueError("Invalid cron expression")

        # Make request
        request_data = {
            "workflow_name": "test_workflow",
            "name": "test_schedule",
            "schedule_config": {"type": "cron", "cron_expression": "invalid"},
        }

        response = test_client.post("/schedules", json=request_data)

        # Verify response
        assert response.status_code == 500
        assert "error" in response.text.lower()

    @patch("flux.server.create_schedule_manager")
    def test_update_nonexistent_schedule(
        self,
        mock_manager_create,
        test_client,
    ):
        """Test updating a non-existent schedule."""
        # Setup mocks to raise an error
        mock_manager = MagicMock()
        mock_manager.update_schedule.side_effect = Exception("Schedule not found")
        mock_manager_create.return_value = mock_manager

        # Make request
        request_data = {"description": "New description"}

        response = test_client.put("/schedules/nonexistent", json=request_data)

        # Verify response
        assert response.status_code == 500


class TestSchedulingAPIHistory:
    """Tests for schedule history endpoint."""

    @patch("flux.server.create_schedule_manager")
    def test_get_schedule_history_success(
        self,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test getting schedule history."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.get_schedule.return_value = mock_schedule_model
        mock_manager.get_schedule_history.return_value = (
            [
                {
                    "execution_id": "exec-1",
                    "workflow_name": "test_workflow",
                    "state": "completed",
                    "worker_name": "worker-1",
                },
                {
                    "execution_id": "exec-2",
                    "workflow_name": "test_workflow",
                    "state": "failed",
                    "worker_name": "worker-1",
                },
            ],
            2,
        )
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.get("/schedules/schedule-123/history")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["schedule_id"] == "schedule-123"
        assert data["workflow_name"] == "test_workflow"
        assert len(data["entries"]) == 2
        assert data["total"] == 2
        assert data["entries"][0]["execution_id"] == "exec-1"
        assert data["entries"][0]["state"] == "completed"

        # Verify mocks were called
        mock_manager.get_schedule.assert_called_once_with("schedule-123")
        mock_manager.get_schedule_history.assert_called_once_with(
            "schedule-123", limit=50, offset=0
        )

    @patch("flux.server.create_schedule_manager")
    def test_get_schedule_history_with_pagination(
        self,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test getting schedule history with pagination."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.get_schedule.return_value = mock_schedule_model
        mock_manager.get_schedule_history.return_value = (
            [
                {
                    "execution_id": "exec-10",
                    "workflow_name": "test_workflow",
                    "state": "completed",
                    "worker_name": "worker-1",
                },
            ],
            50,
        )
        mock_manager_create.return_value = mock_manager

        # Make request with pagination
        response = test_client.get("/schedules/schedule-123/history?limit=10&offset=10")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 10
        assert data["total"] == 50

        # Verify mocks were called with pagination
        mock_manager.get_schedule_history.assert_called_once_with(
            "schedule-123", limit=10, offset=10
        )

    @patch("flux.server.create_schedule_manager")
    def test_get_schedule_history_not_found(
        self,
        mock_manager_create,
        test_client,
    ):
        """Test getting history for non-existent schedule."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.get_schedule.return_value = None
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.get("/schedules/nonexistent/history")

        # Verify response
        assert response.status_code == 404
        assert "not found" in response.text.lower()

    @patch("flux.server.create_schedule_manager")
    def test_get_schedule_history_empty(
        self,
        mock_manager_create,
        test_client,
        mock_schedule_model,
    ):
        """Test getting schedule history when empty."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_manager.get_schedule.return_value = mock_schedule_model
        mock_manager.get_schedule_history.return_value = ([], 0)
        mock_manager_create.return_value = mock_manager

        # Make request
        response = test_client.get("/schedules/schedule-123/history")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []
        assert data["total"] == 0


# =============================================================================
# Unit Tests for ScheduleManager.get_schedule_history()
# =============================================================================


class TestScheduleManagerGetHistory:
    """Unit tests for ScheduleManager.get_schedule_history() method."""

    @patch("flux.schedule_manager.RepositoryFactory.create_repository")
    def test_get_schedule_history_returns_executions(self, mock_repo_factory):
        """Test that get_schedule_history returns execution history."""
        from flux.schedule_manager import DatabaseScheduleManager, ScheduleManagerError

        # Setup mock repository and session
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo_factory.return_value = mock_repo

        # Setup mock schedule
        mock_schedule = MagicMock()
        mock_schedule.workflow_name = "test_workflow"

        # Setup mock executions
        mock_exec1 = MagicMock()
        mock_exec1.execution_id = "exec-1"
        mock_exec1.workflow_name = "test_workflow"
        mock_exec1.state = MagicMock()
        mock_exec1.state.value = "completed"
        mock_exec1.worker_name = "worker-1"

        mock_exec2 = MagicMock()
        mock_exec2.execution_id = "exec-2"
        mock_exec2.workflow_name = "test_workflow"
        mock_exec2.state = MagicMock()
        mock_exec2.state.value = "failed"
        mock_exec2.worker_name = "worker-1"

        # Setup query chain
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 2
        mock_query.offset.return_value.limit.return_value.all.return_value = [
            mock_exec1,
            mock_exec2,
        ]
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_schedule
        )
        mock_session.query.return_value.filter.return_value = mock_query

        # Create manager and call method
        manager = DatabaseScheduleManager()
        results, total = manager.get_schedule_history("schedule-123")

        # Verify results
        assert total == 2
        assert len(results) == 2
        assert results[0]["execution_id"] == "exec-1"
        assert results[1]["execution_id"] == "exec-2"

    @patch("flux.schedule_manager.RepositoryFactory.create_repository")
    def test_get_schedule_history_schedule_not_found(self, mock_repo_factory):
        """Test that get_schedule_history raises error for non-existent schedule."""
        from flux.schedule_manager import DatabaseScheduleManager, ScheduleManagerError

        # Setup mock repository
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo_factory.return_value = mock_repo

        # Schedule not found
        mock_session.query.return_value.filter.return_value.first.return_value = None

        # Create manager and verify error
        manager = DatabaseScheduleManager()
        with pytest.raises(ScheduleManagerError) as excinfo:
            manager.get_schedule_history("nonexistent")

        assert "not found" in str(excinfo.value).lower()

    @patch("flux.schedule_manager.RepositoryFactory.create_repository")
    def test_get_schedule_history_with_pagination(self, mock_repo_factory):
        """Test that get_schedule_history respects pagination parameters."""
        from flux.schedule_manager import DatabaseScheduleManager

        # Setup mock repository
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo_factory.return_value = mock_repo

        # Setup mock schedule
        mock_schedule = MagicMock()
        mock_schedule.workflow_name = "test_workflow"

        # Setup query chain
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 100
        mock_query.offset.return_value.limit.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_schedule
        )
        mock_session.query.return_value.filter.return_value = mock_query

        # Create manager and call with pagination
        manager = DatabaseScheduleManager()
        results, total = manager.get_schedule_history(
            "schedule-123", limit=10, offset=20
        )

        # Verify pagination was applied
        mock_query.offset.assert_called_with(20)
        mock_query.offset.return_value.limit.assert_called_with(10)
        assert total == 100

    @patch("flux.schedule_manager.RepositoryFactory.create_repository")
    def test_get_schedule_history_empty(self, mock_repo_factory):
        """Test that get_schedule_history returns empty list when no executions."""
        from flux.schedule_manager import DatabaseScheduleManager

        # Setup mock repository
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo_factory.return_value = mock_repo

        # Setup mock schedule
        mock_schedule = MagicMock()
        mock_schedule.workflow_name = "test_workflow"

        # Setup empty query results
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 0
        mock_query.offset.return_value.limit.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_schedule
        )
        mock_session.query.return_value.filter.return_value = mock_query

        # Create manager and call
        manager = DatabaseScheduleManager()
        results, total = manager.get_schedule_history("schedule-123")

        # Verify empty results
        assert results == []
        assert total == 0
