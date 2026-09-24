from __future__ import annotations

import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flux.domain.execution_context import ExecutionContext
from flux.runners.base import RunnerHooks
from flux.runners.subprocess_runner import SubprocessRunner


def hooks(checkpoint=None):
    return RunnerHooks(
        checkpoint=checkpoint or AsyncMock(),
        get_secrets=AsyncMock(return_value={}),
        get_configs=AsyncMock(return_value={}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("frame", ["[]", "null", "1", '"invalid"'])
async def test_invalid_frame_reaps_real_child(frame):
    class ControlledRunner(SubprocessRunner):
        async def _spawn(self, request):
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                f"import sys,time; sys.stdin.readline(); print({frame!r},flush=True); time.sleep(30)",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return self.proc

    runner = ControlledRunner(execution_timeout=0.1)
    request = SimpleNamespace(
        context=ExecutionContext(
            workflow_id="default/probe",
            workflow_name="probe",
            workflow_namespace="default",
        ),
        workflow=SimpleNamespace(model_dump=lambda: {}),
        exec_token=None,
    )
    try:
        with pytest.raises(ValueError, match="frame"):
            await runner.execute(request, hooks())
        await asyncio.sleep(0.2)
        assert runner.proc.returncode is not None
    finally:
        if runner.proc.returncode is None:
            runner.proc.kill()
        await runner.proc.wait()


@pytest.mark.asyncio
async def test_cancellation_drain_rejects_foreign_execution_checkpoint():
    runner = SubprocessRunner()
    foreign = ExecutionContext(
        workflow_id="default/foreign",
        workflow_name="foreign",
        workflow_namespace="default",
    )
    frame = {"type": "checkpoint", "context": foreign.to_dict()}
    proc = SimpleNamespace(stdout=asyncio.StreamReader(), wait=AsyncMock())
    proc.stdout.feed_data(json.dumps(frame, default=str).encode() + b"\n")
    proc.stdout.feed_eof()
    checkpoint = AsyncMock()
    assert runner._owns_frame(frame, "actual-dispatched-id") is False
    await runner._drain_frames(proc, hooks(checkpoint), "actual-dispatched-id")
    checkpoint.assert_not_awaited()


def test_dynamic_registration_quota_is_enforced_by_concurrent_requests(tmp_path, monkeypatch):
    from flux import dynamic_workflows
    from flux.catalogs import WorkflowCatalog
    from flux.config import Configuration
    from flux.models import DatabaseRepository

    url = f"sqlite:///{tmp_path / 'quota.db'}"
    monkeypatch.setenv("FLUX_DATABASE_URL", url)
    monkeypatch.setenv("FLUX_HOME", str(tmp_path))
    Configuration._instance = None
    Configuration._config = None
    DatabaseRepository._engines.clear()
    Configuration.get().override(database_url=url)
    WorkflowCatalog.create()
    namespace = dynamic_workflows.namespace_for_subject("quota-review")
    original_count = dynamic_workflows._distinct_names
    assert original_count(namespace) == 0

    def read_before_insert(ns, **kwargs):
        count = original_count(ns, **kwargs)
        time.sleep(0.1)
        return count

    config = SimpleNamespace(
        max_source_bytes=65536,
        max_per_agent=1,
        require_runner="docker-airgapped",
    )
    monkeypatch.setattr(dynamic_workflows, "_distinct_names", read_before_insert)

    def register(name):
        source = f"from flux import workflow\n@workflow\nasync def {name}(ctx):\n    return 1\n"
        try:
            return dynamic_workflows.register(
                source.encode(),
                subject="quota-review",
                config=config,
            )
        except dynamic_workflows.DynamicRegistrationError:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(register, ["first", "second"]))
        assert sum(result is not None for result in results) == 1
        assert original_count(namespace) == config.max_per_agent
    finally:
        Configuration._instance = None
        Configuration._config = None
        DatabaseRepository._engines.clear()


def test_negative_provider_usage_is_rejected():
    from flux.tasks.ai.budget import Budget
    from flux.tasks.ai.models import Usage

    budget = Budget(max_tokens=100)
    budget.record(Usage(input_tokens=100))
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        budget.record(Usage(input_tokens=-100))
    assert budget.spent() == 100


@pytest.mark.asyncio
async def test_concurrent_budget_overshoot_exceeds_one_calls_usage():
    from flux.tasks.ai.agent_loop import _call_llm
    from flux.tasks.ai.budget import Budget
    from flux.tasks.ai.models import LLMResponse, Usage

    budget = Budget(max_tokens=100)
    both_entered = asyncio.Event()
    entered = 0

    async def respond(*args, **kwargs):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=5)
        return LLMResponse(text="ok", usage=Usage(input_tokens=150))

    formatter = SimpleNamespace(supports_reasoning_stream=True, call_with_reasoning_stream=respond)
    await asyncio.gather(
        *(_call_llm(None, formatter, [], {}, None, True, i, budget) for i in range(2)),
    )
    assert budget.spent() - budget.max_tokens == 200 > 150


