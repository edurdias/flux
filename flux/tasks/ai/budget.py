from __future__ import annotations

from flux.errors import BudgetExceededError
from flux.tasks.ai.models import Usage


class Budget:
    """A shared token-spend ceiling for LLM work.

    Create one instance and pass it to every ``agent()`` call whose spend
    should count against the same ceiling. ``max_tokens=None`` tracks usage
    without enforcing anything.

    Enforcement is a pre-flight gate: the agent loop calls :meth:`check`
    before each LLM call and raises :class:`BudgetExceededError` once
    ``spent() >= max_tokens``. A call already in flight is never interrupted,
    so overshoot is bounded by one call's usage.

    Scope: a budget bounds spend within one run attempt. On resume, the
    agent loop's inner LLM task calls replay from the event log — their
    recorded usage is re-counted into the (fresh) budget in the same order,
    so accounting stays consistent for a resumed attempt without re-spending
    real tokens. Sharing one budget across *concurrently running* agents
    makes the exact enforcement point nondeterministic across replays;
    durable cumulative accounting is future work.
    """

    def __init__(self, max_tokens: int | None = None) -> None:
        if max_tokens is not None and max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {max_tokens}")
        self.max_tokens = max_tokens
        self._input_tokens = 0
        self._output_tokens = 0

    def record(self, usage: Usage | None) -> None:
        """Add one LLM call's usage. None (provider reported nothing) is a no-op."""
        if usage is None:
            return
        self._input_tokens += usage.input_tokens
        self._output_tokens += usage.output_tokens

    def spent(self) -> int:
        """Total tokens (input + output) recorded so far."""
        return self._input_tokens + self._output_tokens

    def remaining(self) -> int | None:
        """Tokens left under the ceiling (floored at 0); None when no ceiling is set."""
        if self.max_tokens is None:
            return None
        return max(0, self.max_tokens - self.spent())

    def check(self) -> None:
        """Raise BudgetExceededError if the ceiling has been reached."""
        if self.max_tokens is not None and self.spent() >= self.max_tokens:
            raise BudgetExceededError(spent_tokens=self.spent(), max_tokens=self.max_tokens)

    def __repr__(self) -> str:
        ceiling = self.max_tokens if self.max_tokens is not None else "unlimited"
        return f"Budget(spent={self.spent()}, max_tokens={ceiling})"
