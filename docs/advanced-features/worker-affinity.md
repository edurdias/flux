# Worker Affinity

Worker affinity lets you target workflows to specific workers based on capability labels. Workers can declare labels (like `role=harness`, `browser=true`), and workflows can declare affinity requirements to route tasks to workers with matching labels.

## What is Worker Affinity

Worker affinity is a routing mechanism:

1. A **worker** declares immutable capability labels when it starts
2. A **workflow** declares affinity constraints using `@workflow.with_options`
3. When a workflow runs, the system only dispatches to workers whose labels match all affinity requirements

This is different from resource requests: labels describe *capability* (what kind of tools or environment), while resources describe *capacity* (CPU, memory, GPU).

## Starting a Worker with Labels

Labels are declared when a worker starts and cannot be changed without restarting. Start a worker with labels using the `--label` flag:

```bash
flux start worker --label role=harness --label env=sandbox --label browser=true
```

Multiple `--label` flags declare multiple labels. Label keys and values are strings.

View a worker's labels:

```bash
flux worker list
```

This shows each worker's labels in the output.

## Declaring Workflow Affinity

Use `affinity` in `@workflow.with_options` to specify required worker labels:

```python
from flux import workflow, ExecutionContext

@workflow.with_options(affinity={"role": "harness", "browser": "true"})
async def my_agent(ctx: ExecutionContext):
    # This workflow only runs on workers with both
    # role=harness AND browser=true
    return "Running on a harness worker with browser tools"
```

The `affinity` dict is a mapping of label keys to label values. All keys and values are strings.

## Matching Semantics

Affinity matching follows these rules:

- A worker matches affinity if it has **all** labels specified in the affinity dict
- Extra labels on the worker are ignored
- A worker with `role=harness, env=sandbox, browser=true` matches affinity `{"role": "harness", "browser": "true"}`
- A worker with `role=harness` does **not** match affinity `{"role": "harness", "browser": "true"}`
- No affinity constraint = any worker (no filtering)

If no worker matches the affinity requirements when a workflow is dispatched, the workflow cannot run and remains pending until a matching worker appears.

## Labels vs Resource Requests

Labels and resource requests are independent mechanisms:

| Aspect | Labels | Resources |
|--------|--------|-----------|
| **Purpose** | Capability (what kind) | Capacity (how much) |
| **Examples** | `role`, `env`, `browser`, `gpu_model` | CPU cores, memory, GPU count |
| **Matching** | All labels in affinity must match | All resources must be available |
| **Immutability** | Immutable; requires restart to change | Can be dynamic per task |

A dispatch checks both: the worker must match the affinity labels **and** have sufficient resources.

## Resume Behavior

When a paused workflow resumes:

1. The system **prefers** the original worker that executed it before
2. If the original worker is unavailable (offline, removed), the system falls back to any worker matching the affinity labels
3. Resume always respects affinity constraints — a worker without the required labels cannot pick up a resumed workflow

This ensures workflows can reconnect to the same worker context when possible, improving task continuity.

## Example: Pinning to a Sandbox Harness Worker

Here's a practical example: an AI agent that needs browser tools and a sandboxed environment.

First, start a specialized worker:

```bash
flux start worker --label role=harness --label env=sandbox --label browser=true
```

Then declare a workflow that targets it:

```python
from flux import workflow, ExecutionContext, task

@task
async def check_website(url: str) -> str:
    """Use browser tools to visit a URL."""
    # Browser tools available only on harness workers
    return f"Checked {url}"

@workflow.with_options(affinity={"role": "harness", "env": "sandbox", "browser": "true"})
async def ai_researcher(ctx: ExecutionContext[str]):
    url = ctx.input
    result = await check_website(url)
    return result

# This workflow will only dispatch to the worker started above
ctx = ai_researcher.run("https://example.com")
```

Without the affinity constraint, the workflow might run on a generic worker without browser tools and fail.

## Affinity Expressions: `require(...)`

The static dict pins a workflow to one fixed label set. An **affinity
expression** built with `flux.routing.require(...)` keeps the same
hard-filter role but resolves its terms **per execution** against the
execution input — one registration can serve many differently-routed
requests. Unlike scoring policies, expressions work in **both** poll and
event dispatch modes (filtering is a per-worker predicate).

```python
from flux import workflow
from flux.routing import require, optional, when, label, label_for, input

@workflow.with_options(
    affinity=require(
        label("datacenter") == input("dc"),                 # data locality
        label_for("dataset.", input("dataset")) == "true",  # worker holds a local copy
        optional(label("node") == input("node")),           # hard pin only when requested
        when(input("classification") == "restricted",
             label("compliance.hipaa") == "true"),          # gate on requester intent
    ),
)
async def locality_query(ctx): ...
```

An execution with input `{"dc": "eu-central", "dataset": "orders-2026"}`
matches only workers in that datacenter labeled `dataset.orders-2026=true`;
add `"node": "node-a"` to pin it to one machine; add
`"classification": "restricted"` to gate it to compliance-certified workers.
Same workflow, different eligible sets, zero catalog churn.

Other common patterns:

```python
# Tenant isolation on a shared fleet
affinity=require(label("tenant") == input("tenant_id"))

# Maintenance windows without redeploying workflows
affinity=require(label("maintenance") != "true")
```

