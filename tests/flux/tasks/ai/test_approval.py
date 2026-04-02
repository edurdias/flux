from __future__ import annotations

import inspect

from flux.task import task
from flux.tasks.ai.approval import requires_approval


@task
async def read_file(path: str) -> str:
    """Read a file."""
    return f"contents of {path}"


@task
async def shell(command: str) -> str:
    """Execute a shell command."""
    return f"output of {command}"


@task
async def list_files(directory: str) -> str:
    """List files in a directory."""
    return f"files in {directory}"


def test_requires_approval_single_tool():
    wrapped = requires_approval(shell)
    assert getattr(wrapped, "requires_approval", False) is True


def test_requires_approval_preserves_func():
    wrapped = requires_approval(shell)
    assert wrapped.func.__name__ == "shell"


def test_requires_approval_preserves_name():
    wrapped = requires_approval(shell)
    assert wrapped.name == "shell"


def test_requires_approval_preserves_doc():
    wrapped = requires_approval(shell)
    assert wrapped.func.__doc__ == "Execute a shell command."


def test_requires_approval_preserves_signature():
    wrapped = requires_approval(shell)
    sig = inspect.signature(wrapped.func)
    assert "command" in sig.parameters


def test_requires_approval_is_callable():
    wrapped = requires_approval(shell)
    assert callable(wrapped)


def test_requires_approval_list_wraps_all():
    tools = requires_approval([shell, read_file, list_files])
    assert len(tools) == 3
    for t in tools:
        assert getattr(t, "requires_approval", False) is True


def test_requires_approval_list_with_only():
    tools = requires_approval([shell, read_file, list_files], only=["shell"])
    shell_tool = [t for t in tools if t.func.__name__ == "shell"][0]
    read_tool = [t for t in tools if t.func.__name__ == "read_file"][0]
    list_tool = [t for t in tools if t.func.__name__ == "list_files"][0]
    assert getattr(shell_tool, "requires_approval", False) is True
    assert getattr(read_tool, "requires_approval", False) is False
    assert getattr(list_tool, "requires_approval", False) is False


def test_unwrapped_tool_has_no_requires_approval():
    assert getattr(shell, "requires_approval", False) is False


def test_requires_approval_with_options_preserves_flag():
    wrapped = requires_approval(shell)
    renamed = wrapped.with_options(name="shell_1")
    assert getattr(renamed, "requires_approval", False) is True


def test_requires_approval_only_matches_func_name():
    @task.with_options(name="custom_name")
    async def my_func(x: str) -> str:
        """A tool."""
        return x

    tools = requires_approval([my_func], only=["my_func"])
    assert getattr(tools[0], "requires_approval", False) is True
