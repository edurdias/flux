"""Tests for terminal UI."""

from __future__ import annotations

import pytest

from flux.agents.ui.terminal import TerminalUI


@pytest.fixture
def ui():
    return TerminalUI()


@pytest.mark.asyncio
async def test_display_response(capsys, ui):
    await ui.display_response("Hello world")
    captured = capsys.readouterr()
    assert "Hello world" in captured.out


@pytest.mark.asyncio
async def test_display_response_none(capsys, ui):
    await ui.display_response(None)
    captured = capsys.readouterr()
    assert captured.out == "" or captured.out.strip() == ""


@pytest.mark.asyncio
async def test_display_tool_start(capsys, ui):
    await ui.display_tool_start("shell", {"cmd": "ls"})
    captured = capsys.readouterr()
    assert "shell" in captured.out
    assert "ls" in captured.out


@pytest.mark.asyncio
async def test_display_tool_done_success(capsys, ui):
    await ui.display_tool_done("shell", "success")
    captured = capsys.readouterr()
    assert "✓" in captured.out
    assert "shell" in captured.out


@pytest.mark.asyncio
async def test_display_tool_done_error(capsys, ui):
    await ui.display_tool_done("shell", "error")
    captured = capsys.readouterr()
    assert "✗" in captured.out
    assert "shell" in captured.out


@pytest.mark.asyncio
async def test_display_token(capsys, ui):
    await ui.display_token("Hello")
    await ui.display_token(" world")
    captured = capsys.readouterr()
    assert "Hello world" in captured.out


@pytest.mark.asyncio
async def test_display_session_info(capsys, ui):
    await ui.display_session_info("exec_abc123", "coder")
    captured = capsys.readouterr()
    assert "exec_abc123" in captured.out
    assert "coder" in captured.out
