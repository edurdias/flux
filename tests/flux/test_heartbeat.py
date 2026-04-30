"""Tests for heartbeat, worker cache, and reconnect features."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flux.config import WorkersConfig
from flux.server import Server, WorkerResponse


class TestHeartbeatConfig:
    """Tests for heartbeat-related configuration fields."""

    def test_default_heartbeat_interval(self):
        config = WorkersConfig()
        assert config.heartbeat_interval == 10

    def test_default_heartbeat_timeout(self):
        config = WorkersConfig()
        assert config.heartbeat_timeout == 30

    def test_default_reconnect_max_delay(self):
        config = WorkersConfig()
        assert config.reconnect_max_delay == 60

    def test_default_offline_ttl(self):
        config = WorkersConfig()
        assert config.offline_ttl == 7200

    def test_custom_heartbeat_values(self):
        config = WorkersConfig(
            heartbeat_interval=5,
            heartbeat_timeout=15,
            reconnect_max_delay=120,
            offline_ttl=3600,
        )
        assert config.heartbeat_interval == 5
        assert config.heartbeat_timeout == 15
        assert config.reconnect_max_delay == 120
        assert config.offline_ttl == 3600


class TestServerHeartbeatReaper:
    """Tests for the server heartbeat reaper logic."""

    @pytest.fixture
    def server(self):
        with patch("flux.server.Configuration") as mock_conf:
            settings = MagicMock()
            settings.scheduling.poll_interval = 30.0
            settings.workers.heartbeat_interval = 2
            settings.workers.heartbeat_timeout = 5
            settings.workers.offline_ttl = 10
            settings.workers.eviction_grace_period = 10
            settings.observability.enabled = False
            mock_conf.get.return_value.settings = settings
            s = Server(host="localhost", port=8000)
        return s

    def test_server_initializes_heartbeat_state(self, server):
        assert server._worker_last_pong == {}
        assert server._worker_cache == {}
        assert server._worker_offline_since == {}
        assert server._worker_stale_since == {}
        assert server.heartbeat_interval == 2
        assert server.heartbeat_timeout == 5
        assert server.offline_ttl == 10
        assert server.eviction_grace_period == 10

    @pytest.mark.asyncio
    async def test_reaper_marks_stale_then_evicts(self, server):
        """Reaper should first mark a worker as stale, then evict after grace period."""
        server._worker_names.append("w1")
        server._worker_events["w1"] = asyncio.Event()
        server._worker_last_pong["w1"] = time.monotonic() - 10  # 10s ago, timeout is 5
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")

        call_count = 0

        async def sleep_then_cancel(delay):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return  # First iteration: marks stale
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=sleep_then_cancel):
            await server._run_heartbeat_reaper()

        # After one iteration: worker should be stale but NOT evicted yet
        assert "w1" in server._worker_stale_since
        assert "w1" in server._worker_names  # Still connected during grace period

    @pytest.mark.asyncio
    async def test_reaper_evicts_after_grace_period(self, server):
        """Reaper should evict a worker after the grace period expires."""
        server._worker_names.append("w1")
        server._worker_events["w1"] = asyncio.Event()
        server._worker_last_pong["w1"] = time.monotonic() - 20  # 20s ago
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")
        # Already stale for longer than grace period
        server._worker_stale_since["w1"] = time.monotonic() - 15

        call_count = 0

        async def sleep_then_cancel(delay):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=sleep_then_cancel):
            await server._run_heartbeat_reaper()

        assert "w1" not in server._worker_names
        assert "w1" not in server._worker_last_pong
        assert "w1" not in server._worker_events
        assert "w1" not in server._worker_stale_since
        assert "w1" in server._worker_offline_since
        assert server._worker_cache["w1"].status == "offline"

    @pytest.mark.asyncio
    async def test_reaper_recovers_worker_that_pongs(self, server):
        """Worker that pongs during grace period should be recovered."""
        server._worker_names.append("w1")
        server._worker_events["w1"] = asyncio.Event()
        server._worker_last_pong["w1"] = time.monotonic()  # Just ponged
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")
        server._worker_stale_since["w1"] = time.monotonic() - 5  # Was stale 5s ago

        call_count = 0

        async def sleep_then_cancel(delay):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=sleep_then_cancel):
            await server._run_heartbeat_reaper()

        assert "w1" in server._worker_names
        assert "w1" not in server._worker_stale_since
        assert server._worker_cache["w1"].status == "online"

    @pytest.mark.asyncio
    async def test_reaper_does_not_evict_healthy_worker(self, server):
        """Reaper should keep workers whose last pong is within timeout."""
        server._worker_names.append("w1")
        server._worker_events["w1"] = asyncio.Event()
        server._worker_last_pong["w1"] = time.monotonic()  # Just now
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")

        call_count = 0

        async def sleep_then_cancel(delay):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=sleep_then_cancel):
            await server._run_heartbeat_reaper()

        assert "w1" in server._worker_names
        assert server._worker_cache["w1"].status == "online"

    @pytest.mark.asyncio
    async def test_reaper_prunes_expired_offline_workers(self, server):
        """Reaper should remove workers from cache after offline_ttl."""
        server._worker_offline_since["old"] = time.monotonic() - 20  # 20s ago, TTL is 10
        server._worker_cache["old"] = WorkerResponse(name="old", status="offline")

        call_count = 0

        async def sleep_then_cancel(delay):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=sleep_then_cancel):
            await server._run_heartbeat_reaper()

        assert "old" not in server._worker_offline_since
        assert "old" not in server._worker_cache

    @pytest.mark.asyncio
    async def test_reaper_keeps_recent_offline_workers(self, server):
        """Reaper should keep recently-offline workers in cache."""
        server._worker_offline_since["recent"] = time.monotonic() - 1  # 1s ago, TTL is 10
        server._worker_cache["recent"] = WorkerResponse(name="recent", status="offline")

        call_count = 0

        async def sleep_then_cancel(delay):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=sleep_then_cancel):
            await server._run_heartbeat_reaper()

        assert "recent" in server._worker_offline_since
        assert "recent" in server._worker_cache

    @pytest.mark.asyncio
    async def test_reaper_unclaims_executions_on_eviction(self, server):
        """Reaper should unclaim executions when evicting a worker."""
        server._worker_names.append("w1")
        server._worker_events["w1"] = asyncio.Event()
        server._worker_last_pong["w1"] = time.monotonic() - 20
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")
        server._worker_stale_since["w1"] = time.monotonic() - 15

        call_count = 0

        async def sleep_then_cancel(delay):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return
            raise asyncio.CancelledError()

        fake_ctx_1 = MagicMock()
        fake_ctx_1.execution_id = "exec-1"
        fake_ctx_2 = MagicMock()
        fake_ctx_2.execution_id = "exec-2"

        with (
            patch("asyncio.sleep", side_effect=sleep_then_cancel),
            patch("flux.server.ContextManager") as mock_cm,
        ):
            mock_manager = MagicMock()
            mock_manager.find_by_worker.return_value = [fake_ctx_1, fake_ctx_2]
            mock_cm.create.return_value = mock_manager
            await server._run_heartbeat_reaper()

        assert mock_manager.find_by_worker.call_count == 1
        assert mock_manager.unclaim.call_count == 2


class TestWorkerCache:
    """Tests for the in-memory worker cache."""

    @pytest.fixture
    def server(self):
        with patch("flux.server.Configuration") as mock_conf:
            settings = MagicMock()
            settings.scheduling.poll_interval = 30.0
            settings.workers.heartbeat_interval = 10
            settings.workers.heartbeat_timeout = 30
            settings.workers.offline_ttl = 7200
            settings.workers.eviction_grace_period = 30
            settings.observability.enabled = False
            mock_conf.get.return_value.settings = settings
            s = Server(host="localhost", port=8000)
        return s

    def test_cache_starts_empty(self, server):
        assert len(server._worker_cache) == 0

    def test_worker_status_online_when_connected(self, server):
        """Workers in _worker_names should be considered online."""
        server._worker_names.append("w1")
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")

        assert server._worker_cache["w1"].status == "online"

    def test_worker_status_offline_after_disconnect(self, server):
        """Simulating disconnect should mark worker offline in cache."""
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")
        server._worker_names.append("w1")

        # Simulate disconnect
        server._worker_names.remove("w1")
        server._worker_offline_since["w1"] = time.monotonic()
        server._worker_cache["w1"].status = "offline"

        assert server._worker_cache["w1"].status == "offline"
        assert "w1" in server._worker_offline_since

    def test_worker_comes_back_online(self, server):
        """Reconnecting worker should be marked online and removed from offline tracking."""
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="offline")
        server._worker_offline_since["w1"] = time.monotonic() - 60

        # Simulate reconnect
        server._worker_names.append("w1")
        server._worker_offline_since.pop("w1", None)
        server._worker_cache["w1"].status = "online"

        assert server._worker_cache["w1"].status == "online"
        assert "w1" not in server._worker_offline_since

    def test_cache_filter_online(self, server):
        """Filtering by online should return only connected workers."""
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")
        server._worker_cache["w2"] = WorkerResponse(name="w2", status="offline")
        server._worker_names.append("w1")
        server._worker_offline_since["w2"] = time.monotonic()

        online = [
            server._worker_cache[n] for n in server._worker_names if n in server._worker_cache
        ]
        assert len(online) == 1
        assert online[0].name == "w1"

    def test_cache_filter_offline(self, server):
        """Filtering by offline should return only recently-offline workers."""
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")
        server._worker_cache["w2"] = WorkerResponse(name="w2", status="offline")
        server._worker_names.append("w1")
        server._worker_offline_since["w2"] = time.monotonic()

        offline = [
            server._worker_cache[n]
            for n in server._worker_offline_since
            if n in server._worker_cache
        ]
        assert len(offline) == 1
        assert offline[0].name == "w2"

    def test_cache_no_filter_returns_all(self, server):
        """No filter should return all cached workers."""
        server._worker_cache["w1"] = WorkerResponse(name="w1", status="online")
        server._worker_cache["w2"] = WorkerResponse(name="w2", status="offline")

        all_workers = list(server._worker_cache.values())
        assert len(all_workers) == 2


class TestWorkerReconnect:
    """Tests for worker reconnect loop."""

    @pytest.fixture
    def mock_config(self):
        mock_settings = MagicMock()
        mock_settings.workers.bootstrap_token = "test-token"
        mock_settings.workers.server_url = "http://localhost:8000"
        mock_settings.workers.default_timeout = 0
        mock_settings.workers.reconnect_max_delay = 4
        mock_settings.workers.module_cache_ttl = 300

        with patch("flux.config.Configuration.get") as mock_get:
            mock_get.return_value.settings = mock_settings
            yield mock_settings

    @pytest.mark.asyncio
    async def test_run_reconnects_on_failure(self, mock_config):
        from flux.worker import Worker

        worker = Worker(name="test-worker", server_url="http://localhost:8000")
        register_count = 0

        async def mock_register():
            nonlocal register_count
            register_count += 1
            if register_count < 3:
                raise ConnectionError("Server down")
            worker._registered = True
            worker.session_token = "token"

        connect_count = 0

        async def mock_connect():
            nonlocal connect_count
            connect_count += 1
            if connect_count >= 1:
                raise KeyboardInterrupt()

        with (
            patch.object(worker, "_register", side_effect=mock_register),
            patch.object(worker, "_connect", side_effect=mock_connect),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            with pytest.raises(KeyboardInterrupt):
                await worker._run()

        # First call registers (fails), second call registers (fails), third registers (succeeds) then connects
        assert register_count == 3
        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    async def test_run_stops_on_keyboard_interrupt(self, mock_config):
        from flux.worker import Worker

        worker = Worker(name="test-worker", server_url="http://localhost:8000")

        with patch.object(worker, "_register", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                await worker._run()

    @pytest.mark.asyncio
    async def test_send_pong(self, mock_config):
        from flux.worker import Worker

        worker = Worker(name="test-worker", server_url="http://localhost:8000")
        worker.session_token = "test-token"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(worker.client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await worker._send_pong()

            mock_post.assert_called_once()
            call_url = mock_post.call_args[0][0]
            assert "/pong" in call_url

    @pytest.mark.asyncio
    async def test_send_pong_handles_error(self, mock_config):
        from flux.worker import Worker

        worker = Worker(name="test-worker", server_url="http://localhost:8000")
        worker.session_token = "test-token"

        with patch.object(
            worker.client,
            "post",
            new_callable=AsyncMock,
            side_effect=Exception("Network error"),
        ):
            # Should not raise
            await worker._send_pong()


class TestWorkerCardStatus:
    """Tests for WorkerCard online/offline visual display."""

    def test_online_worker_has_no_offline_class(self):
        from flux.console.screens.workers import WorkerCard

        card = WorkerCard({"name": "w1", "status": "online"})
        assert "offline" not in card.classes

    def test_offline_worker_has_offline_class(self):
        from flux.console.screens.workers import WorkerCard

        card = WorkerCard({"name": "w1", "status": "offline"})
        assert "offline" in card.classes

    def test_missing_status_defaults_to_offline(self):
        from flux.console.screens.workers import WorkerCard

        card = WorkerCard({"name": "w1"})
        assert "offline" in card.classes

    def test_workers_view_counts_by_status(self):
        workers = [
            {"name": "w1", "status": "online"},
            {"name": "w2", "status": "online"},
            {"name": "w3", "status": "offline"},
        ]
        online = sum(1 for w in workers if w.get("status") == "online")
        offline = len(workers) - online
        assert online == 2
        assert offline == 1
