# Dynamic Routing

Dynamic routing lets a workflow decide **which** of its eligible workers
*should* run it — by latency, queue depth, locality, utilization, or any
factor you can measure. It builds on the two mechanisms you may already use:

- [Resource requests](../core-concepts/workflow-management.md) and
  [worker affinity](worker-affinity.md) are **hard constraints**: they filter
  the workers that *can* run a workflow.
- A **routing policy** is a soft preference: it ranks the workers that
  survived the filter. Without one, Flux picks the least-loaded eligible
  worker.

Routing policies are evaluated by the event dispatcher
(`[flux.dispatch] mode = "event"`); the legacy poll mode ignores them.

## Declaring a policy

```python
from flux import ExecutionContext, workflow
from flux.routing import score, prefer, least, most, sticky, label, metric, resource, load, input


@workflow.with_options(
    routing=score(
        prefer(label("region") == input("region"), weight=10),  # payload locality
        prefer(metric("temp") < 60, weight=2),                   # threshold preference
        least(metric("queue_depth"), weight=5),                  # minimize a worker metric
        most(resource("memory_available")),                      # maximize a resource field
        sticky(weight=3),                                        # opt the relay hint into the score
        least(load()),                                           # built-in: active executions
    ),
)
async def train(ctx: ExecutionContext[dict]):
    ...
```

A policy is a weighted combination of terms over **selectors**:

| Selector | Reads | Freshness |
|---|---|---|
| `label("key")` | worker labels (`--label key=value`) | static (set at registration) |
| `metric("key")` | worker-advertised metrics (built-in `flux.*` or your provider's) | refreshed every `metrics_interval` |
| `resource("field")` | `cpu_total`, `cpu_available`, `memory_total`, `memory_available`, `disk_total`, `disk_free` | registration-time snapshot (prefer `metric("flux.cpu_percent")` etc. for live values) |
| `load()` | active executions on the worker | live, computed at dispatch |

And four term types:

- `prefer(condition, weight=...)` — scores 1 when the comparison holds.
  Conditions use ordinary Python operators (`==`, `!=`, `<`, `<=`, `>`,
  `>=`) between a selector and a constant or `input(...)`.
- `least(selector, weight=...)` / `most(selector, weight=...)` — prefer the
  lowest / highest numeric value.
- `sticky(weight=...)` — opts the relayed-`call()` worker hint into the
  score. A workflow with a policy owns its score stage entirely: the hint
  participates **only** through this term.

`input("path")` resolves against the execution's input at dispatch time —
dotted paths (`input("customer.region")`) descend nested dictionaries. This
is how payload-driven locality works: the same workflow routes each
execution by its own data.

## Dynamic keys and conditional terms

The [affinity expression](worker-affinity.md) vocabulary works in the score
stage too — the same comparison is a hard wall under `require(...)` and a
soft preference under `prefer(...)`:

- `prefer(label_for("cache.", input("dataset")) == "true", weight=5)` —
  dynamic label key: prefer workers holding a warm copy of *this*
  execution's dataset without excluding cold ones. Unresolved input (or an
  invalid resolved key) just means the term cannot discriminate — everyone
  scores 0 for it; the policy does not degrade. (`least`/`most` reject
  `label_for` — label strings have no ordering.)
- `prefer(service(input("model")), weight=2)` — prefer a worker with the
  granted service socket, fall back to the rest.
- `when(input("latency_sensitive") == "true", least(load(), weight=10))` —
  apply a term only when the request says it matters. The condition reads
  execution input only, never worker attributes; unresolved leaves the term
  inactive.

Pair the stages for floor-plus-preference routing:

```python
@workflow.with_options(
    affinity=require(label("datacenter") == input("dc")),          # must
    routing=score(
        prefer(label_for("cache.", input("dataset")) == "true",    # prefer
               weight=10),
        least(load()),
    ),
)
```

Note the distinction with `optional(...)` in `require`: an optional term is
*hard when its input is present* (a pin), while a `prefer` term is *soft
always* (a nudge).

## How scoring works

1. Hard constraints filter first — a policy can never route to a worker
   that fails `requests`/`affinity`/`runner` matching, is unhealthy, or has
   no free capacity.
