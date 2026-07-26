# Agent Architecture with Flux: Harness, Loop, and Graph Engineering

Production agent systems are built out of three distinct engineering layers, and
each one gives you a different lever to pull when the system misbehaves:

| Layer | What it is | Flux features |
|---|---|---|
| **Harness engineering** | The machinery around the model: tool definitions, execution control, credentials, approval gates, audit trail | Tools as `@task` with `retry_*`, `timeout`, `fallback`, `rollback`; `secret_requests`; `requires_approval`; sandboxed runners; the `ExecutionEvent` log |
| **Loop engineering** | The repeated work-and-feedback cycle: verify with evidence, bound the retries, name the escalation path | `agent()` with `max_tool_calls` and `Budget`; task retry chains; `pause()` as a durable human interrupt |
| **Graph engineering** | Explicit workflow topology: nodes, branches, fan-out, joins, gates, checkpoints | `Graph` (validated DAG with conditional edges), `parallel` / `pipeline`, event-sourced state machine with deterministic replay |

The layers are not interchangeable — a bad prompt is not a harness bug, an
unbounded retry loop is not a model problem, and hidden control flow is not
fixed by a better verifier. These examples demonstrate each practice in
isolation, then compose them.

## The examples

Each standalone example is deterministic (no LLM required) so the *practice*
stays in focus, and each is covered by the unit suite in
`tests/examples/agent_architecture/`.

### 1. [`harness.py`](harness.py) — harness engineering

Builds the hardened tool surface for a market-briefing agent: a flaky upstream
that the harness retries with backoff, a dead dependency that degrades to a
fallback, a credential injected per-call via `secret_requests` (never stored in
the event log), and an outward-facing publish step gated by
`requires_approval`. The agent code never sees any of this machinery — it is
declared on the tools.

```bash
poetry run python examples/agent_architecture/harness.py
```

### 2. [`loop.py`](loop.py) — loop engineering

Implements *loop on evidence, not confidence*: a simulated generator produces a
draft, a deterministic verifier returns the failing checks (the evidence), the
loop feeds the evidence back for revision, and a bounded attempt budget ends in
a **named escalation path** — a durable `pause()` a human resolves, whose
resolution is itself verified before the run may succeed.

```bash
poetry run python examples/agent_architecture/loop.py
```

### 3. [`graph.py`](graph.py) — graph engineering

Declares the topology explicitly with `Graph`: one ingest node fans out to two
parallel branches, a join node waits for both, and a **conditional edge**
(quality gate) decides whether the publish node runs at all. The graph is
validated (connectivity, no cycles) before execution, and every node is a
checkpointed task.

```bash
poetry run python examples/agent_architecture/graph.py
```

### 4. [`full_stack_agent_ollama.py`](full_stack_agent_ollama.py) — all three, nested

A release-manager agent driven by a real LLM (Ollama), showing how the layers
compose: hardened tools with an always-on approval gate (harness), `agent()`
bounded by `max_tool_calls` and a token `Budget` (loop), all running inside a
durable workflow that can pause on the approval for days and resume by
deterministic replay (graph/durability).

```bash
# Requires Ollama with a tool-capable model: ollama pull llama3.2
flux workflow register examples/agent_architecture/full_stack_agent_ollama.py
flux workflow run release_manager_agent '{"version": "2.1.0"}'
```

## Why Flux for this

Most frameworks make you pick a layer: agent-loop libraries give you the loop
but bolt on durability; workflow engines give you the graph but treat the LLM
as an opaque activity. In Flux the agent loop runs *inside* a durable
workflow — every LLM call and tool execution is a checkpointed `@task`, so a
crash mid-loop resumes exactly where it stopped, human interrupts are ordinary
control flow, and the same code runs inline for development or distributed
across workers in production.
