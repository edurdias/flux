# Spec: agent vocabulary layer — structured output, staged pipeline, budget

**Date:** 2026-07-15 · **Status:** draft for review (PR 3 of the
dynamic-workflows series) · **Depends on:** nothing at runtime — composes
with #130/#131 but each primitive stands alone.

## Motivation

PR 1 gave model-authored code a safe place to run; PR 2 gave agents a way to
author workflows. This PR makes what they author (and what agent loops do in
general) *expressive and bounded*: subagent calls that return validated
structured data instead of prose to re-parse, a fan-out combinator that
streams items through stages without artificial barriers, and a spend
ceiling that turns "the agent looped and burned tokens all night" into a
structured, catchable failure.

Each primitive replicates a proven shape from orchestration harnesses
(schema-forced subagent output, per-item pipelines, shared token budgets)
on Flux's own machinery.

## 1. Structured output that survives tools

### Today

`agent(response_format=SomeModel)` exists and uses native provider
enforcement — but only on the **tool-less** path. Every structured-output
branch in the loop is guarded by `response_format and not tools`, so an
agent that has tools silently returns prose even when a schema was
requested. There is also no validation-retry: a malformed response raises
`ValidationError` and fails the task outright.

### Design

- **Tools + schema compose.** When `response_format` is set and the agent
  has tools, the tool loop runs as usual; when the model produces its final
  (non-tool-call) answer, the loop validates it against the schema. If the
  final turn used native structured output the parse is a formality;
  otherwise the schema is appended to the system prompt as today.
- **Validation retry.** On `ValidationError`, feed the error back once —
  one additional turn with the validation message and the schema
  (`max_schema_retries`, default 1, config-able per call) — then fail with
  a structured error naming the field-level problems. Mirrors how
  harness-side structured output behaves: the model corrects itself on
  mismatch instead of the caller eating a parse failure.
- **Delegation carries contracts.** `workflow_agent(...)` and `delegate`
  sub-agents accept `response_format` and surface the validated model to
  the parent — the parent agent (or authored workflow) receives data, not
  prose. `DelegationResult.value` is the validated instance when a format
  was declared.
- **Replay:** validated models already round-trip through task events
  (pickled outputs); no new event types.

### Out of scope

Streaming structured output (stream stays auto-disabled when a format is
set, as today).

## 2. `pipeline_map` — per-item staged fan-out

### Today

`parallel(*coros)` is a gather (barrier at the end); `pipeline(*tasks,
input)` threads ONE value through a chain. There is no way to run N items
through M stages where item A can be in stage 3 while item B is still in
stage 1 — the natural shape for "summarize every document, then judge every
summary".

### Design

```python
results = await pipeline_map(
    items,
    fetch,        # stage 1: receives (item)
    summarize,    # stage 2+: receives (previous_result, item, index)
    judge,
    max_concurrent=8,
    on_error="none",   # "none" (default) | "raise"
)
```

- Each item flows through all stages **independently** — no barrier between
  stages. Wall-clock is the slowest single-item chain, not the sum of the
  slowest stage per phase.
- Stage 1 receives the item; later stages receive
  `(previous_result, item, index)` so context need not be threaded through
  return values. Callables with a single parameter are called with just the
  previous result (inspected once, at submission).
- **Failure policy:** `on_error="none"` (default) — a stage exception drops
  that item's result to `None` and skips its remaining stages, so one bad
  item never kills the batch; the exception is recorded as a task event as
  usual. `on_error="raise"` propagates the first failure.
- `max_concurrent` caps in-flight items (semaphore); default unlimited.
- Results return in input order, `None` for dropped items.
- Composition with replay is free: stages are ordinary task calls, each with
  per-call occurrence identity, so a resumed pipeline replays completed
  stage calls and re-runs only what never finished.

Ships in `flux/tasks/builtins.py` next to `parallel`/`pipeline`; exported
from `flux.tasks`.

## 3. Budget — a spend ceiling for LLM work

### Today

No provider reports usage anywhere in the AI stack: `formatter.py` has no
usage hook, the agent loop counts tool *calls* but not tokens. An agent loop
(or an authored workflow full of `agent()` calls) has no way to bound its
own spend.

### Design

**Usage plumbing (the bulk of the work).** The `LLMFormatter` ABC gains
`extract_usage(response) -> Usage | None` where
`Usage(input_tokens, output_tokens)`; each provider module implements it
from its native response shape (all four expose token counts). The agent
loop records usage after every LLM call — including streamed calls, whose
final usage arrives on the terminal chunk for every supported provider —
and attaches it to the agent task's progress/telemetry.

**The primitive.**

```python
budget = Budget(max_tokens=200_000)

result = await agent("...", model=..., budget=budget)(task_input)
# later, in workflow code:
if budget.remaining() < 20_000:
    return partial_result
```

- `Budget` is a plain object: `spent()`, `remaining()`, `max_tokens`
  (`None` = tracking only, no ceiling). Passed explicitly to `agent()` /
  `workflow_agent()` / `delegate` — one budget instance can be shared across
  every agent call in a workflow, which is the point.
- **Enforcement:** checked *before* each LLM call; when `spent() >=
  max_tokens` the call raises `BudgetExceededError` (an `ExecutionError`
  subclass) — catchable in workflow code, mapped through retry/fallback like
  any task error. A call in flight is never killed mid-stream; the ceiling
  is a pre-flight gate, so overshoot is bounded by one call's output.
- **Scope & replay semantics (stated honestly):** a `Budget` bounds spend
  within one *run attempt*. On resume, replayed agent calls short-circuit
  from the event log without re-spending, and the in-memory counter starts
  fresh — so the ceiling applies to *new* spend after resume. Durable
  cumulative accounting across attempts (usage as persisted events) is
  future work, noted in the doc.

## Config

No new config section. `max_schema_retries` is a per-call `agent()`
parameter (default 1). Budget is explicit-object-only — no ambient global.

## Testing

- **Structured output:** schema + tools returns validated model (mock
  provider); malformed-then-corrected retry path; retry exhaustion fails
  with field-level detail; delegation surfaces validated instances;
  tool-less path unchanged (existing tests keep passing).
- **pipeline_map:** ordering, no-barrier interleaving (stage timestamps),
  single-param stage calling convention, error-drop vs error-raise,
  max_concurrent cap, replay-resume mid-pipeline (pause between stages,
  resume, completed stage calls not re-run).
- **Budget:** per-provider `extract_usage` (fixture responses); accumulation
  across calls sharing one budget; pre-flight ceiling raises
  `BudgetExceededError`; `None` ceiling tracks without enforcement;
  streamed-call usage capture.

## Rollout / compatibility

Additive: new combinator, new optional parameters, a new formatter method
with a `None`-returning default (existing custom formatters keep working;
budget enforcement simply sees no usage from them and only tracking-only
budgets are useful until they implement it). Version: 0.59.0.
