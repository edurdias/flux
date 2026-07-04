"""Tests for the runner abstraction: subprocess protocol, crash mapping, selection."""

from __future__ import annotations

import asyncio
import base64
import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

from flux.domain.execution_context import ExecutionContext
from flux.errors import WorkerProcessCrashed
from flux.runners.base import RunnerHooks
from flux.runners.subprocess_runner import SubprocessRunner
from flux.worker import WorkflowDefinition, WorkflowExecutionRequest


def make_request(source: str, name: str, input=None, transient: bool = False):
    ctx: ExecutionContext = ExecutionContext(
        workflow_id=f"default/{name}",
        workflow_namespace="default",
        workflow_name=name,
        input=input,
    )
    if transient:
        ctx.mark_transient()
    return WorkflowExecutionRequest(
        workflow=WorkflowDefinition(
            id=f"default/{name}",
            namespace="default",
            name=name,
            version=1,
            source=base64.b64encode(textwrap.dedent(source).encode()).decode(),
        ),
        context=ctx,
    )


def make_hooks(checkpoints=None, secrets=None, configs=None, progress=None):
    async def capture_checkpoint(ctx):
        if checkpoints is not None:
            checkpoints.append(ctx)

    async def get_secrets(names):
        return {n: (secrets or {}).get(n, f"secret-{n}") for n in names}

    async def get_configs(names):
        return {n: (configs or {}).get(n, f"config-{n}") for n in names}

    return RunnerHooks(
        checkpoint=capture_checkpoint,
        get_secrets=get_secrets,
        get_configs=get_configs,
        progress=progress,
    )


class TestSubprocessRunner:
    @pytest.mark.asyncio
    async def test_executes_workflow_and_forwards_checkpoints(self):
        source = """
        from flux import ExecutionContext, task, workflow

        @task
        async def double(x: int) -> int:
            return x * 2

        @workflow
        async def sub_wf(ctx: ExecutionContext[int]):
            return await double(ctx.input)
        """
        checkpoints = []
        request = make_request(source, "sub_wf", input=21)
        runner = SubprocessRunner(term_grace=5)

        result = await runner.execute(request, make_hooks(checkpoints))

        assert result.has_finished and not result.has_failed
        assert result.output == 42
        # The terminal state reached the checkpoint hook (server-facing path).
        assert checkpoints and checkpoints[-1].has_finished

    @pytest.mark.asyncio
    async def test_workflow_failure_is_a_result_not_a_crash(self):
        source = """
        from flux import ExecutionContext, workflow

        @workflow
        async def failing_wf(ctx: ExecutionContext):
            raise ValueError("intentional")
        """
        request = make_request(source, "failing_wf")
        runner = SubprocessRunner(term_grace=5)

        result = await runner.execute(request, make_hooks())

        assert result.has_finished and result.has_failed

    @pytest.mark.asyncio
    async def test_hard_crash_raises_worker_process_crashed(self):
        source = """
        import os
        from flux import ExecutionContext, workflow

        @workflow
        async def crashing_wf(ctx: ExecutionContext):
            os._exit(9)
        """
        request = make_request(source, "crashing_wf")
        runner = SubprocessRunner(term_grace=5)

        with pytest.raises(WorkerProcessCrashed) as excinfo:
            await runner.execute(request, make_hooks())
        assert excinfo.value.exit_code == 9
        assert excinfo.value.last_context is not None

    @pytest.mark.asyncio
    async def test_secrets_resolve_through_parent_pipe(self):
        source = """
        from flux import ExecutionContext, task, workflow

        @task.with_options(secret_requests=["API_KEY"])
        async def read_secret(secrets: dict = {}):
            return secrets["API_KEY"]

        @workflow
        async def secret_wf(ctx: ExecutionContext):
            return await read_secret()
        """
        request = make_request(source, "secret_wf")
        runner = SubprocessRunner(term_grace=5)

        result = await runner.execute(
            request,
            make_hooks(secrets={"API_KEY": "s3cr3t-from-parent"}),
        )

        assert result.has_finished and not result.has_failed
        assert result.output == "s3cr3t-from-parent"

    @pytest.mark.asyncio
    async def test_cancellation_terminates_child_and_reraises(self):
        source = """
        import asyncio
        from flux import ExecutionContext, task, workflow

        @task
        async def started() -> int:
            return 1

        @workflow
        async def stuck_wf(ctx: ExecutionContext):
            await started()  # checkpointed: signals the parent we're running
            await asyncio.sleep(300)
        """
        checkpoints = []
        request = make_request(source, "stuck_wf")
        runner = SubprocessRunner(term_grace=5)

        task = asyncio.create_task(runner.execute(request, make_hooks(checkpoints)))
        # Wait until the child is genuinely executing (first checkpoint —
        # emitted at the first task boundary — arrives).
        async with asyncio.timeout(60):
            while not checkpoints:
                await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=30)


