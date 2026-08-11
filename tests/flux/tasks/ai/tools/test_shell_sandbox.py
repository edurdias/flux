"""The shell tool requires a container, or an explicit opt-out.

``run_security_checks`` matches the raw command string while ``/bin/sh -c``
performs quote removal and word splitting first, so the checks are evadable by
reassembly (``s'u'do``, ``cd $HOME/.ssh && echo >> authorized_keys``). They stay
as speed bumps; the boundary is the container the execution runs in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flux.tasks.ai.tools.system_tools import SANDBOX_ENV_VAR, system_tools


def _tool_names(tools) -> set[str]:
    return {t.func.__name__ for t in tools}


def test_shell_is_refused_without_a_sandbox(tmp_path, monkeypatch):
    monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
    with pytest.raises(ValueError, match="allow_unsandboxed_shell"):
        system_tools(workspace=tmp_path)


def test_shell_is_built_inside_a_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
    assert "shell" in _tool_names(system_tools(workspace=tmp_path))


def test_opt_out_builds_shell_unsandboxed(tmp_path, monkeypatch):
    monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
    tools = system_tools(workspace=tmp_path, allow_unsandboxed_shell=True)
    assert "shell" in _tool_names(tools)


def test_file_tools_do_not_require_a_sandbox(tmp_path, monkeypatch):
    """Only shell is gated: the file, search and directory tools enforce the
    workspace boundary themselves."""
    monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
    names = _tool_names(system_tools(workspace=tmp_path, include_shell=False))
    assert {"read_file", "grep", "directory_tree"} <= names
    assert "shell" not in names


def test_shell_declares_its_risk(tmp_path, monkeypatch):
    """risk drives the agent's ordering barrier and the approval preamble;
    undeclared, shell ran concurrently with its batch siblings as a read."""
    monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
    shell = next(t for t in system_tools(workspace=tmp_path) if t.func.__name__ == "shell")
    assert shell.risk == "exec"


class TestSandboxMarker:
    def test_docker_runner_marks_the_child(self):
        from flux.runners.docker import DockerRunner

        runner = DockerRunner(image="flux:test")
        command = runner._build_command("flux-exec-x")
        assert f"{SANDBOX_ENV_VAR}=1" in command

    def test_airgapped_runner_marks_the_child(self):
        from flux.runners.docker import AirgappedDockerRunner
        from unittest.mock import MagicMock, patch

        with (
            patch("flux.runners.docker.shutil.which", return_value="/usr/bin/docker"),
            patch("flux.runners.docker.subprocess.run") as run,
        ):
            run.return_value = MagicMock(returncode=0, stdout="27", stderr="")
            runner = AirgappedDockerRunner(image="flux:test")
        command = runner._build_command("flux-exec-x")
        assert f"{SANDBOX_ENV_VAR}=1" in command

    def test_subprocess_runner_does_not(self):
        """The subprocess child runs on the worker host; claiming otherwise
        would defeat the check."""
        from flux.runners.subprocess_runner import child_environment

        assert SANDBOX_ENV_VAR not in child_environment()


def test_agent_template_does_not_force_a_runner():
    """Pinning the template would force a container on every agent, including
    those with no shell tool. The gate fires only when shell is built; the
    runner is the operator's choice (worker default_runner, or their own
    workflow)."""
    from flux.agents.template import agent_chat

    assert agent_chat.runner is None


def test_workspace_still_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
    tools = system_tools(workspace=str(tmp_path))
    assert tools and isinstance(tmp_path, Path)


class TestResolverGate:
    """The YAML tool groups build shell directly rather than through
    system_tools(), so the check has to live where both can reach it."""

    def test_system_tools_group_is_gated(self, monkeypatch):
        from flux.agents.tools_resolver import resolve_builtin_tools

        monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
        with pytest.raises(ValueError, match="allow_unsandboxed_shell"):
            resolve_builtin_tools([{"system_tools": {"workspace": "."}}])

    def test_shell_group_is_gated(self, monkeypatch):
        from flux.agents.tools_resolver import resolve_builtin_tools

        monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
        with pytest.raises(ValueError, match="allow_unsandboxed_shell"):
            resolve_builtin_tools([{"shell": {"workspace": "."}}])

    def test_group_opt_out_is_honoured(self, monkeypatch):
        from flux.agents.tools_resolver import resolve_builtin_tools

        monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
        tools = resolve_builtin_tools(
            [{"shell": {"workspace": ".", "allow_unsandboxed_shell": True}}],
        )
        assert "shell" in {t.func.__name__ for t in tools}

    def test_non_shell_groups_are_not_gated(self, monkeypatch):
        from flux.agents.tools_resolver import resolve_builtin_tools

        monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
        names = {t.func.__name__ for t in resolve_builtin_tools([{"files": {"workspace": "."}}])}
        assert "read_file" in names
