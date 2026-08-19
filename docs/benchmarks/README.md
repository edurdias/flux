# Engine benchmarks (B series)

The three numbers every performance change in Flux is judged against
(issue #259): **dispatch latency**, **sustained throughput**, and **replay
cost**. They live in `tests/perf/test_b*.py` and share the perf suite's
harness — one real server, one real worker, both as subprocesses, no
in-process shortcuts.

They are deliberately separate from the T series in the same directory:
T characterizes the *progress-streaming* path (`tests/perf/PLAN.md`), B
measures the engine underneath it.

## Running them

```bash
make bench                       # all four, ci profile, SQLite
make bench B=b1                  # one benchmark
make bench PROFILE=workstation   # bigger windows for a real dev box
make bench-postgresql            # against dockerized PostgreSQL (needs the postgresql extra)
make bench-profile B=b2          # same run, under py-spy (see Profiling)
```

Profiles come from `tests/perf/fixtures/harness/profile.py`: `ci` is sized
to finish in a shared pipeline, `workstation` for a 16–32 core dev box,
`full` for quiet dedicated hardware. Every run writes a JSON record under
`tests/perf/results/<B*>/` carrying the machine spec and the measured
localhost HTTP RTT, so a figure can be compared against one from another
box honestly.

Correctness gates hard-fail (every execution sampled, every task
persisted, no task re-executed on replay). Performance gates are soft
unless `FLUX_PERF_STRICT=1` — a noisy box should produce numbers, not red
pipelines.

CI does not run these on every PR: the perf suite is opt-in
(`FLUX_PERF=1`). Deliberate runs go through the existing on-demand
workflow (`.github/workflows/perf.yml`), whose `tests` input takes a
selector — `tests/perf -k "b1 or b2 or b3"`.

## What each one measures

Every figure is derived from **server-stamped event times**. The server
re-stamps `ExecutionEvent.time` at ingest with its own clock, so an
interval between two events is one clock's arithmetic — no skew between
machines, and no client polling granularity smeared into a latency.

| Benchmark | Metric | Derived from |
|---|---|---|
| **B1** | dispatch latency p50/p95/p99 | `WORKFLOW_SCHEDULED` → `WORKFLOW_STARTED`, plus `→ WORKFLOW_CLAIMED` and `→ TASK_STARTED` as the two halves |
| **B2** | sustained tasks/second | total `TASK_COMPLETED` ÷ the window work was in flight (first task start → last task completion) |
| **B4** | loop responsiveness under load | `GET /health` round trips sampled while a throughput workload runs, idle vs loaded |
| **B3** | replay cost | `WORKFLOW_RESUME_SCHEDULED` → `WORKFLOW_COMPLETED`, across two or more history lengths |

B2 measures the in-flight window rather than wall time so submission and
drain ramps do not deflate the rate. B3 reports the **marginal** cost per
task of history (what each extra task adds), not the average — at small
histories the average is dominated by the fixed resume overhead and says
nothing about how replay scales.

## Baseline

Recorded at `0.86.8`, `ci` profile, on **both backends** — SQLite (the
default) and PostgreSQL 16 (what production uses, via
`make bench-postgresql`). Every run record names its `backend`; a dispatch
or throughput figure without it cannot be compared to anything.

- **Machine**: Intel Core i9-14900HX, 32 cores, Linux; localhost HTTP RTT ~3.4 ms
- **Raw records**: `tests/perf/results/B1|B2|B3/*.json`

| Metric | SQLite | PostgreSQL |
|---|---|---|
| Dispatch p50 / p95 / p99 | **850** / 1348 / 1381 ms | **870** / 1248 / 1288 ms |
| ↳ claim half, p50 / p95 | **87** / 162 ms | **158** / 214 ms |
| Sustained throughput | **194 tasks/s** | **161 tasks/s** |
| Replay, fixed cost | ~585 ms | ~589 ms |
| Replay, marginal cost | **0.24 ms/task** | **0.47 ms/task** |

### Loop responsiveness (B4)

A blocking call inside an async handler does not slow *its own* request
down; it stalls every other request sharing the loop. B4 makes that
visible by pinging `GET /health` -- a handler that touches nothing --
while a workload runs. On an idle server that is one round trip; under
load, everything above it is the loop being held.

| | idle p95 | under load p50 | p95 | p99 | samples |
|---|---|---|---|---|---|
| ci profile | 6.5 ms | 8.4 ms | 37.4 ms | 61.6 ms | 151 over 100 % of the load window |
| workstation profile | 7.2 ms | 14.0 ms | 55.5 ms | 73.1 ms | 87 over 99 % |

The run gates on *coverage* rather than a sample count: under heavier load
each ping takes longer, so a fixed count tightens exactly when the system
is busiest -- which is the window the measurement exists to cover.

Set `[flux.observability] slow_callback_ms` (env
`FLUX_OBSERVABILITY__SLOW_CALLBACK_MS`) to run the server's loop in
asyncio debug mode and have it name every callback that holds the loop
longer than that. It is the direct form of the same question, and what
#263's "no slow-callback warnings above threshold" is measured with.

### What the baseline already says

**Dispatch is not claim-bound, and not database-bound.** A worker claims
in 87 ms (SQLite) or 158 ms (PostgreSQL) but the workflow does not start
for ~860 ms on *either* backend. Nearly doubling the claim cost moves the
total by ~2 %, which puts ~700 ms of the p50 somewhere the database is not:
module compile and runner-child startup, after the claim. That is where
work on dispatch latency (#261, #263) has to aim, and the gap between the
recorded `claim_ms` and `dispatch_ms` series is the instrument for it.

**The database differences are real but second-order.** PostgreSQL costs
~1.8× on claim, ~17 % on throughput and ~2× on the marginal replay walk —
all consistent with a network round trip replacing a local file read, and
all small next to the fixed costs beside them.

**Replay is linear and cheap per task against a fixed floor.** ~0.25–0.5 ms
per task of history versus ~585 ms of fixed resume cost on both backends,
so #262 (event-store batching) should move the *constant*. If it moves the
marginal number instead, something regressed.

## Event loop: what the measurement said about uvloop (#261)

`event_loop` (top level in flux.toml) selects the loop for the server and worker
(`asyncio` by default, `auto` for uvloop-when-installed, `uvloop` to
require it; `pip install 'flux-core[uvloop]'`).

**The default is asyncio because the numbers said so.** #261 expected
uvloop to improve dispatch p95. On this suite it did not improve anything,
and cost a little at the tail of the claim path:

| claim p50 (ms) | pair 1 | pair 2 | pair 3 | pair 4 |
|---|---|---|---|---|
| asyncio | 103 | 114 | 106 | 97 |
| uvloop | 139 | 91 | 125 | 155 |

| claim p95 (ms) | pair 1 | pair 2 | pair 3 | pair 4 |
|---|---|---|---|---|
| asyncio | 177 | 227 | 170 | 186 |
| uvloop | 261 | 222 | 260 | 248 |

Runs are **interleaved** (asyncio, uvloop, asyncio, uvloop …), not batched
per loop: an earlier batched comparison showed uvloop uniformly worse, and
interleaving reversed one of the four pairs — which is what a machine-drift
confound looks like. p50 is therefore inconclusive; p95 is worse in 4 of 4;
dispatch p50 and throughput are unchanged.

Isolating the two processes (server loop and worker loop switched
independently, sequential submissions with no queue) showed **no difference
at all**: claim p50 21.1 / 21.1 / 20.8 / 21.4 ms across the four
combinations. Whatever the loaded runs are picking up, it is contention
behavior, not raw loop speed.

Other hardware and larger fleets may differ — the point of shipping the
setting is that an operator can opt in and check with `make bench` rather
than take anyone's word for it.

### Connection pooling

The other half of #261 was already in place: the worker holds one
long-lived `httpx.AsyncClient`, and `flux/task.py` a module-level one. The
two remaining per-call constructions are correct as they are — the worker's
SSE connection is a single long-lived stream, and the OIDC discovery
fetch sits behind a TTL cache.

B2 records open file descriptors idle-before and idle-after, counted after
the fleet goes quiet rather than mid-flight (keep-alive is *supposed* to
hold connections open, so a mid-flight count cannot tell a pool from a
leak). Across three back-to-back load cycles the counts settle rather than
climb — server 22 cold, then 44 / 38 / 42; worker 18 cold, then 28 / 26 /
26 — which is a warm pool, not a leak.

## Where throughput is actually bound (#262)

#262 assumed per-event synchronous writes were the ceiling. They are not,
and the measurement is worth keeping because it also says what *would*
help.

**Event writes are already batched at three levels** — the worker's
outbox coalesces snapshots and sends only unacknowledged events, the
server writes a checkpoint's events in one transaction, and SQLite already
runs WAL with `synchronous=NORMAL`. Measured cost of the write path:

| Events per checkpoint | SQLite | PostgreSQL |
|---|---|---|
| 1 | 1.81 ms | 3.27 ms |
| 10 | 2.89 ms (0.29/event) | 6.05 ms (0.61/event) |
| 50 | 6.38 ms (0.13/event) | 13.80 ms (0.28/event) |

**Throughput is bound by per-execution overhead, not per-event cost.**
Holding executions constant at 20 and varying tasks per execution:

| tasks per execution | total tasks | window | throughput |
|---|---|---|---|
| 5 | 100 | 0.85 s | 118 tasks/s |
| 10 | 200 | 0.99 s | 201 tasks/s |
| 20 | 400 | 1.29 s | 311 tasks/s |
| 40 | 800 | 1.53 s | **524 tasks/s** |

Eight times the tasks in 1.8x the window. The fixed cost of starting an
execution -- the ~700 ms post-claim window B1 already isolates -- is what
a throughput number is really measuring at small task counts, which is
also why "tasks/s" should always be read together with the shape of the
workload that produced it.

## Serialization: dill vs msgpack (#260)

Encode/decode of representative payloads, measured directly:

| payload | dill encode | msgpack encode | dill decode | msgpack decode |
|---|---|---|---|---|
| small string | 0.009 ms | **0.001 ms** | 0.003 ms | 0.000 ms |
| dict, 20 keys | 0.073 ms | **0.003 ms** | 0.006 ms | 0.003 ms |
| list of 1,000 ints | 1.129 ms | **0.051 ms** | 0.040 ms | 0.035 ms |
| nested, 200 rows | 2.571 ms | **0.060 ms** | 0.055 ms | 0.088 ms |
| 100 KB text | 0.017 ms | 0.008 ms | 0.016 ms | 0.009 ms |

Encoding is 2-43x faster; decoding is a wash (dill's decode was never the
expensive half, and msgpack is slightly slower on the deeply nested case).
Payload sizes are within a few percent either way.

**End to end this is invisible, and that is the honest headline.** The
B-series after the change: dispatch p50 830 ms SQLite / 884 ms PostgreSQL,
throughput 180 / 162 tasks/s, replay 572 / 608 ms fixed -- all inside the
run-to-run spread documented above. Serialization is a fraction of a ~1 ms
per-task marginal, so a 43x on the encode of a large payload does not move
a benchmark whose cost is dominated by execution startup.

The reason to make the change is the other one: every payload on msgpack
is a payload whose *read* no longer executes code.

## Profiling

`make bench-profile B=<id>` runs a benchmark with the server and worker
launched **under** py-spy, writing flame graphs to
`docs/benchmarks/flamegraphs/`.

Three mechanics worth knowing, all learned the hard way:

- **Profile the Flux process, not the launcher.** The harness normally
  starts `poetry run flux …`; profiling through that produces a graph of
  poetry's own imports and nothing of the server. Profiled runs therefore
  invoke the venv's `flux` console script directly. The first version of
  this doc shipped graphs with zero Flux frames in them for exactly this
  reason.
- **Launch under py-spy rather than attaching.** With
  `kernel.yama.ptrace_scope=1` — the default on most distributions — a
  profiler may only trace its own descendants, so attaching to a running
  server needs root while spawning it does not.
- **Stop a profiled run with SIGINT.** py-spy renders on Ctrl-C or when
  the profiled process exits; SIGTERM kills it with nothing written.

Knobs: `FLUX_BENCH_PYSPY_RATE` (default 25 Hz — higher rates make py-spy
fall behind and stall the process it is sampling),
`FLUX_BENCH_PYSPY_FORMAT=raw` for folded stacks that can be aggregated in
a shell, and `FLUX_BENCH_PYSPY_SUBPROCESSES=1` to follow runner children
(off by default: one child per execution is more tracing than the graph is
worth).

**Limits.** A profiled run is a slower system by construction — never
compare its numbers to a baseline; the graphs are for *shape*, and the
figures come from an unprofiled run. B1 and B3 profile cleanly. **B2 does
not**: its concurrent fan-out under sampling makes the server miss its
health window or a status read time out. Use `B=b1` for graphs, or profile
B2 on quieter hardware.

py-spy is a profiling tool, not a project dependency: install it when you
want graphs (`poetry run pip install py-spy`). The benchmarks run normally
without it. Committed graphs are the `ci`-profile baseline for the release
named above; regenerate rather than trust them across versions.