@pytest.mark.asyncio
async def test_execute_cancellation_rejects_foreign_checkpoint_from_real_child(tmp_path):
    from flux.config import Configuration
    from flux.context_managers import ContextManager
    from flux.domain import ExecutionState

    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'victim.db'}")
    manager = ContextManager.create()
    foreign = ExecutionContext(
        workflow_id="default/foreign",
        workflow_name="foreign",
        workflow_namespace="default",
    )
    foreign._state = ExecutionState.RUNNING
    foreign._current_worker = "same-worker"
    manager.save(foreign)
    foreign.complete(foreign.execution_id, "forged-output")
    foreign_frame = json.dumps({"type": "checkpoint", "context": foreign.to_dict()}, default=str)
    ready = asyncio.Event()

    class ControlledRunner(SubprocessRunner):
        async def _spawn(self, request):
            script = (
                "import signal,sys,time\n"
                "sys.stdin.readline()\n"
                "def stop(*args):\n"
                f"    print({foreign_frame!r}, flush=True)\n"
                "    sys.exit(0)\n"
                "signal.signal(signal.SIGTERM,stop)\n"
                'print(\'{"type":"progress","task_id":"x",'
                '"task_name":"ready","value":1}\',flush=True)\n'
                "time.sleep(30)\n"
            )
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return self.proc

    runner = ControlledRunner(term_grace=1)
    request = SimpleNamespace(
        context=ExecutionContext(
            workflow_id="default/probe",
            workflow_name="probe",
            workflow_namespace="default",
        ),
        workflow=SimpleNamespace(model_dump=lambda: {}),
        exec_token=None,
    )

    async def checkpoint(ctx):
        manager.update(ctx, expected_claim_generation=0)

    runner_hooks = hooks(checkpoint)
    runner_hooks.progress = lambda *args: ready.set()
    execution = asyncio.create_task(runner.execute(request, runner_hooks))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=5)
        persisted = manager.get(foreign.execution_id)
        assert persisted.state == ExecutionState.RUNNING
        assert persisted.output is None
        assert foreign.execution_id != request.context.execution_id
    finally:
        if not execution.done():
            execution.cancel()
        if hasattr(runner, "proc"):
            if runner.proc.returncode is None:
                runner.proc.kill()
            await runner.proc.wait()


def test_mutated_usage_cannot_reduce_recorded_spend():
    from flux.tasks.ai.budget import Budget
    from flux.tasks.ai.models import Usage

    budget = Budget(max_tokens=100)
    budget.record(Usage(input_tokens=100))
    malformed = Usage()
    malformed.output_tokens = -100
    with pytest.raises(ValueError):
        budget.record(malformed)
    assert budget.spent() == 100


@pytest.mark.asyncio
async def test_checkpoint_callback_error_reaps_child_and_preserves_error():
    class ControlledRunner(SubprocessRunner):
        async def _spawn(self, request):
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import json,sys,time; r=json.loads(sys.stdin.readline()); "
                "print(json.dumps({'type':'checkpoint','context':r['context']}),flush=True); "
                "time.sleep(30)",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return self.proc

    runner = ControlledRunner()
    request = SimpleNamespace(
        context=ExecutionContext("wf", "default", "wf"),
        workflow=SimpleNamespace(model_dump=lambda: {}),
        exec_token=None,
    )

    async def fail_checkpoint(ctx):
        raise RuntimeError("checkpoint unavailable")

    try:
        with pytest.raises(RuntimeError, match="checkpoint unavailable"):
            await runner.execute(request, hooks(fail_checkpoint))
        assert runner.proc.returncode is not None
    finally:
        if runner.proc.returncode is None:
            runner.proc.kill()
        await runner.proc.wait()


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_child_cleanup():
    ready = asyncio.Event()
    shutdown_started = asyncio.Event()
    kill_started = asyncio.Event()
    allow_kill = asyncio.Event()

    class ControlledRunner(SubprocessRunner):
        async def _spawn(self, request):
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import signal,sys,time; sys.stdin.readline(); "
                "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                'print(\'{"type":"progress","task_id":"t","task_name":"ready","value":1}\',flush=True); '
                "time.sleep(30)",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return self.proc

        async def _shutdown(self, proc, runner_hooks, execution_id):
            shutdown_started.set()
            await super()._shutdown(proc, runner_hooks, execution_id)

        async def _force_kill(self, proc):
            kill_started.set()
            await allow_kill.wait()
            await super()._force_kill(proc)

    runner = ControlledRunner()
    request = SimpleNamespace(
        context=ExecutionContext("wf", "default", "wf"),
        workflow=SimpleNamespace(model_dump=lambda: {}),
        exec_token=None,
    )
    runner_hooks = hooks()
    runner_hooks.progress = lambda *args: ready.set()
    execution = asyncio.create_task(runner.execute(request, runner_hooks))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        execution.cancel()
        await asyncio.wait_for(shutdown_started.wait(), timeout=5)
        execution.cancel()
        await asyncio.wait_for(kill_started.wait(), timeout=5)
        execution.cancel()
        allow_kill.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=5)
        assert runner.proc.returncode is not None
    finally:
        allow_kill.set()
        if not execution.done():
            execution.cancel()
        if runner.proc.returncode is None:
            runner.proc.kill()
        await runner.proc.wait()
