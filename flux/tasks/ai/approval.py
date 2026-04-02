from __future__ import annotations

from typing import Any, overload

from flux.task import task


class _ApprovalWrapper:
    """Transparent wrapper that adds .requires_approval to a task."""

    def __init__(self, wrapped: task):
        self._wrapped = wrapped
        self.requires_approval = True

    @property
    def func(self):
        return self._wrapped.func if hasattr(self._wrapped, "func") else self._wrapped

    @property
    def name(self):
        return self._wrapped.name

    @name.setter
    def name(self, value):
        self._wrapped.name = value

    @property
    def description(self):
        return getattr(self._wrapped, "description", None)

    @description.setter
    def description(self, value):
        self._wrapped.description = value

    def with_options(self, **kwargs) -> _ApprovalWrapper:
        new_task = (
            self._wrapped.with_options(**kwargs)
            if hasattr(self._wrapped, "with_options")
            else self._wrapped
        )
        return _ApprovalWrapper(new_task)

    async def __call__(self, *args, **kwargs):
        return await self._wrapped(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


@overload
def requires_approval(tool: task, *, only: list[str] | None = None) -> _ApprovalWrapper:
    ...


@overload
def requires_approval(tool: list, *, only: list[str] | None = None) -> list:
    ...


def requires_approval(
    tool: Any,
    *,
    only: list[str] | None = None,
) -> Any:
    """Mark tools as requiring human approval before execution.

    Args:
        tool: A single task or list of tasks.
        only: If provided, only wrap tools whose func.__name__ is in this list.
            Ignored when wrapping a single tool.

    Returns:
        Wrapped tool(s) with .requires_approval = True.
    """
    if isinstance(tool, list):
        result = []
        for t in tool:
            func = t.func if hasattr(t, "func") else t
            func_name = func.__name__
            if only is None or func_name in only:
                result.append(_ApprovalWrapper(t))
            else:
                result.append(t)
        return result

    return _ApprovalWrapper(tool)
