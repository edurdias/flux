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
make bench                       # all three, ci profile, SQLite
make bench B=b1                  # one benchmark
make bench PROFILE=workstation   # bigger windows for a real dev box
make bench-postgresql            # against the dockerized PostgreSQL
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
| **B3** | replay cost | `WORKFLOW_RESUME_SCHEDULED` → `WORKFLOW_COMPLETED`, across two or more history lengths |

B2 measures the in-flight window rather than wall time so submission and
drain ramps do not deflate the rate. B3 reports the **marginal** cost per
task of history (what each extra task adds), not the average — at small
histories the average is dominated by the fixed resume overhead and says
nothing about how replay scales.

## Baseline

Recorded at `0.86.7`, `ci` profile, SQLite backend.

- **Machine**: Intel Core i9-14900HX, 32 cores, Linux; localhost HTTP RTT 3.4 ms
- **Raw records**: `tests/perf/results/B1|B2|B3/*.json`

| Metric | Value |
|---|---|
| Dispatch latency (scheduled → started) | p50 **823 ms**, p95 1164 ms, p99 1297 ms (n=40) |
| ↳ claim half (scheduled → claimed) | p50 **86 ms**, p95 153 ms |
| Sustained throughput | **189 tasks/s** (20 workflows × 10 tasks, submit concurrency 8) |
| Replay, fixed cost | **~570 ms** per resume |
| Replay, marginal cost | **0.35 ms** per task of history (25 → 100 tasks) |

### What the baseline already says

**Dispatch is not claim-bound.** A worker claims an execution in ~86 ms
but the workflow does not start for ~823 ms. Roughly 700 ms of the p50 is
spent *after* the claim — module compile and the runner child's startup
— so work aimed at dispatch latency (#261, #263) should target that
window, and this benchmark will show it directly as the gap between the
`claim_ms` and `dispatch_ms` series.

**Replay is linear and cheap per task, but resume has a fixed floor.**
0.35 ms per task of history against ~570 ms of fixed resume cost means a
1,000-task history still replays in well under a second, and the thing
worth attacking is the constant, not the walk. #262 (event-store batching)
should move the constant; if it moves the *marginal* number instead,
something regressed.

## Profiling

`make bench-profile B=<id>` runs a benchmark with the server and worker
launched **under** py-spy, writing flame graphs to
`docs/benchmarks/flamegraphs/`.

Two mechanics worth knowing, both learned the hard way:

- The processes are launched under py-spy rather than attached to
  afterwards. With `kernel.yama.ptrace_scope=1` — the default on most
  distributions — a profiler may only trace its own descendants, so
  attaching to a running server needs root while spawning it does not.
- The harness stops a profiled run with **SIGINT**, not SIGTERM: py-spy
  renders its flamegraph on Ctrl-C or when the profiled process exits, and
  a SIGTERM kills it with nothing written.

py-spy is a profiling tool, not a project dependency: install it into the
environment when you want graphs (`poetry run pip install py-spy`). The
benchmarks run normally without it.

Committed graphs are the `ci`-profile baseline for the release named
above; regenerate rather than trust them across versions.
