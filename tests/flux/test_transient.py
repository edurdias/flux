"""Tests for transient (non-durable-tasks) executions.

A transient workflow keeps its outer lifecycle — execution row, dispatch,
claim, terminal state — but suppresses all intermediate task-level
checkpoints and never persists TASK_* events.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from flux.domain.execution_context import ExecutionContext
from tests.flux.test_worker_checkpoint import make_ctx, make_worker, ok_response


class TestWorkflowDurabilityOption:
    def test_transient_option_is_stored(self):
        from flux.workflow import workflow

        @workflow.with_options(durability="transient")
        async def wf(ctx):
            return 1

        assert wf.durability == "transient"

    def test_invalid_durability_rejected(self):
        from flux.workflow import workflow

        with pytest.raises(ValueError, match="durability must be"):

            @workflow.with_options(durability="sometimes")
            async def wf(ctx):
                return 1

    def test_transient_with_schedule_rejected(self):
        from flux.domain.schedule import interval
        from flux.workflow import workflow

        with pytest.raises(ValueError, match="schedule"):

            @workflow.with_options(durability="transient", schedule=interval(seconds=60))
            async def wf(ctx):
                return 1


class TestCatalogDurabilityExtraction:
    def test_parse_static_extracts_durability(self):
        from flux.catalogs import DatabaseWorkflowCatalog

        source = b"""
from flux import ExecutionContext
from flux.workflow import workflow


@workflow.with_options(durability="transient")
async def mesh_hop(ctx: ExecutionContext[str]):
    return ctx.input
"""
        # Bypass __init__ (which opens a database) — parse_static only needs
        # the AST helper methods on the instance.
        catalog = DatabaseWorkflowCatalog.__new__(DatabaseWorkflowCatalog)
        workflows = catalog.parse_static(source)
        assert workflows[0].metadata["durability"] == "transient"


class TestTransientCheckpointSuppression:
    @pytest.mark.asyncio
    async def test_only_one_intermediate_checkpoint_persists_running(self):
        """The first intermediate checkpoint goes through (persisting the
        RUNNING transition, filtered to WORKFLOW_* events); every later
        task-level checkpoint stays on the worker."""
        worker = make_worker()
        payloads = []

        async def capture_post(url, **kwargs):
            payloads.append(kwargs["json"])
            return ok_response()

        worker.client.post = capture_post

        def snapshot():
            ctx = make_ctx()
            ctx.is_transient = True
            ctx.is_paused = False
            task_event = MagicMock()
            task_event.type.value = "TASK_COMPLETED"
            ctx.events = [task_event]
            ctx.to_dict.return_value = {
                "execution_id": "exec-1",
                "events": [
                    {"id": "e1", "type": "WORKFLOW_STARTED"},
                    {"id": "e2", "type": "TASK_COMPLETED"},
                ],
            }
            return ctx

        await worker._checkpoint(snapshot())  # first: persists RUNNING
        box = worker._checkpoint_outboxes.get("exec-1")
        while box and box.acked < box.generation:
            await asyncio.sleep(0.005)
        await worker._checkpoint(snapshot())  # second: suppressed
        await worker._checkpoint(snapshot())  # third: suppressed
        await asyncio.sleep(0.02)

        assert len(payloads) == 1
        assert [e["type"] for e in payloads[0]["events"]] == ["WORKFLOW_STARTED"]

    @pytest.mark.asyncio
    async def test_terminal_checkpoint_is_sent_without_task_events(self):
        """The terminal checkpoint persists, carrying only WORKFLOW_* events."""
        worker = make_worker()
        payloads = []

        async def capture_post(url, **kwargs):
            payloads.append(kwargs["json"])
            return ok_response()

        worker.client.post = capture_post

        ctx = make_ctx(finished=True)
        ctx.is_transient = True
        ctx.is_paused = False
        ctx.to_dict.return_value = {
            "execution_id": "exec-1",
            "events": [
                {"id": "e1", "type": "WORKFLOW_STARTED"},
                {"id": "e2", "type": "TASK_STARTED"},
                {"id": "e3", "type": "TASK_COMPLETED"},
                {"id": "e4", "type": "WORKFLOW_COMPLETED"},
            ],
        }
        await worker._checkpoint(ctx)

        assert len(payloads) == 1
        types = [e["type"] for e in payloads[0]["events"]]
        assert types == ["WORKFLOW_STARTED", "WORKFLOW_COMPLETED"]

    @pytest.mark.asyncio
    async def test_transient_pause_converts_to_terminal_failure(self):
        """Pause needs replayable task history; a transient run fails instead."""
        worker = make_worker()
        worker.client.post = AsyncMock(return_value=ok_response())

        ctx = ExecutionContext(
            workflow_id="default/wf",
            workflow_namespace="default",
            workflow_name="wf",
        ).mark_transient()
        ctx.start("w")
        ctx.pause("gate", "gate")

        await worker._checkpoint(ctx)

        assert ctx.has_failed
        worker.client.post.assert_called()

    @pytest.mark.asyncio
    async def test_durable_context_still_checkpoints_intermediates(self):
        """Regression guard: suppression only applies to transient contexts."""
        worker = make_worker()
        worker.client.post = AsyncMock(return_value=ok_response())

        ctx = make_ctx()  # MagicMock: is_transient truthiness must not trigger
        ctx.is_transient = False
        ctx.is_paused = False
        await worker._checkpoint(ctx)
        box = worker._checkpoint_outboxes.get(ctx.execution_id)
        assert box is not None
        while box.acked < box.generation:
            await asyncio.sleep(0.005)
        worker.client.post.assert_called()


class TestApprovalGuard:
    @pytest.mark.asyncio
    async def test_transient_execution_refuses_approval_gate(self):
        from flux.errors import TransientDurabilityError
        from flux.task import task

        @task.with_options(requires_approval=True)
        async def gated():
            return 1

        ctx = ExecutionContext(
            workflow_id="default/wf",
            workflow_namespace="default",
            workflow_name="wf",
        ).mark_transient()

        with pytest.raises(TransientDurabilityError, match="requires_approval"):
            await gated._approval_gate(ctx, "call-1", "gated", (), {})


class TestDispatchPayloadFlag:
    def test_payload_flags_transient_workflows(self):
        from flux.server import Server

        server = Server(host="localhost", port=8000)
        with (
            pytest.MonkeyPatch.context() as mp,
        ):
            workflow_info = MagicMock()
            workflow_info.source = b"src"
            workflow_info.metadata = {"durability": "transient"}
            catalog = MagicMock()
            catalog.get.return_value = workflow_info
            mp.setattr("flux.api.worker_routes.WorkflowCatalog", MagicMock(create=lambda: catalog))
            session = MagicMock()
            session.get.return_value = None
            mp.setattr(server, "_get_db_session", lambda: session)

            ctx = MagicMock()
            ctx.workflow_namespace = "default"
            ctx.workflow_name = "wf"
            ctx.execution_id = "e1"
            payload = server._build_dispatch_payload(ctx)

        assert payload.get("transient") is True