### Vocabulary

- `label(key) == value` / `label(key) != value` — compare a worker label
  against a constant or `input("path")` (dotted paths descend nested dicts).
  Only `==` and `!=`; ordered comparisons belong to
  [Dynamic Routing](dynamic-routing.md).
- `label_for(prefix, input("path"))` — dynamic label key: the author
  declares the namespace (`prefix` is mandatory), input only completes it.
  The resolved key must be a valid label key; inputs never create labels,
  only test them.
- `service(name_or_input)` — targets workers holding a granted service
  socket; sugar for `label_for("flux.service.", ...) == "true"`. Because the
  `flux.` label prefix is reserved (workers reject user labels under it),
  this is a capability grant a worker cannot fabricate. See
  [Airgapped Execution](airgapped-execution.md).
- `meta(key) == value` / `meta(key) != value` — compare **server-side
  worker metadata** (written through the admin API, never by the worker)
  instead of a self-advertised label. Same `==`/`!=` semantics as `label`,
  including the absent-≠-value inversion. See
  [Server-Side Worker Metadata](#server-side-worker-metadata) below.
- `optional(term)` — skipped when its input is absent; a resolved
  comparison that is false still fails the match, and input that resolves
  to something invalid (bad label key, non-scalar, invalid service name)
  fails and diagnoses exactly like a bare term.
- `when(input(...) == const, term)` — the term applies only when the input
  condition holds. Conditions read execution input only, never worker
  attributes; an unresolved condition leaves the term inactive.

### Evaluation semantics

Terms are AND-ed and evaluation is **fail-closed**: a bare term whose input
cannot be resolved matches no worker — and because that is a property of the
execution alone, dispatch **fails the execution** with an error naming the
unresolved input instead of queueing it forever. A resolvable-but-unmatched
expression parks the execution exactly like the dict form (a matching worker
may join later).

| Term | Input resolved? | Label present? | Result |
|---|---|---|---|
| `label(k) == input(p)` | no | — | no match; execution **fails** with a named diagnostic |
| `label(k) == input(p)` | yes | no | no match (parks) |
| `label(k) == input(p)` | yes | yes, equal | match |
| `label(k) != v` | — | no | **match** (absent ≠ v — the one documented inversion) |
| `optional(term)` | no | — | term skipped |
| `optional(term)` | yes, false | — | no match (optional ≠ decorative) |
| `optional(term)` | yes, invalid key | — | no match; execution **fails** (optional forgives absence only) |
| `when(if, then)`, `if` unresolved | — | — | term inactive |
| `label_for(...)`, resolved key invalid | yes | — | no match; execution **fails** with a named diagnostic |

Input values compare against labels as strings (booleans as
`"true"`/`"false"`). The dict form remains valid forever and its semantics
are unchanged.

Expressions are extracted statically at registration (AST, like `routing=`);
an expression that cannot be statically extracted fails registration loudly
rather than silently dropping a hard constraint.

See `examples/affinity_expressions.py` for runnable data-locality, tenant
isolation, and maintenance-window patterns.

## Server-Side Worker Metadata

Labels are the right channel for what a worker declares about itself; some
routing inputs are facts the **control plane** asserts about a worker —
policy flags, drain hints, centrally-computed scores. Worker **metadata** is
a third attribute channel for exactly that: a server-held
`dict[str, str | float]` written only through an authenticated admin API
(`admin:workers:manage`) and consumed through the `meta(...)` selector.

```bash
flux worker metadata set   my-worker maintenance=true weight=0.8
flux worker metadata show  my-worker
flux worker metadata unset my-worker maintenance
flux worker metadata clear my-worker
```

or over HTTP: `PUT /admin/workers/{name}/metadata` with
`{"metadata": {"maintenance": "true"}, "replace": false}` (merge by
default), `GET`/`DELETE` on the same path, and
`DELETE .../metadata/{key}` for a single key. `GET /workers` surfaces the
current values.

```python
# Soft-drain a worker without touching the worker or the workflows
affinity=require(meta("maintenance") != "true")
```

Properties that distinguish metadata from labels:

- **Authoritative.** Workers have no write path to it — registration never
  touches it — and `meta(...)` reads only this dict, so no label or metric
  a worker advertises can satisfy a `meta` term.
- **Hot.** Updates take effect on the next dispatch: the dispatch
  transaction re-reads the values, no worker re-registration or reconnect
  involved.
- **Durable.** Values survive worker reconnect and re-registration; they
  live and die with the worker's registry row.
- **Numeric-capable.** String values suit `require(...)` equality; numeric
  values participate in `score()` ranking via `least(meta(...))` /
  `most(meta(...))` — see [Dynamic Routing](dynamic-routing.md).

## Beyond Hard Constraints

Affinity decides which workers *can* run a workflow. To rank the eligible
workers — by latency, load, locality, or custom metrics — add a scoring
policy on top: see [Dynamic Routing](dynamic-routing.md). The dynamic
vocabulary spans both stages — `input(...)` values, `label_for(...)` keys,
`service(...)`, and `when(...)` work in `prefer()` too, so the same
comparison can be a hard wall in `require()` and a soft preference in
`score()`.