2. Each term is normalized to 0–1 **across the eligible workers** (so an
   unbounded `load` term cannot drown a boolean `prefer`), multiplied by
   its weight, and summed.
3. The highest total wins; ties break deterministically (lower load, then
   name).

Degradation is deliberate: a worker missing a metric scores 0 for that
term; a metric absent everywhere makes the term a no-op; a malformed policy
falls back to least-loaded. A routing policy can never strand an execution.

Policies are **data, not code**. The `score(...)` expression compiles to a
JSON spec that is extracted statically at registration (the same AST
mechanism as `requests`) and evaluated natively by the server — no user
code runs in the dispatcher. The flip side: the policy must be declared
with literal values (or `input(...)`); a policy the parser cannot extract
fails registration with a clear error rather than silently routing
differently than written.

## Built-in worker metrics

Every worker publishes a standard metric set under the reserved `flux.`
prefix on its heartbeat — no configuration needed
(`[flux.workers] builtin_metrics = true` by default):

| Metric | Meaning |
|---|---|
| `flux.running_executions` / `flux.slots_free` | live occupancy / headroom (bounded capacity only) |
| `flux.loop_lag_seconds` / `flux.loop_lag_p95_seconds` | latest / p95 event-loop lag |
| `flux.cpu_percent` / `flux.memory_available_bytes` / `flux.load_avg_1m` | live utilization (EWMA-smoothed / quantized) |
| `flux.failure_rate` / `flux.crash_rate` | failed / child-crashed fraction of recent executions |
| `flux.executions_per_minute` | observed completion throughput |
| `flux.execution_duration_p95_seconds` | completion-time tail |
| `flux.startup_overhead_seconds` | median dispatch→first-checkpoint gap (runner spawn/load cost) |
| `flux.warm_modules` | workflow modules warm in the inprocess runner's cache |

So these work with zero setup:

```python
# Steer latency-sensitive work away from degraded-but-not-unhealthy workers
routing=score(least(metric("flux.loop_lag_p95_seconds"), weight=5), least(load()))

# Quarantine workers that accept work and fail it (full disk, sick GPU, ...)
routing=score(prefer(metric("flux.crash_rate") < 0.1, weight=10), least(load()))
```

Aggregates are computed on the worker over fixed windows and published as
single scalars — the server stores only the latest snapshot per worker,
never a time series. For history and trending, use the
[observability](observability.md) pipeline.

## Custom metrics providers

For anything the built-ins don't cover, point the worker at your own
callable (sync or async) returning `dict[str, float]`:

```python
# myapp/routing.py — runs inside the worker process
import psutil


async def collect() -> dict[str, float]:
    return {
        "gpu_queue_depth": gpu_queue.qsize(),
        "shard_latency_ms": await probe_local_shard(),
        "scratch_free_gb": psutil.disk_usage("/scratch").free / 1e9,
    }
```

```toml
[flux.workers]
metrics_provider = "myapp.routing:collect"
metrics_interval = 10.0
```

The worker refreshes the provider on that cadence (sync providers run in a
thread; a failure keeps the previous snapshot) and merges the result with
the built-ins. This is the intended home for *arbitrary* routing logic:
measure anything worker-side — including windowed aggregates like a rolling
p95 you compute yourself — and publish it as a number the server can rank
on declaratively.

Guardrails: a provider may publish up to 32 metrics (string keys ≤64 chars,
finite numbers); invalid payloads are dropped with a warning, never an
error. Keys under the reserved `flux.` prefix are stripped, so user values
can never impersonate a built-in signal.

## Observing routing decisions

- `flux worker list` / `flux worker show <name>` (and `GET /workers`) show
  each worker's latest advertised metrics — the values the last dispatch
  decision actually saw.
- `flux workflow show <name>` includes the registered routing policy in the
  workflow's metadata.

## Relationship to sticky routing

Relayed `call()`s tag their child executions with the calling worker's name
(the `X-Flux-Preferred-Worker` hint), and workflows **without** a policy
prefer that worker when eligible — keeping mesh hops on warm module caches.
A workflow **with** a policy takes full ownership of the score stage;
include `sticky(weight=...)` to blend the hint into your ranking, or omit
it to override the hint entirely.
