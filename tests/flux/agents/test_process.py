"""Tests for AgentProcess."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from unittest.mock import patch as mock_patch

import pytest

from flux.agents.events import AgentEvent
from flux.agents.process import AgentProcess, wants_plain_terminal
from flux.agents.ui.terminal import TerminalUI


def test_process_init():
    process = AgentProcess(
        agent_name="coder",
        server_url="http://localhost:8000",
        mode="terminal",
    )
    assert process.agent_name == "coder"
    assert process.mode == "terminal"


def test_process_invalid_mode():
    with pytest.raises(ValueError, match="mode"):
        AgentProcess(
            agent_name="coder",
            server_url="http://localhost:8000",
            mode="invalid",
        )


def test_process_with_session():
    process = AgentProcess(
        agent_name="coder",
        server_url="http://localhost:8000",
        mode="terminal",
        session_id="exec_123",
    )
    assert process.session_id == "exec_123"


@pytest.mark.asyncio
async def test_terminal_mode_dispatches_events_to_ui():
    proc = AgentProcess(agent_name="coder", server_url="http://x", mode="terminal")
    proc.ui = MagicMock()
    proc.ui.display_session_info = AsyncMock()
    proc.ui.display_response = AsyncMock()
    proc.ui.display_token = AsyncMock()
    proc.ui.display_tool_start = AsyncMock()
    proc.ui.display_tool_done = AsyncMock()
    proc.ui.display_elicitation = AsyncMock(return_value={})
    proc.ui.begin_reply = AsyncMock()
    proc.ui.end_reply = AsyncMock()
    proc.ui.prompt_user = AsyncMock(side_effect=["/quit"])

    async def fake_session_start():
        yield AgentEvent(kind="session_id", data={"id": "exec-1"})
        yield AgentEvent(kind="token", data={"text": "hello"})
        yield AgentEvent(
            kind="chat_response",
            data={"content": None, "turn": 0},
        )

    fake_session = MagicMock()
    fake_session.start = fake_session_start
    fake_session.session_id = "exec-1"

    proc.client.ensure_workflow_registered = AsyncMock()

    with patch("flux.agents.process.AgentSession", return_value=fake_session):
        await proc.run()

    proc.ui.display_token.assert_any_call("hello")
    proc.ui.display_response.assert_any_call(None)


@pytest.mark.asyncio
async def test_terminal_mode_dispatches_session_end():
    proc = AgentProcess(agent_name="coder", server_url="http://x", mode="terminal")
    proc.ui = MagicMock()
    proc.ui.display_session_info = AsyncMock()
    proc.ui.display_session_end = AsyncMock()
    proc.ui.begin_reply = AsyncMock()
    proc.ui.end_reply = AsyncMock()
    proc.ui.prompt_user = AsyncMock(side_effect=["/quit"])

    async def fake_session_start():
        yield AgentEvent(kind="session_id", data={"id": "exec-1"})
        yield AgentEvent(
            kind="session_end",
            data={"reason": "max_turns", "turns": 5},
        )

    fake_session = MagicMock()
    fake_session.start = fake_session_start
    fake_session.session_id = "exec-1"

    proc.client.ensure_workflow_registered = AsyncMock()

    with patch("flux.agents.process.AgentSession", return_value=fake_session):
        await proc.run()

    proc.ui.display_session_end.assert_called_once()
    call_arg = proc.ui.display_session_end.call_args.args[0]
    assert call_arg.reason == "max_turns"
    assert call_arg.turns == 5


def test_process_uses_console_by_default_on_tty():
    """Terminal mode with a real terminal opens the console, not the old
    single-session TextualUI/AgentApp chat (Task 9: NAME + terminal mode
    without --plain opens the console focused on that agent)."""
    with mock_patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        proc = AgentProcess(
            agent_name="coder",
            server_url="http://localhost:8000",
            mode="terminal",
        )
        assert proc.ui is None


def test_process_agent_name_optional_for_console():
    """NAME-less is only valid for the console -- there is no agent to
    resolve a plain single-session REPL against."""
    with mock_patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        proc = AgentProcess(
            agent_name=None,
            server_url="http://localhost:8000",
            mode="terminal",
        )
        assert proc.agent_name is None
        assert proc.ui is None


def test_process_agent_name_required_for_plain_terminal():
    with mock_patch.dict(os.environ, {"FLUX_PLAIN_TERMINAL": "1"}):
        with pytest.raises(ValueError, match="agent_name"):
            AgentProcess(
                agent_name=None,
                server_url="http://localhost:8000",
                mode="terminal",
            )


def test_process_agent_name_optional_for_web_mode():
    process = AgentProcess(
        agent_name=None,
        server_url="http://localhost:8000",
        mode="web",
    )
    assert process.agent_name is None


def test_process_agent_name_optional_for_api_mode():
    process = AgentProcess(
        agent_name=None,
        server_url="http://localhost:8000",
        mode="api",
    )
    assert process.agent_name is None


def test_process_creates_plain_terminal_when_env_set():
    with mock_patch.dict(os.environ, {"FLUX_PLAIN_TERMINAL": "1"}):
        proc = AgentProcess(
            agent_name="coder",
            server_url="http://localhost:8000",
            mode="terminal",
        )
        assert isinstance(proc.ui, TerminalUI)


def test_process_creates_plain_terminal_when_not_tty():
    with mock_patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = False
        env = os.environ.copy()
        env.pop("FLUX_PLAIN_TERMINAL", None)
        with mock_patch.dict(os.environ, env, clear=True):
            proc = AgentProcess(
                agent_name="coder",
                server_url="http://localhost:8000",
                mode="terminal",
            )
            assert isinstance(proc.ui, TerminalUI)


def test_wants_plain_terminal_is_gated_on_stdout_not_stdin():
    """`wants_plain_terminal` is the single source of truth `_make_terminal_ui`
    and the CLI's pre-flight guard (`flux.cli._console_can_render`) both
    call -- an interactive stdin must not mask a non-interactive stdout
    (e.g. `flux agent start | tee log`), since a Textual app renders to
    stdout. Fix for a review finding: the CLI used to check `sys.stdin`
    independently, which could disagree with this stdout-based check and
    let a startup failure slip through the CLI's guard."""
    env = os.environ.copy()
    env.pop("FLUX_PLAIN_TERMINAL", None)
    with mock_patch.dict(os.environ, env, clear=True):
        with mock_patch("sys.stdin") as mock_stdin, mock_patch("sys.stdout") as mock_stdout:
            mock_stdin.isatty.return_value = True
            mock_stdout.isatty.return_value = False
            assert wants_plain_terminal() is True

            mock_stdout.isatty.return_value = True
            mock_stdin.isatty.return_value = False
            assert wants_plain_terminal() is False


