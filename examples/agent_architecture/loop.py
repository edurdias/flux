"""Loop engineering — the repeated work-and-feedback cycle.

The core rule of loop engineering: **loop on evidence, not confidence**.
"The generator says it is done" is not a stopping condition — a
verification step must produce measurable evidence, the loop must be
bounded, and exhausting the bound must land on a *named escalation path*
instead of silently giving up.

This example implements that shape with a simulated document generator:

1. ``draft_document``   — produces a first draft with some unresolved
                          checklist items (stand-in for an LLM call).
2. ``verify_document``  — deterministic verifier; returns the list of
                          failing checks. Empty list == evidence of done.
3. ``revise_document``  — feeds the evidence back; resolves one reported
                          defect per revision (incremental convergence).
4. Bounded attempts     — after ``max_attempts`` the loop stops and
                          escalates to a human via ``pause(...)``; the
                          resume input is the human's resolution, which is
                          *also* verified before the run may succeed.

Because every iteration is a checkpointed ``@task`` call, the loop
survives a crash mid-iteration and resumes exactly where it stopped —
including across the human escalation, which may happen days later.

For the same loop driven by a real LLM (with ``Budget`` as the bound and
``max_tool_calls`` capping tool iterations), see
``full_stack_agent_ollama.py``.

Usage:
    poetry run python examples/agent_architecture/loop.py
    # If it escalates:
    flux workflow resume evidence_loop <execution_id> '{"resolved": ["..."]}'
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks import pause

CHECKLIST = (
    "has_title",
    "has_summary",
    "cites_sources",
    "consistent_terminology",
    "within_length_limit",
    "passes_linter",
)


@task
async def draft_document(spec: str, defect_count: int) -> dict[str, Any]:
    """Simulated generator: the first draft ships with ``defect_count`` defects."""
    return {
        "spec": spec,
        "body": f"Draft for: {spec}",
        "unresolved": list(CHECKLIST[:defect_count]),
    }


@task
async def verify_document(document: dict[str, Any]) -> list[str]:
    """Deterministic verifier: returns the failing checks — the loop's evidence."""
    return list(document["unresolved"])


@task
async def revise_document(document: dict[str, Any], evidence: list[str]) -> dict[str, Any]:
    """Simulated reviser: fixes the first reported defect, leaves the rest."""
    remaining = [check for check in document["unresolved"] if check != evidence[0]]
    return {**document, "unresolved": remaining}


@task
async def apply_human_resolution(
    document: dict[str, Any],
    resolution: Any,
) -> dict[str, Any]:
    """Apply the checks a human marked as resolved during escalation.

    The resume payload is untrusted operator input, so anything that is
    not a well-formed resolution counts as resolving nothing — the
    verifier then rejects the run on evidence instead of crashing on a
    malformed payload.
    """
    resolved = resolution.get("resolved", []) if isinstance(resolution, dict) else []
    remaining = [check for check in document["unresolved"] if check not in resolved]
    return {**document, "unresolved": remaining}


@workflow
async def evidence_loop(ctx: ExecutionContext[dict]):
    params = ctx.input or {}
    spec = params.get("spec", "quarterly engineering report")
    defect_count = int(params.get("defect_count", 2))
    max_attempts = int(params.get("max_attempts", 3))

    document = await draft_document(spec, defect_count)

    evidence: list[str] = []
    for attempt in range(1, max_attempts + 1):
        evidence = await verify_document(document)
        if not evidence:
            return {
                "status": "verified",
                "attempts": attempt,
                "escalated": False,
                "document": document,
            }
        if attempt < max_attempts:
            document = await revise_document(document, evidence)

    # Bounded loop exhausted — named escalation path, not a silent failure.
    # The execution pauses durably; a human resumes it with their resolution.
    resolution = await pause(
        "verification_budget_exhausted",
        output={"outstanding": evidence, "attempts": max_attempts},
    )

    document = await apply_human_resolution(document, resolution)
    evidence = await verify_document(document)
    if evidence:
        return {
            "status": "rejected",
            "attempts": max_attempts,
            "escalated": True,
            "outstanding": evidence,
        }
    return {
        "status": "verified",
        "attempts": max_attempts,
        "escalated": True,
        "document": document,
    }


if __name__ == "__main__":  # pragma: no cover
    # Converges within budget: 2 defects, one fixed per revision, 3 attempts.
    ctx = evidence_loop.run({"defect_count": 2, "max_attempts": 3})
    print(f"within budget: {ctx.output}")

    # Exhausts the budget and escalates to a human.
    ctx = evidence_loop.run({"defect_count": 6, "max_attempts": 3})
    if ctx.is_paused:
        print(f"escalated: execution_id={ctx.execution_id}")
        resumed = evidence_loop.resume(ctx.execution_id, {"resolved": list(CHECKLIST)})
        print(f"after human resolution: {resumed.output}")
