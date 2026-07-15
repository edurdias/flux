# Spec: agent vocabulary layer — structured output, bounded fan-out, budget

**Date:** 2026-07-15 · **Status:** draft for review (PR 3 of the
dynamic-workflows series) · **Depends on:** nothing at runtime — composes
with #130/#131 but each primitive stands alone.

## Motivation

PR 1 gave model-authored code a safe place to run; PR 2 gave agents a way to
author workflows. This PR makes what they author (and what agent loops do in
general) *expressive and bounded*: subagent calls that return validated
structured data instead of prose to re-parse, fan-out that survives one bad
item and caps its own concurrency, and a spend ceiling that turns "the agent
looped and burned tokens all night" into a structured, catchable failure.

Each primitive replicates a proven shape from orchestration harnesses
(schema-forced subagent output, bounded map over items, shared token
budgets) on Flux's own machinery.

## 1. Structured output that survives tools

### Today

`agent(response_format=SomeModel)` exists and uses native provider
enforcement — but only on the **tool-less** path. The schema-communication
branches in the loop are guarded by `response_format and not tools`, while
the final `model_validate_json` runs unconditionally — so an agent with
tools is validated against a schema the model was never shown, and almost
always fails with a raw `ValidationError` on prose. There is also no
validation-retry: a malformed response fails the task outright.

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
- **Delegation carries contracts.** A sub-agent built with
  `response_format` returns a validated model; `delegate` surfaces it to
  the parent as plain data (`model_dump()` in `DelegationResult.output`) so
  the parent LLM sees clean JSON. `workflow_agent(...,
  response_format=...)` validates a completed workflow's output against the
  model — a contract violation turns the delegation into a `failed` status
  instead of handing the parent unchecked data. Direct callers (workflow
  code awaiting an agent task) receive the validated instance itself.
- **Replay:** validated models already round-trip through task events
  (pickled outputs); no new event types.

### Out of scope

Streaming structured output (stream stays auto-disabled when a format is
set, as today).

## 2. `parallel` — bounded, partial-failure-tolerant fan-out

### Today

`parallel(*coros)` is a bare `asyncio.gather`. Two gaps for agent-scale
fan-out:

1. **No concurrency cap.** 200 items where each is an `agent()` call means
   200 concurrent LLM requests — there is no primitive to bound in-flight
   work.
2. **All-or-nothing failure.** `gather` without `return_exceptions`
   propagates the first exception, and since `parallel` is itself a
   `@task`, one bad item fails the whole combinator — retry then re-runs
   the entire batch, including everything that already succeeded.

Notably, *staged* per-item flow ("summarize every document, then judge
every summary" with no barrier between stages) needs **no new combinator**:
chain the stages in a plain async function and fan it out —

```python
async def process(doc):
    fetched = await fetch(doc)
    summary = await summarize(fetched)
    return await judge(summary)

results = await parallel(*[process(d) for d in docs])
```

Each stage is an ordinary task call with per-call occurrence identity, so
replay/resume of a half-finished batch works for free. This idiom is the
recommended shape and gets documented; an earlier draft proposed a
dedicated `pipeline_map` combinator, rejected as sugar over the above.

### Design

Close the two real gaps in `parallel` itself:

```python
results = await parallel(
    *[process(d) for d in docs],
    max_concurrent=8,        # semaphore; None (default) = unlimited
    raise_on_error=False,    # default True = today's fail-fast behavior
)
```

- **`max_concurrent`** — caps in-flight coroutines with a semaphore;
  `None` (default) preserves today's unbounded behavior.
- **`raise_on_error=True`** (default) — fail-fast propagation, exactly
  today's behavior. With `raise_on_error=False`, a failed coroutine's slot
  in the result list becomes `None` and the remaining items keep running;
  the exception is recorded on the corresponding task's events as usual,
  so nothing is silently swallowed — the failure is visible in the
  execution log, it just doesn't kill the batch.
- Results return in input order, `None` for dropped items.
- Fully backwards compatible: both parameters default to today's
  semantics.

## 3. Budget — a spend ceiling for LLM work

### Today

No provider reports usage anywhere in the AI stack: `formatter.py` has no
usage hook, the agent loop counts tool *calls* but not tokens. An agent loop
(or an authored workflow full of `agent()` calls) has no way to bound its
own spend.

### Design

**Usage plumbing (the bulk of the work).** `LLMResponse` gains
`usage: Usage | None` where `Usage(input_tokens, output_tokens)`; each
provider module populates it from its native response shape (all four
expose token counts), including the reasoning-stream paths, whose final
usage arrives on the terminal chunk. Custom providers opt in by populating
the field. Usage rides the checkpointed LLM task output, so it is visible
in the execution's events. The agent loop records it into the budget after
every LLM call. One consequence: setting a budget disables token-level
content streaming for that agent (the final text still arrives as a
progress event), because the raw token-stream path bypasses the LLM task
and reports no usage.

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
  within one *run attempt*. On resume, the workflow re-runs and replayed
  LLM task calls short-circuit from the event log — their recorded usage
  (it rides `LLMResponse`) is re-counted into the fresh budget in the same
  order, so a resumed attempt's accounting stays consistent without
  re-spending real tokens. Sharing one budget across *concurrently running*
  agents makes the exact enforcement point nondeterministic across replays.
  Durable cumulative accounting across attempts (usage as persisted
  events) is future work, noted in the doc.

## Config

No new config section. `max_schema_retries` is a per-call `agent()`
parameter (default 1). Budget is explicit-object-only — no ambient global.

## Testing

- **Structured output:** schema + tools returns validated model (mock
  provider); malformed-then-corrected retry path; retry exhaustion fails
  with field-level detail; delegation surfaces validated instances;
  tool-less path unchanged (existing tests keep passing).
- **parallel:** ordering preserved; fail-fast default unchanged (existing
  tests keep passing) vs `raise_on_error=False` error-drop (`None` slot,
  batch survives, failure event recorded); `max_concurrent` cap actually
  bounds in-flight count; staged per-item idiom replays correctly (pause
  mid-batch, resume, completed stage calls not re-run).
- **Budget:** per-provider usage extraction (fixture responses);
  accumulation across calls sharing one budget; pre-flight ceiling raises
  `BudgetExceededError` (catchable in workflow code); `None` ceiling tracks
  without enforcement; budget forces the usage-reporting path for
  otherwise-streaming tool-less agents; retry turns count against the
  budget.

## Rollout / compatibility

Additive: new optional parameters (all defaulting to today's semantics)
and a new formatter method with a `None`-returning default (existing
custom formatters keep working; budget enforcement simply sees no usage
from them and only tracking-only budgets are useful until they implement
it). Version: 0.59.0.
