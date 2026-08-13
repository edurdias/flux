"""The scheduler tick's join-token purge (issue #197 follow-up).

``purge_expired`` existed with no caller, so the worker_join_tokens table
only grew. The tick calls ``Server._purge_join_tokens`` every cycle; the
method throttles itself to one real purge an hour.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from flux.config import Configuration
from flux.server import Server


@pytest.fixture
def server(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'purge.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield Server(host="localhost", port=8000)
    DatabaseRepository._engines.clear()


class TestPurgeThrottle:
    def test_first_call_purges(self, server):
        with patch("flux.security.join_tokens.purge_expired", return_value=3) as purge:
            assert server._purge_join_tokens(now_monotonic=1000.0) == 3
        purge.assert_called_once_with(older_than_seconds=86400)

    def test_within_the_hour_is_a_no_op(self, server):
        with patch("flux.security.join_tokens.purge_expired", return_value=0) as purge:
            server._purge_join_tokens(now_monotonic=1000.0)
            assert server._purge_join_tokens(now_monotonic=1000.0 + 3599) == 0
        purge.assert_called_once()

    def test_past_the_hour_purges_again(self, server):
        with patch("flux.security.join_tokens.purge_expired", return_value=0) as purge:
            server._purge_join_tokens(now_monotonic=1000.0)
            server._purge_join_tokens(now_monotonic=1000.0 + 3601)
        assert purge.call_count == 2

    def test_a_failed_purge_retries_next_tick_not_next_hour(self, server):
        """The throttle stamp lands only after a successful purge: a DB
        failure (logged by the tick) must not silence retries for an hour."""
        with patch(
            "flux.security.join_tokens.purge_expired",
            side_effect=RuntimeError("db down"),
        ):
            with pytest.raises(RuntimeError):
                server._purge_join_tokens(now_monotonic=1000.0)

        with patch("flux.security.join_tokens.purge_expired", return_value=1) as purge:
            assert server._purge_join_tokens(now_monotonic=1001.0) == 1
        purge.assert_called_once()

    def test_retention_zero_disables(self, server):
        Configuration.get().override(workers={"join_token_retention": 0})
        try:
            with patch("flux.security.join_tokens.purge_expired") as purge:
                assert server._purge_join_tokens(now_monotonic=1000.0) == 0
            purge.assert_not_called()
        finally:
            Configuration.get().override(workers={"join_token_retention": 86400})

    def test_retention_config_is_passed_through(self, server):
        Configuration.get().override(workers={"join_token_retention": 7200})
        try:
            with patch("flux.security.join_tokens.purge_expired", return_value=0) as purge:
                server._purge_join_tokens(now_monotonic=1000.0)
            purge.assert_called_once_with(older_than_seconds=7200)
        finally:
            Configuration.get().override(workers={"join_token_retention": 86400})


class TestPurgeAgainstRealRows:
    def test_purges_a_stale_row_end_to_end(self, server):
        """No mocks: mint, backdate, and watch the server method remove it."""
        from datetime import datetime, timedelta, timezone

        from flux.models import RepositoryFactory
        from flux.security import join_tokens

        join_tokens.mint(3600)
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            session.query(join_tokens.WorkerJoinTokenModel).update(
                {
                    "expires_at": datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(days=2),
                },
                synchronize_session=False,
            )
            session.commit()

        assert server._purge_join_tokens(now_monotonic=1000.0) == 1
        assert join_tokens.outstanding() == []
