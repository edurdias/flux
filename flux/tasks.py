import os
import time
import uuid
import random

from typing import Callable, NoReturn, Optional, Self
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import flux.decorators as d
from flux.executors import WorkflowExecutor
from flux.errors import WorkflowPausedError


@d.task
def now() -> datetime:
    return datetime.now()


@d.task
def uuid4() -> uuid.UUID:
    return uuid.uuid4()


@d.task
def randint(a: int, b: int) -> int:
    return random.randint(a, b)


@d.task
def randrange(start: int, stop: int | None = None, step: int = 1):
    return random.randrange(start, stop, step)


@d.task
def parallel(*functions: Callable):
    results = []
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = [executor.submit(func) for func in functions]
        for future in as_completed(futures):
            result = yield future.result()
            results.append(result)
    return results


@d.task
def sleep(duration: float | timedelta) -> NoReturn:
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


@d.task.with_options(name="pause_{reference}")
def pause(reference: str) -> NoReturn:
    raise WorkflowPausedError(reference)


@d.task.with_options(name="call_workflow_{workflow}")
def call_workflow(workflow: str | d.workflow, input: any = None):
    name = workflow.name if isinstance(workflow, d.workflow) else str(workflow)
    return WorkflowExecutor.current().execute(name, input).output


@d.task
def pipeline(tasks: list[d.task], input: any):
    result = input
    for task in tasks:
        result = yield task(result)
    return result


class graph:

    START = "START"
    END = "END"

    def __init__(self, name: str):
        self._name = name
        self._nodes: dict[str, d.task] = {}
        self._edges: dict[tuple[str, str], Callable[[any, any], bool]] = {}

    def set_entry_point(self, node: str) -> Self:
        self.add_edge(graph.START, node)
        return self

    def set_finish_point(self, node: str) -> Self:
        self.add_edge(node, graph.END)
        return self

    def add_node(self, name: str, node: d.task | Callable[[any], any]) -> Self:
        if name in self._nodes:
            raise ValueError(f"Node {name} already present.")
        self._nodes[name] = node
        return self

    def add_edge(
        self,
        start_node: str,
        end_node: str,
        condition: Callable[[any, any], bool] = lambda i, r: True,
    ) -> Self:
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

    @d.task.with_options(name="graph_{self._name}")
    def __call__(self, input: Optional[any] = None):

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
        self, node: str, input: Optional[any] = None, result: Optional[any] = None
    ):
        for start, end in self._edges:
            if start == node and self._edges[(start, end)](input, result):
                return end
        return None
