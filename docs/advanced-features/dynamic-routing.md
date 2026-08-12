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
| `meta("key")` | server-side worker metadata (`flux worker metadata set` — admin-written, worker-unspoofable; see [Worker Affinity](worker-affinity.md#server-side-worker-metadata)) | live, re-read at dispatch |
| `resource("field")` | `cpu_total`, `cpu_available`, `memory_total`, `memory_available`, `disk_total`, `disk_free` | registration-time snapshot (prefer `metric("flux.cpu_percent")` etc. for live values) |
| `load()` | active executions on the worker | live, computed at dispatch |
| `utilization()` | active executions ÷ the worker's advertised capacity | live, computed at dispatch |
| `routing_input("key")` | per-execution values the worker never receives (see [below](#routing-on-values-the-worker-cannot-see)) | set at submission |

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
  dynamic keys — `label_for` and `meta_for` alike.)
- `prefer(meta_for("approved.", input("artefact")) == "true", weight=5)` —
  the same dynamic-key mechanics against **server-held metadata** the
  worker cannot write; see
  [worker metadata](worker-affinity.md#server-side-worker-metadata).
- `prefer(service(input("model")), weight=2)` — prefer a worker with the
  granted service socket, fall back to the rest.
- `when(input("latency_sensitive") == "true", least(load(), weight=10))` —
  apply a term only when the request says it matters. An `input(...)`
  condition resolves once per execution (`==`/`!=` only); unresolved leaves
  the term inactive.
- `when(load() < 5, sticky(weight=3))` — apply a term only to the workers a
  condition holds for. `meta()`, `metric()`, `load()` and `utilization()`
  conditions are evaluated **per worker**, so this reads as "keep the
  stickiness while the preferred worker still has room". See
  [gating on load](#gating-on-load) below.

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

## Gating on load

Normalization is relative, which makes `least(load())` sharper than it
looks: across two workers at loads 1 and 0, the gap normalizes to a full
1.0 — the largest value the term can take. So pairing it with a plain
`sticky(...)` leaves only two postures, and neither is "prefer this worker
until it fills up":

| Policy | Behaviour |
|---|---|
| `sticky(weight=3)` | deterministic — the preferred worker wins at any load |
| `sticky(weight=0.5)` | defeated by a **single** in-flight execution |

An absolute gate gives the middle posture, because a `when()` condition on
worker state is evaluated per candidate:

```python
routing=score(
    when(load() < 5, sticky(weight=3)),   # stickiness, while there is room
    least(load()),                        # otherwise spread
)
```

`load()` is a count, so a threshold tuned for a 4-slot worker means
something different on a 32-slot one. In a mixed fleet use the ratio:

```python
routing=score(when(utilization() < 0.8, sticky(weight=3)), least(load()))
```

`utilization()` is `load() ÷ max_concurrent_executions`. A worker that
advertises no capacity has **no** utilization rather than zero, so it does
not match `utilization() < 0.8` — otherwise one uncapped worker would read
as permanently idle and collect every preference in the fleet.

Note that a worker at capacity is already filtered out as a hard constraint,
so a threshold of `1.0` gates nothing new; the useful values sit below it.

Both are counted by the server from its own execution table, which is what
makes them safe as conditions: `label()` and `resource()` are asserted by
the worker, so gating on them would let a worker dodge or attract work, and
they remain rejected in `when()`.

Score terms only. Gating a `require(...)` term on load is refused at
registration: skipping a hard constraint makes a worker match *more*
easily, so it would drop the requirement on exactly the busy workers it was
meant to steer away from.

## Routing on values the worker cannot see

Affinity and scoring normally match against execution input — and that input is
delivered to the worker, because it is how a workflow receives its arguments.
So a field used *purely* for routing is observable by the worker it routes to.

That matters when the routing decision is **about** the worker: audit sampling,
A/B assignment, canary probes. There, "does my input contain the cohort field?"
tells a workflow it is being watched before its body runs — and it is the
*presence* of the key that gives it away, not the value, so obfuscating the
value does not help.

`routing_input(...)` reads from a separate channel that is matched at dispatch
and never delivered:

```python
@workflow.with_options(
    affinity=require(optional(label("cohort") == routing_input("cohort"))),
)
async def audit_probe(ctx: ExecutionContext[dict]):
    ...
```

```bash
curl -X POST localhost:8000/workflows/default/audit_probe/run/async \
     -H 'X-Flux-Routing-Input: {"cohort":"canary"}' \
     -d '{"real":"payload"}'

flux workflow run audit_probe '{"real":"payload"}' -r cohort=canary
```

It works wherever `input(...)` does — `require`, `prefer`, `when`, and dynamic
keys such as `label_for("cache.", routing_input("dataset"))` — and is set from
the run header, a schedule's `routing_input` field, `call(routing_input=...)`,
or the CLI flag above.

**What is hidden, precisely.** The key name is not secret: it is written in the
workflow source, and that source travels to the worker. What the worker cannot
learn is the value **or whether this execution had one at all** — which is the
classifier that mattered. A worker can read `routing_input("cohort")` in its own
affinity and still not know whether it was selected by it.

Consequences worth knowing:

- **Values are never delivered or read back.** They are absent from the context
  the worker receives, from `GET /executions/{id}`, and from both SSE frames.
  There is no API read path at any privilege level — an admin-scoped read would
  put them back on the surface this exists to keep them off. Operators get the
  key *names* in a server log line at ingress, never the values.
- **Diagnostics say nothing.** A routing constraint that cannot be satisfied
  fails with `routing constraint unsatisfied` and no key or value, because that
  message is written to the execution's output, which the worker can read.
- **Rejected, never dropped.** Malformed JSON, a non-object payload, a value
  over 4KB, a repeated header, or a key containing `.` at any depth is a 400.
  A silently discarded routing directive would route the execution somewhere
  the caller did not intend, and that is invisible from outside.
- **No extra permission.** Routing values are exactly as powerful as `input` —
  `require(label("host") == routing_input("host"))` grants the same pinning
  that `input("host")` does today — so they need no grant beyond running the
  workflow. Use `X-Flux-Require-Worker` when you want binding placement; that
  one *does* require `worker:{name}:target`.
- **CLI values are strings**, as with `--label`, and `key=value` cannot express
  nesting. Numbers coerce on comparison; booleans do not, so a boolean needs
  the API.

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

## Binding an execution to one worker

The hint above is advisory: dispatch silently places the execution elsewhere
when the named worker is busy or gone. That is wrong whenever the execution
is a check *on* a worker — verification, an A/B against one instance,
reproducing a fault on a suspect node — because falling back is
indistinguishable from success at the client.

`X-Flux-Require-Worker` is the binding counterpart:

```bash
curl -X POST localhost:8000/workflows/default/verify/run/async \
     -H "X-Flux-Require-Worker: worker-7"
```

| | `X-Flux-Preferred-Worker` | `X-Flux-Require-Worker` |
|---|---|---|
| Busy or offline worker | falls back | parks, never falls back |
| Invalid value | dropped | rejected (400) |
| Unknown worker name | dropped | rejected (400) |
| Permission | none beyond run | `worker:{name}:target` |

It is a **header, not a policy term**, on purpose: `affinity=` is declared in
workflow source and fixed at registration, but "run *this* execution on
worker-7" is decided per call. A `require()` term that applied only when a
header happened to be present would not be a hard constraint at all.

If both headers are sent the binding wins and the hint is ignored — a binding
leaves one eligible worker, so the hint has nothing left to order. (It is not
an error because `FluxClient.for_current_execution()` attaches the hint
automatically inside a running workflow, so rejecting would break the very
case this feature exists for.)

The name must belong to a worker that has registered at least once; an
unknown name is a 400 rather than an execution that can only park. Binding to
a **registered but currently offline** worker is fine and parks until it
returns.

**It parks rather than falling back.** If the named worker is offline or
fails the workflow's own `affinity`/`requests`, the execution stays
unclaimed. Whether that park ever ends is governed by `park_ttl`, which
**defaults to `0` — park indefinitely**. Set a non-zero `[flux.workers]
park_ttl` (or pass `?park_ttl=` per run) if you want a bound execution to
fail terminally with a `ParkTimeoutError` naming the worker instead of
waiting forever.

Because the binding is a filter on the execution row rather than a score, it
holds in **both** dispatch modes — unlike the advisory hint, which is
event-mode only — and it is re-applied when a paused execution resumes. It is
enforced on the claim endpoint too, so a worker cannot take a bound execution
by claiming it directly.

If a bound execution is released back to `RESUMING` — its worker evicted, or
reaped after crashing mid-resume — it waits on that worker with no fallback,
so the park TTL covers that case too. The clock restarts at the moment it
becomes unassigned rather than running from submission, since a long-running
execution that pauses hours in would otherwise be failed immediately.

The workflow's own constraints still apply on top: the named worker must also
satisfy `affinity=`/`requests`. A `routing=score(...)` policy then ranks a
single survivor, so `sticky()` alongside a binding header is redundant rather
than contradictory.

The directive never reaches the worker. It arrives as a header, lives on the
execution row, and is absent from the context serialized to the worker — so a
workflow that checks a worker cannot tip it off, which is the point.
