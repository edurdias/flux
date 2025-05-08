from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Awaitable
from collections.abc import Coroutine
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Callable
from typing import Literal
from typing import TypeVar

import flux.decorators as decorators
from flux.catalogs import WorkflowCatalog
from flux.context import WorkflowExecutionContext

T = TypeVar("T", bound=Any)


@decorators.task
async def now() -> datetime:
    return datetime.now()


@decorators.task
async def uuid4() -> uuid.UUID:
    return uuid.uuid4()


@decorators.task
async def choice(options: list[Any]) -> int:
    return random.choice(options)


@decorators.task
async def randint(a: int, b: int) -> int:
    return random.randint(a, b)


@decorators.task
async def randrange(start: int, stop: int | None = None, step: int = 1):
    return random.randrange(start, stop, step)


@decorators.task
async def parallel(*functions: Coroutine[Any, Any, Any]) -> list[Any]:
    tasks: list[asyncio.Task] = [asyncio.create_task(f) for f in functions]
    return await asyncio.gather(*tasks)


@decorators.task
async def sleep(duration: float | timedelta):
    """
    Pauses the execution of the workflow for a given duration.

    :param duration: The amount of time to sleep.
        - If `duration` is a float, it represents the number of seconds to sleep.
        - If `duration` is a timedelta, it will be converted to seconds using the `total_seconds()` method.

    :raises TypeError: If `duration` is neither a float nor a timedelta.
    """
    if isinstance(duration, timedelta):
        duration = duration.total_seconds()
    await asyncio.sleep(duration)


@decorators.task.with_options(name="call_workflow_{workflow}")
async def call(workflow: str | decorators.workflow, *args):
    if isinstance(workflow, str):
        workflow = WorkflowCatalog.create().get(workflow)
    ctx = await workflow(WorkflowExecutionContext(workflow.name, *args))
    return ctx.output


@decorators.task
async def pipeline(*tasks: Callable, input: Any):
    result = input
    for task in tasks:
        result = await task(result)
    return result


async def default_action(arg: Any) -> Any:
    return arg


class Graph:
    @dataclass
    class Node:
        name: str
        upstream: dict[str, Callable[..., Awaitable[Any]]] = field(default_factory=dict)
        state: Literal["pending", "completed"] = "pending"
        action: Callable[..., Awaitable[Any]] = field(default=lambda _: default_action(True))
        output: Any = None

        def __hash__(self):
            return hash(self.name)

    START = Node(name="START", action=default_action)
    END = Node(name="END", action=default_action)

    def __init__(self, name: str):
        self._name = name
        self._nodes: dict[str, Graph.Node] = {"START": Graph.START, "END": Graph.END}

    def start_with(self, node: str) -> Graph:
        self.add_edge(Graph.START.name, node)
        return self

    def end_with(self, node: str) -> Graph:
        self.add_edge(node, Graph.END.name)
        return self

    def add_node(self, name: str, action: Callable[..., Any]) -> Graph:
        if name in self._nodes:
            raise ValueError(f"Node {name} already present.")
        self._nodes[name] = Graph.Node(name=name, action=action)
        return self

    def add_edge(
        self,
        start_node: str,
        end_node: str,
        condition: Callable[..., Awaitable[bool]] = lambda _: default_action(True),
    ) -> Graph:
        if start_node not in self._nodes:
            raise ValueError(f"Node {start_node} must be present.")

        if end_node not in self._nodes:
            raise ValueError(f"Node {end_node} must be present.")

        if end_node == Graph.START.name:
            raise ValueError("START cannot be an end_node")

        if start_node == Graph.END.name:
            raise ValueError("END cannot be an start_node")

        self._nodes[end_node].upstream[start_node] = condition
        return self

    def validate(self) -> Graph:
        has_start = any(Graph.START.name in node.upstream for node in self._nodes.values())
        if not has_start:
            raise ValueError("Graph must have a starting node.")

        has_end = self._nodes[Graph.END.name].upstream
        if not has_end:
            raise ValueError("Graph must have a ending node.")

        def dfs(node_name: str, visited: set):
            if node_name in visited:
                return
            visited.add(node_name)
            for neighbor_name, node in self._nodes.items():
                if node_name in node.upstream:
                    dfs(neighbor_name, visited)

        visited: set = set()
        dfs(Graph.START.name, visited)
        if len(visited) != len(self._nodes):
            raise ValueError("Not all nodes are connected.")

        return self

    @decorators.task.with_options(name="graph_{self._name}")
    async def __call__(self, input: Any | None = None):
        self.validate()
        await self.__execute_node(Graph.START.name, input)
        return self._nodes[Graph.END.name].output

    async def __execute_node(self, name: str, input: Any | None = None):
        node = self._nodes[name]
        if self.__can_execute(node):
            upstream_outputs = (
                [input]
                if name == Graph.START.name
                else [up.output for up in self.__get_upstream(node)]
            )
            node.output = await node.action(*upstream_outputs)
            node.state = "completed"
            for dnode in self.__get_downstream(node):
                await self.__execute_node(dnode.name)

    def __can_execute(self, node: Graph.Node) -> bool:
        for name, ok_to_proceed in node.upstream.items():
            upstream = self._nodes[name]
            if (
                upstream.state == "pending"
                or not ok_to_proceed(upstream.output)
                or not self.__can_execute(upstream)
            ):
                return False
        return True

    def __get_upstream(self, node):
        return [self._nodes[name] for name in node.upstream]

    def __get_downstream(self, node: Graph.Node):
        return [dnode for dnode in self._nodes.values() if node.name in dnode.upstream]
