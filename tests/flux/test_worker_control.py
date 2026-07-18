"""Worker runtime control: pause/resume, bulk cancel, and the local API.

Pause stops claiming without process exit — heartbeats continue so the
worker reads as *paused*, not offline; effective capacity is zero (racing
dispatches are released for re-dispatch). cancel_all is the worker-initiated
bulk form of the server's per-execution cancel. Control arrives from the
worker's own host: SIGUSR1/SIGUSR2 or the Unix control socket
('flux worker pause|resume|cancel-all|status <name>').
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flux.config import Configuration
from tests.flux.test_worker_checkpoint import make_worker
from tests.flux.test_worker_health import _scheduled_event


class TestPauseResume:
    def test_status_transitions(self):
        worker = make_worker()
        assert worker.status == "active"
        worker._paused = True
        assert worker.status == "paused"
        worker._draining = True
        assert worker.status == "draining"  # draining wins over paused

    @pytest.mark.asyncio
    async def test_pause_and_resume_flip_state_and_notify_server(self):
        worker = make_worker()
        worker._send_pong = AsyncMock()

        worker.pause()
        await asyncio.sleep(0)  # let the notify task run
        assert worker.status == "paused"

        worker.resume()
        await asyncio.sleep(0)
        assert worker.status == "active"
        assert worker._send_pong.await_count == 2

    @pytest.mark.asyncio
    async def test_pause_and_resume_are_idempotent(self):
        worker = make_worker()
        worker._send_pong = AsyncMock()

        worker.pause()
        worker.pause()
        await asyncio.sleep(0)
        assert worker._send_pong.await_count == 1

        worker.resume()
        worker.resume()
        await asyncio.sleep(0)
        assert worker._send_pong.await_count == 2

    @pytest.mark.asyncio
    async def test_scheduled_dispatch_is_released_when_paused(self):
        worker = make_worker()
        worker._paused = True
        worker._release_claim = AsyncMock()
        worker._authorized_post = AsyncMock()

        await worker._handle_execution_scheduled(
            "http://localhost:19000/workers/x",
            _scheduled_event("exec-paused"),
        )

        worker._release_claim.assert_awaited_once_with("exec-paused")
        worker._authorized_post.assert_not_called()  # never claimed

    @pytest.mark.asyncio
    async def test_resumed_dispatch_is_released_when_paused(self):
        worker = make_worker()
        worker._paused = True
        worker._release_claim = AsyncMock()
        worker._authorized_post = AsyncMock()

        await worker._handle_execution_resumed(_scheduled_event("exec-resume"))

        worker._release_claim.assert_awaited_once_with("exec-resume")
        worker._authorized_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_pong_carries_status_and_in_flight(self):
        worker = make_worker()
        worker._paused = True
        worker._running_workflows["exec-1"] = MagicMock()
        captured = {}

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            response = MagicMock()
            response.status_code = 200
            return response

        worker._authorized_post = capture_post

        await worker._send_pong()

        assert captured["json"] == {
            "healthy": True,
            "status": "paused",
            "in_flight": 1,
        }


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancels_every_running_task(self):
        worker = make_worker()

        async def hang():
            await asyncio.Event().wait()

        tasks = {f"exec-{i}": asyncio.create_task(hang()) for i in range(3)}
        worker._running_workflows.update(tasks)

        cancelled = await worker.cancel_all()

        assert cancelled == 3
        assert all(t.cancelled() for t in tasks.values())

    @pytest.mark.asyncio
    async def test_no_running_work_is_a_noop(self):
        worker = make_worker()
        assert await worker.cancel_all() == 0

    @pytest.mark.asyncio
    async def test_done_tasks_are_not_counted(self):
        worker = make_worker()

        async def done():
            return None

        finished = asyncio.create_task(done())
        await finished
        worker._running_workflows["exec-done"] = finished

        assert await worker.cancel_all() == 0


@pytest.fixture
def control_config(tmp_path):
    socket_path = str(tmp_path / "worker.sock")
    mock_settings = MagicMock()
    mock_settings.workers.control_socket_enabled = True
    mock_settings.workers.control_socket_path = socket_path
    with patch.object(Configuration, "get") as mock_get:
        mock_get.return_value = MagicMock(settings=mock_settings)
        yield socket_path


async def _send_command(socket_path: str, command: str) -> dict:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    writer.write((json.dumps({"command": command}) + "\n").encode())
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return json.loads(line.decode())


class TestControlSocket:
    @pytest.mark.asyncio
    async def test_pause_resume_status_roundtrip(self, control_config):
        worker = make_worker()
        worker._send_pong = AsyncMock()
        await worker._start_control_server()
        try:
            response = await _send_command(control_config, "status")
            assert response == {"status": "active", "in_flight": 0, "healthy": True}

            response = await _send_command(control_config, "pause")
            assert response["status"] == "paused"
            assert worker._paused is True

            response = await _send_command(control_config, "resume")
            assert response["status"] == "active"
            assert worker._paused is False
        finally:
            await worker._stop_control_server()

    @pytest.mark.asyncio
    async def test_cancel_all_over_socket(self, control_config):
        worker = make_worker()
        await worker._start_control_server()

        async def hang():
            await asyncio.Event().wait()

        task = asyncio.create_task(hang())
        worker._running_workflows["exec-1"] = task
        try:
            response = await _send_command(control_config, "cancel-all")
            assert response["cancelled"] == 1
            assert task.cancelled()
        finally:
            await worker._stop_control_server()

    @pytest.mark.asyncio
    async def test_unknown_command_reports_error(self, control_config):
        worker = make_worker()
        await worker._start_control_server()
        try:
            response = await _send_command(control_config, "explode")
            assert "unknown command" in response["error"]
        finally:
            await worker._stop_control_server()

    @pytest.mark.asyncio
    async def test_stop_removes_the_socket_file(self, control_config, tmp_path):
        import os

        worker = make_worker()
        await worker._start_control_server()
        assert os.path.exists(control_config)
        # Same trust boundary as the worker process owner.
        assert (os.stat(control_config).st_mode & 0o777) == 0o600

        await worker._stop_control_server()
        assert not os.path.exists(control_config)

    @pytest.mark.asyncio
    async def test_disabled_by_config(self, control_config, tmp_path):
        import os

        Configuration.get().settings.workers.control_socket_enabled = False
        worker = make_worker()
        await worker._start_control_server()
        assert worker._control_server is None
        assert not os.path.exists(control_config)


class TestDispatcherExclusion:
    def test_connected_workers_excludes_paused(self):
        from flux.dispatcher import Dispatcher

        dispatcher = Dispatcher.__new__(Dispatcher)
        server = MagicMock()
        server._worker_info = {"w1": "info1", "w2": "info2"}
        server._worker_queues = {"w1": MagicMock(), "w2": MagicMock()}
        server._worker_unhealthy = set()
        server._worker_paused = {"w2"}
        dispatcher._server = server

        assert dispatcher._connected_workers() == ["info1"]
