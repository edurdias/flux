from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal

from flux.task import task

logger = logging.getLogger("flux.agent.plan")

_STEP_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9_-]*[a-z0-9])?$")


class PlanValidationError(ValueError):
    """Raised at plan creation time for invalid step configuration."""

    pass


@dataclass
class AgentStep:
    """A single step in an agent plan."""

    name: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    result: Any | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["depends_on"]:
            del d["depends_on"]
        if d["result"] is None:
            del d["result"]
        if d["error"] is None:
            del d["error"]
        return d


@dataclass
class AgentPlan:
    """An agent's active plan — closure-scoped singleton."""

    steps: list[AgentStep] = field(default_factory=list)

    def get_step(self, name: str) -> AgentStep | None:
        return next((s for s in self.steps if s.name == name), None)

    def completed_steps(self) -> list[AgentStep]:
        return [s for s in self.steps if s.status == "completed"]

    def pending_steps(self) -> list[AgentStep]:
        return [s for s in self.steps if s.status == "pending"]

    def in_progress_steps(self) -> list[AgentStep]:
        return [s for s in self.steps if s.status == "in_progress"]

    def failed_steps(self) -> list[AgentStep]:
        return [s for s in self.steps if s.status == "failed"]

    def active_step(self) -> AgentStep | None:
        in_progress = self.in_progress_steps()
        return in_progress[0] if in_progress else None

    def ready_steps(self) -> list[AgentStep]:
        return [s for s in self.pending_steps() if self.dependencies_satisfied(s)]

    def dependencies_satisfied(self, step: AgentStep) -> bool:
        for dep_name in step.depends_on:
            dep = self.get_step(dep_name)
            if dep is None or dep.status != "completed":
                return False
        return True

    def dependency_results(self, step: AgentStep) -> dict[str, Any]:
        results = {}
        for dep_name in step.depends_on:
            dep = self.get_step(dep_name)
            if dep and dep.result is not None:
                results[dep_name] = dep.result
        return results

    def summary(self) -> str:
        completed = len(self.completed_steps())
        total = len(self.steps)
        failed = self.failed_steps()
        active = self.active_step()
        ready = self.ready_steps()

        parts = [f"[Plan: {completed}/{total} done"]

        if failed:
            failed_names = ", ".join(f'"{s.name}"' for s in failed)
            parts.append(f", {len(failed)} failed ({failed_names})")

        parts.append(".")

        if active:
            parts.append(f' Active: "{active.name}".')

        if ready:
            ready_parts = []
            for step in ready[:3]:
                dep_results = self.dependency_results(step)
                if dep_results:
                    deps_str = "; ".join(f"{k}: {str(v)[:200]}" for k, v in dep_results.items())
                    ready_parts.append(f'"{step.name}" (from {deps_str})')
                else:
                    ready_parts.append(f'"{step.name}"')
            parts.append(f" Ready: {', '.join(ready_parts)}.")

        if not active and not ready:
            parts.append(" No steps ready.")

        parts.append("]")
        return "".join(parts)

    def to_dict(self) -> dict:
        return {"steps": [s.to_dict() for s in self.steps]}


def _validate_step_name(name: str) -> None:
    if not name:
        raise PlanValidationError("Step name must not be empty.")
    if len(name) > 64:
        raise PlanValidationError(f"Step name '{name}' exceeds 64 characters.")
    if "--" in name or "__" in name:
        raise PlanValidationError(
            f"Step name '{name}' must not contain consecutive hyphens or underscores.",
        )
    if not _STEP_NAME_PATTERN.match(name):
        raise PlanValidationError(
            f"Step name '{name}' is invalid. Use lowercase letters, numbers, "
            f"hyphens, and underscores. Must not start or end with a hyphen or underscore.",
        )


def _validate_dependencies(plan: AgentPlan) -> None:
    names = set()
    for step in plan.steps:
        if step.name in names:
            raise PlanValidationError(f"Duplicate step name: '{step.name}'.")
        names.add(step.name)
    for step in plan.steps:
        for dep in step.depends_on:
            if dep not in names:
                raise PlanValidationError(
                    f"Step '{step.name}' depends on '{dep}' which is not in the plan.",
                )

    visited: set[str] = set()
    in_stack: set[str] = set()
    step_map = {s.name: s for s in plan.steps}

    def _visit(name: str) -> None:
        if name in in_stack:
            raise PlanValidationError(
                f"Circular dependency detected involving '{name}'.",
            )
        if name in visited:
            return
        in_stack.add(name)
        for dep in step_map[name].depends_on:
            _visit(dep)
        in_stack.remove(name)
        visited.add(name)

    for step in plan.steps:
        _visit(step.name)


