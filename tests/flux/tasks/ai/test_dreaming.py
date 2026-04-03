from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider


class TestDreamFactory:
    def test_dream_returns_callable(self):
        from flux.tasks.ai.dreaming import dream
        from flux.tasks.ai.memory.long_term_memory import LongTermMemory

        provider = InMemoryProvider()
        memory = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
        hook = dream(memory, execution_id="exec_123")
        assert callable(hook)

    @pytest.mark.asyncio
    async def test_dream_submits_async_workflow(self):
        from flux.tasks.ai.dreaming import dream
        from flux.tasks.ai.memory.long_term_memory import LongTermMemory

        provider = InMemoryProvider()
        memory = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
        hook = dream(memory, execution_id="exec_123")

        with patch("flux.tasks.ai.dreaming.call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "dream_exec_456"
            await hook("my_agent", "some result")

            mock_call.assert_called_once_with(
                "agent_dream",
                {
                    "execution_id": "exec_123",
                    "agent": "my_agent",
                    "scope": "user:1",
                },
                mode="async",
            )

    @pytest.mark.asyncio
    async def test_dream_custom_workflow_name(self):
        from flux.tasks.ai.dreaming import dream
        from flux.tasks.ai.memory.long_term_memory import LongTermMemory

        provider = InMemoryProvider()
        memory = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
        hook = dream(memory, execution_id="exec_123", workflow="custom_dream")

        with patch("flux.tasks.ai.dreaming.call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "dream_exec_789"
            await hook("my_agent", "result")

            assert mock_call.call_args[0][0] == "custom_dream"

    @pytest.mark.asyncio
    async def test_dream_failure_is_logged_not_raised(self):
        from flux.tasks.ai.dreaming import dream
        from flux.tasks.ai.memory.long_term_memory import LongTermMemory

        provider = InMemoryProvider()
        memory = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
        hook = dream(memory, execution_id="exec_123")

        with patch("flux.tasks.ai.dreaming.call", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = RuntimeError("connection failed")
            await hook("my_agent", "result")


class TestFailureGate:
    @pytest.mark.asyncio
    async def test_skips_when_max_failures_reached(self):
        from flux.tasks.ai.dreaming import check_failure_gate
        from flux.tasks.ai.memory.long_term_memory import LongTermMemory

        provider = InMemoryProvider()
        memory = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
        await memory.memorize("_dream:failures", 3)

        result = await check_failure_gate(memory, max_failures=3)
        assert result is False

    @pytest.mark.asyncio
    async def test_passes_when_below_max_failures(self):
        from flux.tasks.ai.dreaming import check_failure_gate
        from flux.tasks.ai.memory.long_term_memory import LongTermMemory

        provider = InMemoryProvider()
        memory = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
        await memory.memorize("_dream:failures", 2)

        result = await check_failure_gate(memory, max_failures=3)
        assert result is True

    @pytest.mark.asyncio
    async def test_passes_when_no_failure_key(self):
        from flux.tasks.ai.dreaming import check_failure_gate
        from flux.tasks.ai.memory.long_term_memory import LongTermMemory

        provider = InMemoryProvider()
        memory = LongTermMemory(provider=provider, agent="assistant", scope="user:1")

        result = await check_failure_gate(memory, max_failures=3)
        assert result is True


class TestFailureTracking:
    @pytest.mark.asyncio
    async def test_increment_failure_counter(self):
        from flux.tasks.ai.dreaming import increment_failure_counter
        from flux.tasks.ai.memory.long_term_memory import LongTermMemory

        provider = InMemoryProvider()
        memory = LongTermMemory(provider=provider, agent="assistant", scope="user:1")

        await increment_failure_counter(memory)
        assert await memory.recall("_dream:failures") == 1

        await increment_failure_counter(memory)
        assert await memory.recall("_dream:failures") == 2

    @pytest.mark.asyncio
    async def test_reset_failure_counter(self):
        from flux.tasks.ai.dreaming import reset_failure_counter
        from flux.tasks.ai.memory.long_term_memory import LongTermMemory

        provider = InMemoryProvider()
        memory = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
        await memory.memorize("_dream:failures", 5)

        await reset_failure_counter(memory)
        assert await memory.recall("_dream:failures") == 0


class TestDreamPrompts:
    def test_orient_prompt_mentions_memory_keys(self):
        from flux.tasks.ai.dreaming import ORIENT_PROMPT

        assert "list_memory_keys" in ORIENT_PROMPT
        assert "recall_memory" in ORIENT_PROMPT

    def test_gather_signal_prompt_mentions_corrections(self):
        from flux.tasks.ai.dreaming import GATHER_SIGNAL_PROMPT

        assert "correction" in GATHER_SIGNAL_PROMPT.lower()
        assert "decision" in GATHER_SIGNAL_PROMPT.lower()

    def test_consolidate_prompt_mentions_merge(self):
        from flux.tasks.ai.dreaming import CONSOLIDATE_PROMPT

        assert "merge" in CONSOLIDATE_PROMPT.lower()
        assert "contradict" in CONSOLIDATE_PROMPT.lower()
        assert "provenance" in CONSOLIDATE_PROMPT.lower()

    def test_prune_prompt_mentions_cap(self):
        from flux.tasks.ai.dreaming import PRUNE_PROMPT

        assert "100" in PRUNE_PROMPT
        assert "stale" in PRUNE_PROMPT.lower()
