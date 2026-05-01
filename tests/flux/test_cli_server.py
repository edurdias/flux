"""Tests for the `flux server` CLI group (server lifecycle commands)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from flux.cli import cli
from flux.security import bootstrap_token as bt


@pytest.fixture
def runner():
    return CliRunner()


def _patched_settings(home: Path, configured: str | None = None):
    """Build a settings-like mock that the CLI sees when reading Configuration.

    We patch ``flux.config.Configuration.get`` to return an object whose
    ``settings.home`` and ``settings.workers.bootstrap_token`` match the
    test inputs.
    """
    workers = type("W", (), {"bootstrap_token": configured})()
    settings = type("S", (), {"home": str(home), "workers": workers})()
    config = type("C", (), {"settings": settings})()
    return config


class TestServerBootstrapToken:
    def test_prints_configured_value_when_set(self, runner, tmp_path: Path):
        with patch(
            "flux.config.Configuration.get",
            return_value=_patched_settings(tmp_path, configured="explicit-token"),
        ):
            result = runner.invoke(cli, ["server", "bootstrap-token"])
        assert result.exit_code == 0
        assert result.output.strip() == "explicit-token"

    def test_prints_persisted_value_when_no_config(self, runner, tmp_path: Path):
        bt.write(tmp_path, "persisted-token")
        with patch("flux.config.Configuration.get", return_value=_patched_settings(tmp_path)):
            result = runner.invoke(cli, ["server", "bootstrap-token"])
        assert result.exit_code == 0
        assert result.output.strip() == "persisted-token"

    def test_errors_when_no_token_anywhere(self, runner, tmp_path: Path):
        with patch("flux.config.Configuration.get", return_value=_patched_settings(tmp_path)):
            result = runner.invoke(cli, ["server", "bootstrap-token"])
        assert result.exit_code == 1
        assert "No bootstrap token found" in result.output

    def test_rotate_writes_new_token_and_prints_it(self, runner, tmp_path: Path):
        bt.write(tmp_path, "original")
        with patch("flux.config.Configuration.get", return_value=_patched_settings(tmp_path)):
            result = runner.invoke(cli, ["server", "bootstrap-token", "--rotate"])
        assert result.exit_code == 0
        new = result.output.strip().splitlines()[-1]
        assert new != "original"
        assert len(new) == 64
        assert bt.read_persisted(tmp_path) == new

    def test_rotate_warns_when_configured_overrides(self, runner, tmp_path: Path):
        with patch(
            "flux.config.Configuration.get",
            return_value=_patched_settings(tmp_path, configured="env-supplied"),
        ):
            result = runner.invoke(cli, ["server", "bootstrap-token", "--rotate"])
        assert result.exit_code == 0
        assert "Warning" in result.output
        # The new token must still be written even if config overrides it
        assert bt.read_persisted(tmp_path) is not None

    def test_configured_takes_precedence_over_persisted(self, runner, tmp_path: Path):
        bt.write(tmp_path, "stale-on-disk")
        with patch(
            "flux.config.Configuration.get",
            return_value=_patched_settings(tmp_path, configured="active-from-env"),
        ):
            result = runner.invoke(cli, ["server", "bootstrap-token"])
        assert result.exit_code == 0
        assert result.output.strip() == "active-from-env"

    def test_help_does_not_crash(self, runner):
        """`flux server bootstrap-token --help` must work without runtime imports failing."""
        result = runner.invoke(cli, ["server", "bootstrap-token", "--help"])
        assert result.exit_code == 0
        assert "bootstrap-token" in result.output or "Print" in result.output
        assert "--rotate" in result.output

    def test_server_group_help_lists_subcommand(self, runner):
        """`flux server --help` must list bootstrap-token as a subcommand."""
        result = runner.invoke(cli, ["server", "--help"])
        assert result.exit_code == 0
        assert "bootstrap-token" in result.output

    def test_rotate_creates_home_dir_when_missing(self, runner, tmp_path: Path):
        """`--rotate` must work even if <home> doesn't exist yet."""
        nested = tmp_path / "fresh-home"
        assert not nested.exists()
        with patch("flux.config.Configuration.get", return_value=_patched_settings(nested)):
            result = runner.invoke(cli, ["server", "bootstrap-token", "--rotate"])
        assert result.exit_code == 0
        token = result.output.strip().splitlines()[-1]
        assert (nested / bt.TOKEN_FILENAME).read_text() == token

    def test_rotate_replaces_corrupted_persisted_file(self, runner, tmp_path: Path):
        """If the persisted file is empty/whitespace, --rotate still produces a fresh value."""
        bt.write(tmp_path, "   \n  ")
        with patch("flux.config.Configuration.get", return_value=_patched_settings(tmp_path)):
            result = runner.invoke(cli, ["server", "bootstrap-token", "--rotate"])
        assert result.exit_code == 0
        token = result.output.strip().splitlines()[-1]
        assert len(token) == 64
        assert bt.read_persisted(tmp_path) == token

    def test_persisted_with_trailing_whitespace_is_normalized(self, runner, tmp_path: Path):
        """Operator-edited file with trailing newline still yields the bare token."""
        (tmp_path / bt.TOKEN_FILENAME).write_text("hand-edited-token\n")
        with patch("flux.config.Configuration.get", return_value=_patched_settings(tmp_path)):
            result = runner.invoke(cli, ["server", "bootstrap-token"])
        assert result.exit_code == 0
        assert result.output.strip() == "hand-edited-token"

    def test_rotate_warning_does_not_pollute_token_output(self, tmp_path: Path):
        """The warning goes to stderr; stdout must contain ONLY the token.

        Click 8.2+ removed the ``mix_stderr=False`` argument to ``invoke``,
        so we spawn a real subprocess to inspect stdout and stderr separately.
        """
        import os
        import subprocess
        import sys
        from tests.e2e.conftest import PROJECT_ROOT

        env = {
            **os.environ,
            "FLUX_HOME": str(tmp_path),
            "FLUX_WORKERS__BOOTSTRAP_TOKEN": "env-supplied",
        }
        result = subprocess.run(
            [sys.executable, "-m", "flux.cli", "server", "bootstrap-token", "--rotate"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
        assert len(stdout_lines) == 1
        assert len(stdout_lines[0]) == 64
        assert "Warning" in result.stderr
