"""CLI tests for the local worker control commands.

'flux worker pause|resume|cancel-all|status <name>' talk to the worker's
Unix control socket on the same host — never to the server. The tests run
a real socket server speaking the one-JSON-object-per-line protocol.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest
from click.testing import CliRunner

from flux.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def control_socket(tmp_path):
    """A fake worker control endpoint; records the command it received."""
    path = str(tmp_path / "worker-w1.sock")
    received: dict = {}
    responses = {"default": {"status": "paused", "in_flight": 0, "healthy": True}}

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            received.update(json.loads(data.decode()))
            reply = responses.get(received.get("command"), responses["default"])
            conn.sendall((json.dumps(reply) + "\n").encode())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield path, received, responses
    server.close()


class TestWorkerControlCommands:
    def test_pause_sends_pause_command(self, runner, control_socket):
        path, received, _ = control_socket

        result = runner.invoke(cli, ["worker", "pause", "w1", "--socket", path])

        assert result.exit_code == 0, result.output
        assert received["command"] == "pause"
        assert json.loads(result.output)["status"] == "paused"

    def test_resume_sends_resume_command(self, runner, control_socket):
        path, received, responses = control_socket
        responses["resume"] = {"status": "active", "in_flight": 0, "healthy": True}

        result = runner.invoke(cli, ["worker", "resume", "w1", "--socket", path])

        assert result.exit_code == 0, result.output
        assert received["command"] == "resume"

    def test_cancel_all_reports_count(self, runner, control_socket):
        path, received, responses = control_socket
        responses["cancel-all"] = {
            "status": "paused",
            "in_flight": 0,
            "healthy": True,
            "cancelled": 4,
        }

        result = runner.invoke(cli, ["worker", "cancel-all", "w1", "--socket", path])

        assert result.exit_code == 0, result.output
        assert received["command"] == "cancel-all"
        assert json.loads(result.output)["cancelled"] == 4

    def test_status_prints_payload(self, runner, control_socket):
        path, received, responses = control_socket
        responses["status"] = {"status": "active", "in_flight": 2, "healthy": True}

        result = runner.invoke(cli, ["worker", "status", "w1", "--socket", path])

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["in_flight"] == 2

    def test_missing_socket_is_a_clear_error(self, runner, tmp_path):
        result = runner.invoke(
            cli,
            ["worker", "pause", "w1", "--socket", str(tmp_path / "nope.sock")],
        )

        assert result.exit_code == 1
        assert "control socket" in result.output

    def test_error_response_exits_nonzero(self, runner, control_socket):
        path, _, responses = control_socket
        responses["pause"] = {"error": "kaboom"}

        result = runner.invoke(cli, ["worker", "pause", "w1", "--socket", path])

        assert result.exit_code == 1
        assert "kaboom" in result.output
