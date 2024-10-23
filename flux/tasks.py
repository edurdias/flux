from __future__ import annotations

import os
import random
import time
import uuid
from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Callable
from typing import Never

import flux.decorators as decorators
from flux.errors import WorkflowPausedError
from flux.executors import WorkflowExecutor


@decorators.task
def now() -> datetime:
    return datetime.now()


@decorators.task
def uuid4() -> uuid.UUID:
    return uuid.uuid4()


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
            result = yield future.result()
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


@decorators.task.with_options(name="pause_{reference}")
def pause(reference: str) -> Never:
    raise WorkflowPausedError(reference)


@decorators.task.with_options(name="call_workflow_{workflow}")
def call_workflow(workflow: str | decorators.workflow, input: Any | None = None):
    name = (
        workflow.name
        if isinstance(
            workflow,
            decorators.workflow,
        )
        else str(workflow)
    )
    return WorkflowExecutor.current().execute(name, input).output


@decorators.task
def pipeline(tasks: list[decorators.task], input: Any):
    result = input
    for task in tasks:
        result = yield task(result)
    return result


class graph:
    START = "START"
    END = "END"

    def __init__(self, name: str):
        self._name = name
        self._nodes: dict[str, decorators.task] = {}
        self._edges: dict[tuple[str, str], Callable[[Any, Any], bool]] = {}

    def set_entry_point(self, node: str) -> graph:
        self.add_edge(graph.START, node)
        return self

    def set_finish_point(self, node: str) -> graph:
        self.add_edge(node, graph.END)
        return self

    def add_node(self, name: str, node: decorators.task) -> graph:
        if name in self._nodes:
            raise ValueError(f"Node {name} already present.")
        self._nodes[name] = node
        return self

    def add_edge(
        self,
        start_node: str,
        end_node: str,
        condition: Callable[[Any, Any], bool] = lambda i, r: True,
    ) -> graph:
        if start_node != graph.START and start_node not in self._nodes:
            raise ValueError(f"Node {start_node} must be present.")

        if end_node != graph.END and end_node not in self._nodes:
            raise ValueError(f"Node {end_node} must be present.")

        if end_node == graph.START:
            raise ValueError("START cannot be an end_node")

        if start_node == graph.END:
            raise ValueError("END cannot be an start_node")

        self._edges[(start_node, end_node)] = condition

        return self

    @decorators.task.with_options(name="graph_{self._name}")
    def __call__(self, input: Any | None = None):
        name = self.__get_edge_for(graph.START, input)
        if not name:
            raise ValueError("Entry point must be defined.")

        if name == graph.END:
            return

        entry_point_node = self._nodes[name]
        result = yield (entry_point_node(input) if input else entry_point_node())
        name = self.__get_edge_for(name, input, result)

        while name is not None and name != graph.END:
            node = self._nodes[name]
            result = yield node(result)
            name = self.__get_edge_for(name, input, result)

        return result

    def __get_edge_for(
        self,
        node: str,
        input: Any | None = None,
        result: Any | None = None,
    ):
        for start, end in self._edges:
            if start == node and self._edges[(start, end)](input, result):
                return end
        return None
