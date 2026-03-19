from __future__ import annotations

from typing import Any
from collections.abc import Iterator

from flux.output_storage import OutputStorage, OutputStorageReference
from flux.task import task
from flux.tasks.mcp.tool_builder import build_tools


class ToolSet:
    def __init__(self, tools: dict[str, task], schemas: list[dict[str, Any]]):
        self._tools = tools
        self._schemas = schemas

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return self._schemas

    def __getattr__(self, name: str) -> task:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._tools[name]
        except KeyError:
            raise AttributeError(
                f"ToolSet has no tool '{name}'. Available: {list(self._tools.keys())}",
            )

    def __iter__(self) -> Iterator[task]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


class ToolSetOutputStorage(OutputStorage):
    def __init__(self, client: Any, server_name: str, **task_options: Any):
        self._client = client
        self._server_name = server_name
        self._task_options = task_options

    def store(self, reference_id: str, value: Any) -> OutputStorageReference:
        schemas = value.schemas if isinstance(value, ToolSet) else value
        return OutputStorageReference(
            storage_type="mcp_toolset",
            reference_id=reference_id,
            metadata={"schemas": schemas},
        )

    def retrieve(self, reference: OutputStorageReference) -> ToolSet:
        schemas = reference.metadata["schemas"]
        tools_dict = build_tools(schemas, self._client, self._server_name, **self._task_options)
        return ToolSet(tools_dict, schemas)

    def delete(self, reference: OutputStorageReference) -> None:
        pass