class TestCrashDurabilityMapping:
    def _make_worker(self):
        from tests.flux.test_worker_checkpoint import make_worker

        return make_worker()

    @pytest.mark.asyncio
    async def test_transient_crash_fails_terminally(self):
        worker = self._make_worker()
        worker._checkpoint = AsyncMock()
        worker._release_claim = AsyncMock()

        ctx = ExecutionContext(
            workflow_id="default/wf",
            workflow_namespace="default",
            workflow_name="wf",
        ).mark_transient()
        ctx.start("e")
        request = MagicMock()
        request.context = ctx

        crash = WorkerProcessCrashed(ctx.execution_id, exit_code=9, last_context=ctx)
        result = await worker._handle_runner_crash(request, crash)

        assert result.has_failed
        worker._checkpoint.assert_awaited()
        worker._release_claim.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_durable_crash_releases_claim_for_redispatch(self):
        worker = self._make_worker()
        worker._checkpoint = AsyncMock()
        worker._release_claim = AsyncMock()

        ctx = ExecutionContext(
            workflow_id="default/wf",
            workflow_namespace="default",
            workflow_name="wf",
        )
        request = MagicMock()
        request.context = ctx

        crash = WorkerProcessCrashed(ctx.execution_id, exit_code=-9, last_context=ctx)
        result = await worker._handle_runner_crash(request, crash)

        assert not result.has_failed
        worker._release_claim.assert_awaited_once_with(ctx.execution_id)
        worker._checkpoint.assert_not_awaited()


class TestRunnerSelection:
    def test_create_runners_rejects_unknown_names(self):
        from flux.runners import create_runners

        config = MagicMock(
            module_cache_ttl=0,
            module_cache_max_size=8,
            subprocess_term_grace=5.0,
            subprocess_memory_limit=0,
        )
        with pytest.raises(ValueError, match="Unknown runner 'kubernetes'"):
            create_runners(["inprocess", "kubernetes"], config)

    @pytest.mark.asyncio
    async def test_unavailable_runner_fails_execution(self):
        from flux.errors import RunnerNotAvailableError
        from tests.flux.test_worker_checkpoint import make_ctx, make_worker

        worker = make_worker()
        worker._runners = {}
        worker._default_runner = "subprocess"
        request = MagicMock()
        request.runner = None
        request.context = make_ctx()

        with pytest.raises(RunnerNotAvailableError):
            await worker._run_workflow(request, make_hooks())

    def test_worker_matches_runner_requirement(self):
        from flux.domain.resource_request import worker_matches

        modern = MagicMock(labels={}, resources=None, packages=[])
        modern.runners = ["inprocess", "subprocess"]
        legacy = MagicMock(labels={}, resources=None, packages=[])
        legacy.runners = None

        assert worker_matches(modern, None, None, runner="subprocess")
        assert worker_matches(modern, None, None, runner=None)
        # Legacy workers predate runners: in-process only.
        assert worker_matches(legacy, None, None, runner="inprocess")
        assert not worker_matches(legacy, None, None, runner="subprocess")

        # An explicitly empty list means "no runners" — it is not legacy and
        # must not fall back to inprocess-only matching.
        none_advertised = MagicMock(labels={}, resources=None, packages=[])
        none_advertised.runners = []
        assert not worker_matches(none_advertised, None, None, runner="inprocess")
        assert not worker_matches(none_advertised, None, None, runner="subprocess")
        assert worker_matches(none_advertised, None, None, runner=None)

    def test_catalog_extracts_runner(self):
        from flux.catalogs import DatabaseWorkflowCatalog

        source = b"""
from flux import ExecutionContext
from flux.workflow import workflow


@workflow.with_options(runner="subprocess")
async def isolated_wf(ctx: ExecutionContext[str]):
    return ctx.input
"""
        catalog = DatabaseWorkflowCatalog.__new__(DatabaseWorkflowCatalog)
        workflows = catalog.parse_static(source)
        assert workflows[0].metadata["runner"] == "subprocess"

    def test_dispatch_payload_carries_runner(self):
        from flux.server import Server

        server = Server(host="localhost", port=8000)
        with pytest.MonkeyPatch.context() as mp:
            workflow_info = MagicMock()
            workflow_info.source = b"src"
            workflow_info.metadata = {"runner": "subprocess"}
            catalog = MagicMock()
            catalog.get.return_value = workflow_info
            mp.setattr(
                "flux.api.worker_routes.WorkflowCatalog",
                MagicMock(create=lambda: catalog),
            )
            session = MagicMock()
            session.get.return_value = None
            mp.setattr(server, "_get_db_session", lambda: session)

            ctx = MagicMock()
            ctx.workflow_namespace = "default"
            ctx.workflow_name = "wf"
            ctx.execution_id = "e1"
            payload = server._build_dispatch_payload(ctx)

        assert payload.get("runner") == "subprocess"