class PlanContext:
    """Shared mutable state for plan tools and summary injection."""

    def __init__(self):
        self.plan: AgentPlan | None = None

    def summary(self) -> str | None:
        if self.plan is None:
            return None
        return self.plan.summary()


def build_plan_tools() -> tuple[list[task], Callable[[], str | None]]:
    """Build planning tools and a summary function.

    Returns:
        A tuple of (tools, summary_fn). The tools and summary_fn share
        the same PlanContext via closure.
    """
    ctx = PlanContext()

    @task
    async def create_plan(steps: str) -> dict:
        """Create or replace the agent's plan.

        steps is a JSON array of step objects. Each step has:
          - name: unique identifier (lowercase, hyphens allowed)
          - description: what to accomplish (natural language)
          - depends_on: list of step names that must complete first (optional)

        Example:
          [{"name": "research", "description": "Search for data."},
           {"name": "analyze", "description": "Analyze the data.", "depends_on": ["research"]}]

        When replacing an existing plan, completed steps and their results
        are preserved.
        """
        parsed = json.loads(steps) if isinstance(steps, str) else steps
        new_steps = []
        for s in parsed:
            _validate_step_name(s["name"])
            new_steps.append(
                AgentStep(
                    name=s["name"],
                    description=s.get("description", ""),
                    depends_on=s.get("depends_on", []),
                ),
            )

        new_plan = AgentPlan(steps=new_steps)
        _validate_dependencies(new_plan)

        if ctx.plan:
            for completed in ctx.plan.completed_steps():
                existing = new_plan.get_step(completed.name)
                if existing:
                    idx = new_plan.steps.index(existing)
                    new_plan.steps[idx] = completed

        ctx.plan = new_plan
        return ctx.plan.to_dict()

    @task
    async def mark_step_done(step_name: str, result: str) -> dict:
        """Mark a plan step as completed and store its result.

        The result is stored so that dependent steps can access it
        via get_plan. Use descriptive results — other steps will
        receive this as context.

        Args:
            step_name: The step name to mark as completed.
            result: The result or summary of what was accomplished.
        """
        if ctx.plan is None:
            return {"error": "No plan exists. Call create_plan first."}

        step = ctx.plan.get_step(step_name)
        if step is None:
            available = ", ".join(s.name for s in ctx.plan.steps)
            return {"error": f"Step '{step_name}' not found. Available: {available}"}

        if step.status == "completed":
            return step.to_dict()

        step.status = "completed"
        step.result = result
        return step.to_dict()

    @task
    async def get_plan() -> dict:
        """Return the current plan with all steps, statuses, and results.

        Use this to review progress, check dependency results before
        starting a new step, or decide whether to replan.
        """
        if ctx.plan is None:
            return {"message": "No plan exists."}
        return ctx.plan.to_dict()

    return [create_plan, mark_step_done, get_plan], ctx.summary


def build_plan_preamble() -> str:
    """Build system prompt section for agent planning."""
    return """

You have planning capabilities. Use them when a task requires multiple
coordinated steps that benefit from thinking ahead.

When to create a plan:
- The task involves 3 or more distinct steps
- Steps have dependencies (one step's output feeds into another)
- The task benefits from organizing work before starting
- You want to ensure completeness in a complex workflow

When NOT to create a plan:
- Simple tasks you can accomplish in 1-2 tool calls
- Exploratory tasks where the next step depends entirely on discovery
- When you are unsure what steps are needed (gather information first)

Creating a plan:
- Call create_plan with a list of steps. Each step has a name, description,
  and optional depends_on list.
- Steps are goals, not single tool calls. "Research competitor pricing" is
  a good step. "Call search_web" is too granular.
- Use depends_on to declare data dependencies between steps. Only add
  dependencies where a step genuinely needs another step's result.
- Keep plans concise. Prefer fewer meaningful steps over many granular ones.

Working through a plan:
- Work on steps using your available tools, skills, and agents as normal.
- When you finish a step, call mark_step_done with step_name and a
  descriptive result. Other steps that depend on it will see this result.
- Call get_plan to review progress and access results from completed steps.
- Respect dependency order — complete dependencies before starting a step
  that depends on them.
- A status reminder appears after tool calls to help you track progress.

Replanning:
- If circumstances change or you learn new information, call create_plan
  again with an updated plan. Completed steps are preserved automatically.
- You do not need to complete every step. If the plan is no longer relevant,
  stop calling plan tools and respond directly.
"""
