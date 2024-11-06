from __future__ import annotations

import os
import random
import time
import uuid
from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from types import GeneratorType
from typing import Any
from typing import Callable
from typing import Literal

import flux.decorators as decorators
from flux.executors import WorkflowExecutor


@decorators.task
def now() -> datetime:
    return datetime.now()


@decorators.task
def uuid4() -> uuid.UUID:
    return uuid.uuid4()


@decorators.task
def choice(options: list[Any]) -> int:
    return random.choice(options)


@decorators.task
def randint(a: int, b: int) -> int:
    return random.randint(a, b)


@decorators.task
def randrange(start: int, stop: int | None = None, step: int = 1):
    return random.randrange(start, stop, step)


@decorators.task
def parallel(*functions: Callable):
    results = []
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = [executor.submit(func) for func in functions]
        for future in as_completed(futures):
            result = yield from future.result()
            results.append(result)
    return results


@decorators.task
def sleep(duration: float | timedelta):
    """
    Pauses the execution of the workflow for a given duration.

    :param duration: The amount of time to sleep.
        - If `duration` is a float, it represents the number of seconds to sleep.
        - If `duration` is a timedelta, it will be converted to seconds using the `total_seconds()` method.

    :raises TypeError: If `duration` is neither a float nor a timedelta.
    """
    if isinstance(duration, timedelta):
        duration = duration.total_seconds()
    time.sleep(duration)


@decorators.task.with_options(name="call_workflow_{workflow}")
def call_workflow(workflow: str | decorators.workflow, input: Any | None = None):
    if isinstance(workflow, decorators.workflow):
        return workflow.run(input).output
    return WorkflowExecutor.get().execute(str(workflow), input).output


@decorators.task
def pipeline(*tasks: Callable, input: Any):
    result = input
    for task in tasks:
        result = yield from task(result)
    return result


class Graph:
    @dataclass
    class Node:
        name: str
        upstream: dict[str, Callable[..., Any]] = field(default_factory=dict)
        state: Literal["pending", "completed"] = "pending"
        action: Callable[..., Any] = field(default=lambda: True)
        output: Any = None

        def __hash__(self):
            return hash(self.name)

    START = Node(name="START", action=lambda i: i)
    END = Node(name="END", action=lambda i: i)

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
        condition: Callable[..., bool] = lambda _: True,
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
    def __call__(self, input: Any | None = None):
        self.validate()
        yield from self.__execute_node(Graph.START.name, input)
        return self._nodes[Graph.END.name].output

    def __execute_node(self, name: str, input: Any | None = None):
        node = self._nodes[name]
        if self.__can_execute(node):
            upstream_outputs = (
                [input]
                if name == Graph.START.name
                else [up.output for up in self.__get_upstream(node)]
            )
            output = node.action(*upstream_outputs)
            node.output = (yield output) if isinstance(output, GeneratorType) else output
            node.state = "completed"
            for dnode in self.__get_downstream(node):
                yield from self.__execute_node(dnode.name)

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
