"""sensitive=True (issue #147 phase 2): storage-level redaction of a task's
recorded arguments and output, with replay re-executing the body instead of
returning the marker — plus the secret_requests non-exposure guarantee."""

from __future__ import annotations

import pytest

from flux import ExecutionContext, task as task_decorator, workflow
from flux.domain.events import ExecutionEventType
from flux.task import REDACTED_SENSITIVE


def _events(ctx, event_type, name_part):
    return [e for e in ctx.events if e.type == event_type and name_part in (e.name or "")]


def _stored(task_obj, event):
    return task_obj.output_storage.retrieve(event.value)


class TestStorageRedaction:
    def test_output_stored_as_marker_caller_gets_real_value(self, isolated_db):
        @task_decorator.with_options(sensitive=True)
        async def mint_token() -> str:
            return "sk-live-supersecret"

        @workflow
        async def wf(ctx: ExecutionContext):
            token = await mint_token()
            # The workflow body sees the real value...
            return f"token-length:{len(token)}"

        ctx = wf.run()
        assert ctx.has_succeeded
        assert ctx.output == f"token-length:{len('sk-live-supersecret')}"
        # ...while the event log carries only the marker.
        [completed] = _events(ctx, ExecutionEventType.TASK_COMPLETED, "mint_token")
        assert _stored(mint_token, completed) == REDACTED_SENSITIVE
        assert "sk-live-supersecret" not in str(ctx.to_dict())

    def test_positional_args_recorded_redacted(self, isolated_db):
        @task_decorator.with_options(sensitive=True)
        async def exchange(code: str) -> str:
            return f"token-for-{code}"

        @workflow
        async def wf(ctx: ExecutionContext):
            return await exchange("authcode-123")

        ctx = wf.run()
        assert ctx.has_succeeded
        [started] = _events(ctx, ExecutionEventType.TASK_STARTED, "exchange")
        assert started.value == {"code": REDACTED_SENSITIVE}
        assert "authcode-123" not in str(started.value)

    def test_ordinary_task_recording_unchanged(self, isolated_db):
        @task_decorator
        async def plain(code: str) -> str:
            return f"out-{code}"

        @workflow
        async def wf(ctx: ExecutionContext):
            return await plain("abc")

        ctx = wf.run()
        [started] = _events(ctx, ExecutionEventType.TASK_STARTED, "plain")
        assert started.value == {"code": "abc"}
        [completed] = _events(ctx, ExecutionEventType.TASK_COMPLETED, "plain")
        assert _stored(plain, completed) == "out-abc"

    def test_fallback_output_of_sensitive_task_redacted(self, isolated_db):
        async def substitute() -> str:
            return "sk-fallback-secret"

        @task_decorator.with_options(sensitive=True, fallback=substitute)
        async def flaky_mint() -> str:
            raise ValueError("primary mint broke")

        @workflow
        async def wf(ctx: ExecutionContext):
            return len(await flaky_mint())

        ctx = wf.run()
        assert ctx.has_succeeded
        assert ctx.output == len("sk-fallback-secret")
        [fb] = _events(ctx, ExecutionEventType.TASK_FALLBACK_COMPLETED, "flaky_mint")
        assert _stored(flaky_mint, fb) == REDACTED_SENSITIVE
        [completed] = _events(ctx, ExecutionEventType.TASK_COMPLETED, "flaky_mint")
        assert _stored(flaky_mint, completed) == REDACTED_SENSITIVE


class TestReplaySemantics:
    def test_sensitive_task_reexecutes_on_replay(self, isolated_db):
        from flux.tasks import pause

        runs = {"sensitive": 0, "plain": 0}

        @task_decorator.with_options(sensitive=True)
        async def mint() -> str:
            runs["sensitive"] += 1
            return f"sk-mint-{runs['sensitive']}"

        @task_decorator
        async def plain() -> str:
            runs["plain"] += 1
            return "plain-value"

        @workflow
        async def wf(ctx: ExecutionContext):
            token = await mint()
            fixed = await plain()
            await pause("gate")
            return {"token": token, "fixed": fixed}

        ctx = wf.run()
        assert ctx.is_paused
        assert runs == {"sensitive": 1, "plain": 1}

        resumed = wf.run(execution_id=ctx.execution_id)
        assert resumed.has_succeeded
        # The ordinary task replayed from its stored value; the sensitive one
        # re-executed (the documented trade), producing a fresh credential.
        assert runs == {"sensitive": 2, "plain": 1}
        assert resumed.output["fixed"] == "plain-value"
        assert resumed.output["token"] == "sk-mint-2"

    def test_sensitive_failure_still_replays(self, isolated_db):
        from flux.errors import ExecutionError
        from flux.tasks import pause

        runs = [0]

        @task_decorator.with_options(sensitive=True)
        async def broken_mint() -> str:
            runs[0] += 1
            raise ValueError("mint broke")

        @workflow
        async def wf(ctx: ExecutionContext):
            try:
                await broken_mint()
            except ExecutionError:
                pass
            await pause("gate")
            return "done"

        ctx = wf.run()
        assert ctx.is_paused
        assert runs[0] == 1

        resumed = wf.run(execution_id=ctx.execution_id)
        assert resumed.has_succeeded
        # The stored failure replayed — the body did not run again.
        assert runs[0] == 1


class TestOptionMechanics:
    def test_sensitive_rejects_cache(self):
        with pytest.raises(ValueError, match="cache"):

            @task_decorator.with_options(sensitive=True, cache=True)
            async def bad() -> str:
                return "x"

    def test_with_options_inherits_sensitive(self):
        @task_decorator.with_options(sensitive=True)
        async def mint() -> str:
            return "x"

        assert mint.with_options(name="mint_1").sensitive is True


class TestSecretRequestsGuarantee:
    def test_injected_secrets_never_reach_the_event_log(self, isolated_db):
        """The secret_requests guarantee (issue #147 phase 1 docs, phase 2
        regression test): injected secrets go into separate kwargs after the
        recorded argument dict is built and never land in any event."""
        from flux.secret_managers import SecretManager

        SecretManager.current().save("GUARANTEE_KEY", "sk-injected-value")
        try:

            @task_decorator.with_options(secret_requests=["GUARANTEE_KEY"])
            async def uses_secret(query: str, secrets: dict = {}) -> str:
                assert secrets["GUARANTEE_KEY"] == "sk-injected-value"
                return f"queried-{query}"

            @workflow
            async def wf(ctx: ExecutionContext):
                return await uses_secret("q1")

            ctx = wf.run()
            assert ctx.has_succeeded
            assert ctx.output == "queried-q1"
            # No event payload anywhere contains the injected secret.
            assert "sk-injected-value" not in str(ctx.to_dict())
            [started] = _events(ctx, ExecutionEventType.TASK_STARTED, "uses_secret")
            assert started.value == {"query": "q1"}
        finally:
            SecretManager.current().remove("GUARANTEE_KEY")
