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

from flux import ExecutionContext
from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.errors import ExecutionError, PauseRequested
import flux

from flux.task import TaskMetadata

T = TypeVar("T", bound=Any)


@flux.task
async def now() -> datetime:
    return datetime.now()


@flux.task
async def uuid4() -> uuid.UUID:
    return uuid.uuid4()


@flux.task
async def choice(options: list[Any]) -> int:
    return random.choice(options)


@flux.task
async def randint(a: int, b: int) -> int:
    return random.randint(a, b)


@flux.task
async def randrange(start: int, stop: int | None = None, step: int = 1):
    return random.randrange(start, stop, step)


@flux.task
async def parallel(*functions: Coroutine[Any, Any, Any]) -> list[Any]:
    tasks: list[asyncio.Task] = [asyncio.create_task(f) for f in functions]
    return await asyncio.gather(*tasks)


@flux.task
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


@flux.task.with_options(name="call_workflow_{workflow}")
async def call(workflow: flux.workflow | str, *args):
    """Call a workflow directly or via the HTTP API in sync mode.

    Args:
        workflow: The workflow to call, can be either:
            - A workflow object: Will be called directly
            - A string: Will be called via the HTTP API

    Raises:
        WorkflowNotFoundError: If the workflow does not exist (when using string name)
        ExecutionError: If the workflow execution fails

    Returns:
        Any: The output of the workflow execution
    """
    from flux.errors import WorkflowNotFoundError
    from flux.domain.execution_context import ExecutionContext

    if isinstance(workflow, flux.workflow):
        workflow = workflow.name

    # If workflow is a string, call it via HTTP API
    import httpx
    from flux.config import Configuration

    settings = Configuration.get().settings
    server_url = settings.workers.server_url

    try:
        url = f"{server_url}/workflows/{workflow}/run/sync?detailed=true"
        payload = args[0] if len(args) == 1 else args
        # Make a synchronous HTTP request to execute the workflow
        with httpx.Client(timeout=settings.workers.default_timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()

            data = response.json()
            ctx: ExecutionContext = ExecutionContext(
                workflow_id=data["workflow_id"],
                workflow_name=data["workflow_name"],
                input=data["input"],
                execution_id=data["execution_id"],
                state=data["state"],
                events=[
                    ExecutionEvent(
                        type=ExecutionEventType(event["type"]),
                        source_id=event["source_id"],
                        name=event["name"],
                        value=event.get("value"),
                    )
                    for event in data["events"]
                ],
                requests=data.get("requests", []),
            )

            if ctx.has_succeeded:
                return ctx.output

            if ctx.has_failed:
                raise ExecutionError(ctx.output)

            # TODO: add support for paused workflows
            if ctx.is_paused:
                raise ExecutionError(
                    message=f"Workflow execution {ctx.workflow_name} was paused, but is not supported for nested calls.",
                )

    except httpx.ConnectError as ex:
        raise ExecutionError(
            message=f"Could not connect to the Flux server at {server_url}.",
        ) from ex
    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            raise WorkflowNotFoundError(name=workflow)
        raise ExecutionError(ex) from ex
    except Exception as ex:
        raise ExecutionError(ex) from ex


@flux.task
async def pipeline(*tasks: Callable, input: Any):
    result = input
    for task in tasks:
        result = await task(result)
    return result


@flux.task.with_options(metadata=True)
async def pause(name: str, metadata: TaskMetadata):
    ctx = await ExecutionContext.get()

    if ctx.has_resumed:
        ctx.events.append(
            ExecutionEvent(
                type=ExecutionEventType.TASK_RESUMED,
                source_id=metadata.task_id,
                name=metadata.task_name,
                value=name,
            ),
        )
        return name
    raise PauseRequested(name=name)


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

    @flux.task.with_options(name="graph_{self._name}")
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

    async def __can_execute(self, node: Graph.Node) -> bool:
        for name, ok_to_proceed in node.upstream.items():
            upstream = self._nodes[name]
            if (
                upstream.state == "pending"
                or not await ok_to_proceed(upstream.output)
                or not await self.__can_execute(upstream)
            ):
                return False
        return True

    def __get_upstream(self, node):
        return [self._nodes[name] for name in node.upstream]

    def __get_downstream(self, node: Graph.Node):
        return [dnode for dnode in self._nodes.values() if node.name in dnode.upstream]