@pytest.mark.asyncio
async def test_plain_terminal_dispatches_quit():
    proc = AgentProcess(agent_name="coder", server_url="http://x", mode="terminal")
    proc.ui = MagicMock(spec=TerminalUI)
    proc.ui.display_session_info = AsyncMock()
    proc.ui.display_response = AsyncMock()
    proc.ui.display_token = AsyncMock()
    proc.ui.begin_reply = AsyncMock()
    proc.ui.end_reply = AsyncMock()
    proc.ui.prompt_user = AsyncMock(side_effect=["/quit"])

    async def fake_session_start():
        yield AgentEvent(kind="session_id", data={"id": "exec-1"})
        yield AgentEvent(kind="chat_response", data={"content": None, "turn": 0})

    fake_session = MagicMock()
    fake_session.start = fake_session_start
    fake_session.session_id = "exec-1"

    proc.client.ensure_workflow_registered = AsyncMock()

    with patch("flux.agents.process.AgentSession", return_value=fake_session):
        await proc.run()

    proc.ui.display_response.assert_any_call(None)


# =============================================================================
# Console wiring (Task 9): terminal mode without --plain constructs
# flux.agents.ui.textual_app.ConsoleApp instead of running the plain REPL.
# =============================================================================


def _tty_process(**kwargs) -> AgentProcess:
    with mock_patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        return AgentProcess(server_url="http://x", mode="terminal", **kwargs)


@pytest.mark.asyncio
async def test_console_mode_opens_with_no_initial_agent_when_name_omitted():
    proc = _tty_process(agent_name=None)
    proc.client.ensure_workflow_registered = AsyncMock()

    with patch("flux.agents.ui.textual_app.ConsoleApp") as console_app_cls:
        console_app_cls.return_value.run_async = AsyncMock()
        await proc.run()

    kwargs = console_app_cls.call_args.kwargs
    assert kwargs["initial_agent"] is None
    assert kwargs["initial_session"] is None


@pytest.mark.asyncio
async def test_console_mode_filters_rail_to_named_agent():
    proc = _tty_process(agent_name="coder")
    proc.client.ensure_workflow_registered = AsyncMock()

    with patch("flux.agents.ui.textual_app.ConsoleApp") as console_app_cls:
        console_app_cls.return_value.run_async = AsyncMock()
        await proc.run()

    kwargs = console_app_cls.call_args.kwargs
    assert kwargs["initial_agent"] == "coder"
    assert kwargs["initial_session"] is None


@pytest.mark.asyncio
async def test_console_mode_opens_initial_session_directly():
    proc = _tty_process(agent_name=None, session_id="exec-42")
    proc.client.ensure_workflow_registered = AsyncMock()

    with patch("flux.agents.ui.textual_app.ConsoleApp") as console_app_cls:
        console_app_cls.return_value.run_async = AsyncMock()
        await proc.run()

    kwargs = console_app_cls.call_args.kwargs
    assert kwargs["initial_session"] == "exec-42"


@pytest.mark.asyncio
async def test_console_mode_closes_service_even_on_error():
    proc = _tty_process(agent_name=None)
    proc.client.ensure_workflow_registered = AsyncMock()

    with (
        patch("flux.agents.ui.textual_app.ConsoleApp") as console_app_cls,
        patch("flux.agents.console.service.ConsoleService.aclose") as aclose,
    ):
        console_app_cls.return_value.run_async = AsyncMock(side_effect=RuntimeError("boom"))
        aclose.return_value = None
        with pytest.raises(RuntimeError, match="boom"):
            await proc.run()

    aclose.assert_called_once()
