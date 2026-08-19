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

| | idle p95 | under load p50 | p95 | p99 |
|---|---|---|---|---|
| ci profile | ~6.6 ms | ~12 ms | ~43 ms | ~58 ms |
| workstation profile | ~7 ms | ~15 ms | ~52-67 ms | ~68-84 ms |

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
