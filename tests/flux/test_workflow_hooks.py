"""Unit tests for workflow.with_options(hooks=...) — structural validation
and the .hooks property. Registration-time behavior (AST extraction, scope
confinement, permission checks) is covered in test_catalogs.py-adjacent
tests and tests/flux/hooks/test_owned_reconciliation.py."""

from __future__ import annotations

import pytest

from flux.hooks import hook
from flux.workflow import workflow


class TestWorkflowHooksOption:
    def test_hooks_defaults_to_none(self):
        @workflow
        async def plain(ctx):
            return ctx.input

        assert plain.hooks is None

    def test_hooks_are_stored_and_exposed(self):
        spec = hook.run(
            on="execution:default:with_hooks:failed",
            workflow="ops/notify",
            principal="notifier",
        )

        @workflow.with_options(hooks=[spec])
        async def with_hooks(ctx):
            return ctx.input

        assert with_hooks.hooks == [spec]

    def test_hooks_must_be_a_list(self):
        with pytest.raises(ValueError, match="hooks"):

            @workflow.with_options(hooks={"on": "execution:*"})  # type: ignore[arg-type]
            async def bad(ctx):
                return ctx.input

    def test_hooks_entries_must_be_hook_run_shaped_dicts(self):
        with pytest.raises(ValueError, match="hooks"):

            @workflow.with_options(hooks=[{"not": "a hook spec"}])
            async def bad(ctx):
                return ctx.input
