"""Agent-owned hooks carry a runtime owner filter (declaration path 3):
every agent session runs agents/agent_chat, so selector text alone cannot
confine a hook to one agent's own sessions."""

from __future__ import annotations

from flux.domain.execution_context import ExecutionContext
from flux.hooks.registry import HookRegistry
from flux.hooks.selectors import HookEvent, events_from_save


def _ctx(namespace: str, name: str, input: object) -> ExecutionContext:
    return ExecutionContext(
        workflow_id=f"{namespace}/{name}",
        workflow_namespace=namespace,
        workflow_name=name,
        input=input,
        execution_id="exec-1",
    )


class TestEventsFromSaveCarriesTheAgentName:
    def test_an_agents_namespace_execution_carries_its_agent(self):
        from flux.domain.events import ExecutionEvent, ExecutionEventType

        ctx = _ctx("agents", "agent_chat", {"agent": "helper", "message": "hi"})
        event = ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_STARTED,
            source_id="s",
            name="agent_chat",
        )

        [derived] = events_from_save(ctx, [event])

        assert derived.agent == "helper"

    def test_a_non_agents_namespace_execution_carries_no_agent(self):
        from flux.domain.events import ExecutionEvent, ExecutionEventType

        ctx = _ctx("release", "pipeline", {"foo": "bar"})
        event = ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_STARTED,
            source_id="s",
            name="pipeline",
        )

        [derived] = events_from_save(ctx, [event])

        assert derived.agent is None

    def test_an_agents_namespace_execution_with_a_non_dict_input_carries_no_agent(self):
        from flux.domain.events import ExecutionEvent, ExecutionEventType

        ctx = _ctx("agents", "agent_chat", "not-a-dict")
        event = ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_STARTED,
            source_id="s",
            name="agent_chat",
        )

        [derived] = events_from_save(ctx, [event])

        assert derived.agent is None


def _event(key: str, *, agent: str | None = None) -> HookEvent:
    domain = key.split(":", 1)[0]
    return HookEvent(
        domain=domain,
        key=key,
        execution_id="exec-1",
        workflow_namespace="agents",
        workflow_name="agent_chat",
        event_id="ev-1",
        type=key.rsplit(":", 1)[-1],
        task_name=None,
        task_call_id=None,
        value=None,
        occurred_at="2024-01-01T00:00:00+00:00",
        agent=agent,
    )


class TestOwnerScopedMatching:
    def test_an_agent_owned_hook_only_matches_its_own_agents_events(self, isolated_db):
        registry = HookRegistry.create()
        registry.reconcile_owned_hooks(
            owner_type="agent",
            owner_ref="helper",
            specs=[
                {
                    "on": "execution:agents:agent_chat:completed",
                    "workflow": "ops/notify",
                    "principal": "p",
                    "name": None,
                    "max_attempts": 5,
                },
            ],
        )

        own_event = _event("execution:agents:agent_chat:completed", agent="helper")
        other_event = _event("execution:agents:agent_chat:completed", agent="other-agent")
        no_agent_event = _event("execution:agents:agent_chat:completed", agent=None)

        assert len(registry.matches(own_event)) == 1
        assert registry.matches(other_event) == []
        assert registry.matches(no_agent_event) == []

    def test_a_workflow_owned_hook_ignores_the_owner_filter(self, isolated_db):
        """Only owner_type='agent' rows carry the runtime filter -- a
        workflow-owned hook's static scope confinement is sufficient on its
        own, per the spec's 'Scope confinement differs' distinction."""
        registry = HookRegistry.create()
        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                {
                    "on": "execution:release:pipeline:failed",
                    "workflow": "ops/notify",
                    "principal": "p",
                    "name": None,
                    "max_attempts": 5,
                },
            ],
        )

        event = HookEvent(
            domain="execution",
            key="execution:release:pipeline:failed",
            execution_id="exec-2",
            workflow_namespace="release",
            workflow_name="pipeline",
            event_id="ev-2",
            type="failed",
            task_name=None,
            task_call_id=None,
            value=None,
            occurred_at="2024-01-01T00:00:00+00:00",
            agent=None,
        )

        assert len(registry.matches(event)) == 1
