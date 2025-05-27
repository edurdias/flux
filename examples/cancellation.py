"""Example demonstrating workflow cancellation.

This example simulates a complete cancellation flow including server and worker roles
in a self-contained script, without requiring external components.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from flux import ExecutionContext
from flux.task import task
from flux.worker_registry import WorkerInfo
from flux.workflow import workflow


@task
async def long_running_task(iterations: int = 10):
    """A long running task that can be cancelled."""
    results = []
    try:
        for i in range(iterations):
            # Simulate work
            await asyncio.sleep(1)
            print(f"[TASK] Completed iteration {i + 1}/{iterations}")
            results.append(i)
        print("[TASK] Task completed all iterations")
        return results
    except asyncio.CancelledError:
        print("[TASK] Task was cancelled")
        raise


@workflow
async def cancellable_workflow(ctx: ExecutionContext[dict[str, Any]]):
    """A workflow that demonstrates cancellation."""
    if not ctx.input:
        iterations = 10
    else:
        iterations = ctx.input.get("iterations", 10)

    print(f"[WORKFLOW] Starting cancellable workflow with {iterations} iterations")

    try:
        result = await long_running_task(iterations)
        print(f"[WORKFLOW] Completed successfully with result: {result}")
        return result
    except asyncio.CancelledError:
        print("[WORKFLOW] Caught CancelledError - workflow was cancelled")
        # Let the workflow decorator handle the cancellation
        raise


class SimulatedServer:
    """Simulates the server component of Flux."""

    def __init__(self):
        self.contexts: dict[str, ExecutionContext] = {}
        self.cancellation_queue = asyncio.Queue()

    def register_context(self, ctx: ExecutionContext):
        """Register an execution context with the server."""
        self.contexts[ctx.execution_id] = ctx
        print(f"[SERVER] Registered execution context: {ctx.execution_id}")
        return ctx

    def request_cancellation(self, execution_id: str):
        """Request cancellation of a workflow execution."""
        if execution_id not in self.contexts:
            print(f"[SERVER] Error: Execution {execution_id} not found")
            return False

        ctx = self.contexts[execution_id]
        if ctx.has_finished:
            print(f"[SERVER] Error: Execution {execution_id} already finished")
            return False

        # Mark as cancelling
        ctx.start_cancel()
        self.contexts[execution_id] = ctx
        print(f"[SERVER] Execution {execution_id} marked as CANCELLING")

        # Queue for worker to pick up
        self.cancellation_queue.put_nowait(execution_id)
        print("[SERVER] Cancellation request queued for worker")
        return True

    def update_context(self, ctx: ExecutionContext):
        """Update an execution context in the server."""
        self.contexts[ctx.execution_id] = ctx
        print(f"[SERVER] Updated execution context: {ctx.execution_id}, state: {ctx.state.value}")
        return ctx

    def get_context(self, execution_id: str):
        """Get an execution context from the server."""
        return self.contexts.get(execution_id)


# --------------------- Simulated Worker Component ---------------------


class SimulatedWorker:
    """Simulates the worker component of Flux."""

    def __init__(self, server: SimulatedServer):
        self.server = server
        self.running_workflows: dict[str, asyncio.Task] = {}
        self.worker_id = f"worker-{uuid4().hex[:6]}"

    async def start_cancellation_listener(self):
        """Listen for cancellation requests from the server."""
        print("[WORKER] Starting cancellation listener")
        while True:
            execution_id = await self.server.cancellation_queue.get()
            print(f"[WORKER] Received cancellation request for: {execution_id}")
            await self.handle_cancellation(execution_id)
            self.server.cancellation_queue.task_done()

    async def handle_cancellation(self, execution_id: str):
        """Handle a cancellation request."""
        if execution_id not in self.running_workflows:
            print(f"[WORKER] Workflow {execution_id} not running on this worker")
            return

        task = self.running_workflows[execution_id]
        print(f"[WORKER] Cancelling workflow task: {execution_id}")
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            print(f"[WORKER] Workflow {execution_id} cancelled successfully")
        finally:
            if execution_id in self.running_workflows:
                self.running_workflows.pop(execution_id)

    async def execute_workflow(self, workflow_func, ctx: ExecutionContext):
        """Execute a workflow and track it for possible cancellation."""
        print(f"[WORKER] Starting execution of workflow: {ctx.workflow_name} ({ctx.execution_id})")

        # Claim the execution
        ctx.claim(WorkerInfo(name=self.worker_id))
        self.server.update_context(ctx)

        # Start the workflow
        task = asyncio.create_task(workflow_func(ctx))
        self.running_workflows[ctx.execution_id] = task

        try:
            # Wait for the workflow to complete
            result_ctx = await task
            print(f"[WORKER] Workflow completed: {ctx.execution_id}")
            return result_ctx
        except asyncio.CancelledError:
            # If cancelled, update the context
            ctx = self.server.get_context(ctx.execution_id)
            if ctx:
                ctx.cancel()
                self.server.update_context(ctx)
            print(f"[WORKER] Workflow cancellation completed: {ctx.execution_id}")
            raise
        finally:
            # Clean up
            if ctx.execution_id in self.running_workflows:
                self.running_workflows.pop(ctx.execution_id)


# --------------------- Main Example ---------------------


async def run_cancellation_example():
    """Run the complete cancellation example."""
    print("Starting cancellation example...\n")

    # Create the server and worker
    server = SimulatedServer()
    worker = SimulatedWorker(server)

    # Start the cancellation listener
    listener_task = asyncio.create_task(worker.start_cancellation_listener())

    try:
        # Create execution context
        ctx = ExecutionContext(
            workflow_id="cancellable_workflow",
            workflow_name="cancellable_workflow",
            input={"iterations": 5},  # Run for 5 iterations
        )

        # Register with server
        server.register_context(ctx)

        # Start the workflow on the worker
        workflow_task = asyncio.create_task(worker.execute_workflow(cancellable_workflow, ctx))

        # Wait a bit to let the workflow start
        await asyncio.sleep(3)

        # Request cancellation
        print("\n[CLIENT] Requesting workflow cancellation...\n")
        server.request_cancellation(ctx.execution_id)

        # Wait for the workflow to complete or be cancelled
        try:
            await workflow_task
            print("[CLIENT] Workflow completed normally (unexpected)")
        except asyncio.CancelledError:
            print("[CLIENT] Workflow was cancelled as expected")

        # Check final state
        final_ctx = server.get_context(ctx.execution_id)
        print(f"\n[SUMMARY] Final workflow state: {final_ctx.state.value}")
        print(f"[SUMMARY] Is cancelled: {final_ctx.is_cancelled}")
        print(f"[SUMMARY] Has finished: {final_ctx.has_finished}")

    finally:
        # Clean up
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    # Run the example
    asyncio.run(run_cancellation_example())
