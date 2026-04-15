"""Tests for terminal UI."""

from __future__ import annotations

import asyncio

from flux.agents.ui.terminal import TerminalUI


def test_display_response(capsys):
    ui = TerminalUI()
    asyncio.get_event_loop().run_until_complete(ui.display_response("Hello world"))
    captured = capsys.readouterr()
    assert "Hello world" in captured.out


def test_display_response_none(capsys):
    ui = TerminalUI()
    asyncio.get_event_loop().run_until_complete(ui.display_response(None))
    captured = capsys.readouterr()
    assert captured.out == "" or captured.out.strip() == ""


def test_display_tool_start(capsys):
    ui = TerminalUI()
    asyncio.get_event_loop().run_until_complete(
        ui.display_tool_start("shell", {"cmd": "ls"})
    )
    captured = capsys.readouterr()
    assert "shell" in captured.out
    assert "ls" in captured.out


def test_display_tool_done_success(capsys):
    ui = TerminalUI()
    asyncio.get_event_loop().run_until_complete(ui.display_tool_done("shell", "success"))
    captured = capsys.readouterr()
    assert "Done" in captured.out


def test_display_tool_done_error(capsys):
    ui = TerminalUI()
    asyncio.get_event_loop().run_until_complete(ui.display_tool_done("shell", "error"))
    captured = capsys.readouterr()
    assert "Error" in captured.out


def test_display_token(capsys):
    ui = TerminalUI()
    asyncio.get_event_loop().run_until_complete(ui.display_token("Hello"))
    asyncio.get_event_loop().run_until_complete(ui.display_token(" world"))
    captured = capsys.readouterr()
    assert "Hello world" in captured.out


def test_display_session_info(capsys):
    ui = TerminalUI()
    asyncio.get_event_loop().run_until_complete(
        ui.display_session_info("exec_abc123", "coder")
    )
    captured = capsys.readouterr()
    assert "exec_abc123" in captured.out
    assert "coder" in captured.out
