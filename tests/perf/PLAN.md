# Flux Progress-Streaming Stress Test Plan v1.1

**Audience:** implementing agent working in `edurdias/flux` (off `main` at PR #135's merge).
**Goal:** characterize and bound the `progress()` streaming path — sealed child → stdio child protocol → worker → HTTP → server → SSE consumer — at token-frame granularity, and turn "verify streaming at M1" into measured numbers with pass/fail gates. Motivating workload: LLM token streaming (~30–100 events/s per stream, 60–2,000-byte frames), but every test is Flux-general.

**Prime directive:** measure product code as it is. If a test fails its gate, the deliverable is the measurement plus a written proposal — not an inline product patch on this branch. "Product code" means everything under `flux/`; `pyproject.toml` marker registration and dev-only config changes needed to host the suite are explicitly allowed.

## Changelog v1.0 → v1.1

Amendments from the pre-implementation code walk (all line references at `main` = `1d8855b`):

1. **The buffer policy T5 sought is already readable from source** — drop-newest at three stages (see §0b). T5 is now a *verification* test with a pre-registered expected policy, and T1's "frame loss must be 0" gate is replaced by a **loss-onset curve** gate: loss is designed behavior at overload, so the deliverable is the offered-vs-delivered curve and the rate at which loss begins.
2. **Multi-consumer semantics are a known design limit** (single shared queue per execution; any consumer disconnect pops the shared buffer — `flux/server.py:604-605`). New **T5b** documents this; **T7's consumer churn is constrained to strictly sequential** (never two consumers on one execution) so the soak measures drift, not the known limit.
3. **dbmeter is differential, not instrumented.** A SQLAlchemy `after_cursor_execute` listener can't attach to a subprocess server, and hosting the server in-process would share the GIL with the load generator and corrupt T3's knee. dbmeter now reads the SQLite file side-channel (read-only connection): per-table row counts + `execution_events` rows per execution + DB file size, snapshotted before/after windows. The sharp T0 assertion is differential: N=100 and N=5,000 frame runs must produce **identical** persisted event-row counts for their executions.
4. **T4 defaults to a deterministic synthetic SSE sidecar** (fixed inter-token schedule, seeded); real `llama-server` is an opt-in flavor. The measurement is the *delta* between direct-socket and via-Flux consumption — real inference adds an environment dependency without changing the delta.
5. **CI mechanics made explicit.** The unit job runs `pytest tests/ --ignore=tests/e2e`, which would collect `tests/perf/`. The suite self-excludes: `perf` marker registered in `pyproject.toml`, and `tests/perf/conftest.py` skips every test unless explicitly opted in (`-m perf` containing selection or `FLUX_PERF=1`). No CI workflow edit required. Any PR from this branch must bump the version (patch bump for a test-only PR).
6. **T0's replay assertion is delegated** to the existing unit coverage (`tests/flux/test_progress_durability.py` proves replay skips tasks that emitted progress, inline path). Distributed T0 covers the store and the event log; it does not force a worker reclaim.
7. **T6a's gate accounts for the cancel-flush burst** (`flux/worker.py:1334-1350` flushes the remaining queue on teardown): the gate is "no frames after the flush burst completes," with the burst itself measured and documented.
8. **Timestamping convention fixed**: the server stamps `ExecutionEvent.time` at ingest with its own clock, so latency is measured sender-side — every synthetic frame carries `"t": <wall time>` in its payload; consumers record receive wall time on the same host.
9. **Consumer keys on the SSE event name** `task.progress` (what's actually on the wire — `flux/server.py:553-563`); `WIRE_TASK_PROGRESS` in `flux/agents/events.py` is the agent-client decode layer, not the transport name.
10. **T3 synthetic-worker scaffolding spelled out**: each synthetic worker must register via the bootstrap token (`_verify_worker_identity` matches identity to URL name), executions must exist as real rows, consumers must subscribe *before* load (the server buffer is created at subscribe time), and synthetic batches are capped at 50 frames/POST to match the real worker flusher.

---

## 0. Ground truth (read these first)

### 0a. Facts

| Fact | Where |
|---|---|
| `progress()` is documented ephemeral: "never persisted to the event log, stored in the database, or replayed" | `flux/tasks/progress.py` docstring |
| Server-side ingest constructs `ExecutionEventType.TASK_PROGRESS` | `flux/api/worker_routes.py:921` — T0 verifies this object never reaches the store |
| SSE wire format for progress frames | event name `task.progress`, `data.type == "TASK_PROGRESS"` (`flux/server.py:553-563`) |
| Consumer subscribe path (detached) | `GET /executions/{id}?mode=stream` (`flux/api/execution_routes.py:251-270`) — creates the server-side progress buffer |
| E2E scaffolding pattern (server + worker spin-up) | `tests/e2e/conftest.py` — *modeled, never imported* (deliverable 4) |
| Airgapped runner + service sockets (T4 sealed variant) | `docs/advanced-features/airgapped-execution.md`, `flux/runners/docker.py` |
| Inline-path persistence + replay already unit-tested | `tests/flux/test_progress_durability.py` |

### 0b. The delivery pipeline and its drop policy (pre-registered expectation)

Every stage is **drop-newest, silent** (except a worker-side log warning):

1. **Child → parent**: per-frame JSON lines over stdio (`flux/runners/child.py:203`), relayed via `RunnerHooks.progress` (`flux/runners/subprocess_runner.py:155`).
2. **Worker queue**: per-execution `asyncio.Queue(maxsize=1000)`; `QueueFull` → dropped (`flux/worker.py:1396-1407`).
3. **Worker → server**: one serial flusher per execution, batches ≤50 frames per HTTP POST (`flux/worker.py:1332`); a failed POST drops the whole batch with a warning (`flux/worker.py:1357`). Per-execution throughput ceiling ≈ 50/RTT — record RTT in every run.
4. **Server ingest**: no subscribed consumer → all frames discarded (`flux/api/worker_routes.py:916-918`); `asyncio.Queue(maxsize=10000)` per execution, drop-newest (`worker_routes.py:929`).
5. **SSE fan-out**: single shared queue per execution; concurrent consumers *compete* (each frame reaches exactly one); any consumer disconnect pops the shared buffer, permanently starving survivors (`flux/server.py:498,604-605`).

Tests verify this policy holds under load and quantify where each stage's limit sits. Divergence from this table is itself a finding.

## 1. Harness

`tests/perf/` with marker `perf`; excluded by default via conftest opt-in gate (`FLUX_PERF=1` or a `-m` expression selecting `perf`); runnable via `FLUX_PERF=1 poetry run pytest tests/perf -m perf`. Components:

Harness helper modules live under `fixtures/harness/` — the pre-commit `name-tests-test` hook already excludes `tests/*/fixtures/`, and non-test helpers anywhere else under `tests/` fail its naming check.

- **`fixtures/harness/env.py`** — self-contained server + worker subprocess lifecycle (free port, throwaway SQLite, auth disabled, bootstrap token + encryption key seeded), HTTP helpers (register / run / status / cancel), Docker availability probe. Deliberately duplicates the e2e pattern instead of importing it.
- **`fixtures/stream_workflow.py`** — parameterized synthetic workflow: `N` frames at `R` events/s of `S` pad bytes; payload `{"i": n, "t": <wall ts>, "pad": "x"*S}`; optional jitter; optional startup delay so consumers can attach before frame 0. A second variant runs under `runner="docker-airgapped"` for the sealed tests.
- **`fixtures/harness/consumer.py`** — subscribes via `GET /executions/{id}?mode=stream` (httpx-sse), runs in a thread, records per-frame `(i, t_sent, t_recv)`; computes delivered count, loss, e2e latency and inter-frame distributions (p50/p95/p99); notes terminal event. Supports a throttle mode (bounded read rate) for T5.
- **`fixtures/harness/dbmeter.py`** — differential SQLite meter (read-only side-channel): per-table row counts, `execution_events` rows per execution grouped by type, DB file size. Postgres variant for T3/T7 repeats uses the same interface over `pg_stat_statements` deltas.
- **`fixtures/harness/sampler.py`** — psutil thread: CPU%, RSS for server and worker PIDs at 1 Hz → in-memory series + CSV.
- **`fixtures/harness/report.py`** — machine spec capture (CPU model, cores, RAM, disk), writes `results/<test>/<run>.json`, regenerates the top-level `RESULTS.md` table from all result files.

Discipline: 10 s warmup before measurement windows; every figure = median of 3 runs; machine spec + localhost HTTP RTT recorded into every JSON. Environments: the backend comes from `FLUX_PERF_DATABASE_URL` — SQLite by default for local iteration, **PostgreSQL in CI** (`perf-postgres` job in `.github/workflows/pull-request.yml`; production runs Postgres, so correctness gates must hold there). Full characterization figures: SQLite + Postgres for T3/T7, poll dispatch (primary) with one event-mode repeat of T2.

## 2. Tests

### T0 — Persistence verification (run first; gates everything)
Procedure: run `stream_workflow` twice (N=100 and N=5,000 frames, 200 ev/s) against a live server + worker, consumer attached before frames flow; dbmeter snapshots around each run; inspect the persisted event log per execution.
Assert:
- consumer received progress frames (the stream demonstrably works);
- **zero `TASK_PROGRESS` rows** anywhere in `execution_events`;
- persisted event-row count for the N=5,000 execution **equals** the N=100 execution's (row count independent of frame count);
- detailed status DTO contains no `TASK_PROGRESS` events.
Replay-skip is covered by `tests/flux/test_progress_durability.py` (inline); distributed reclaim is out of T0's scope.
If it fails: stop; write `findings/T0_persistence.md` with the offending path and a proposal, then continue the suite against a local patch clearly marked experimental.

### T1 — Single-child protocol ceiling
Sealed child (`docker-airgapped`; degrade to `subprocess` with `sealed=false` recorded when Docker absent), tight `progress()` loop, no rate limit; frame sizes 150 B and 2 KB (separate runs).
Record: max sustained *delivered* events/s over 60 s; the **loss-onset curve** (offered vs delivered at stepped offered rates); worker CPU per 1k ev/s; which stage drops first (worker queue depth vs server queue vs ingest-discard, inferred from counters).
Gate: ≥ 1,000 ev/s **delivered without loss** at 150 B (10× worst realistic token rate). Loss above the onset rate is expected policy, not failure — report the curve.

### T2 — Worker fan-in
8 concurrent sealed children × 100 ev/s × 120 s through one worker (matches max mesh slots).
Record: aggregate delivered rate (target 800 ev/s sustained); per-stream inter-frame p50/p99; **interference metric**: stream A's p99 with siblings idle vs saturated.
Gate: no loss at this offered rate; interference delta p99 ≤ 10 ms. Repeat once in event-dispatch mode (delivery path parity).

### T3 — Server aggregate knee
Synthetic workers (no containers): each registers via `POST /workers/register` with the bootstrap token, real execution rows are created, consumers subscribe **first**, then a direct client loop POSTs progress batches (≤50 frames each, matching the real flusher). Ramp offered load 1k → 3k → 10k → 30k ev/s, 3 min per step, one server process.
Record: delivered/offered ratio, server CPU/RSS, consumer p99, dbmeter deltas (T0 under pressure) at each step; identify the knee (last step with ratio ≥ 0.99 and CPU ≤ 70%).
Gate: knee ≥ 10k ev/s on SQLite (beta reality is ~3k: 25 nodes × 4 slots × 30 tok/s). Characterize past-knee behavior: graceful (latency grows) vs collapse (loss/disconnects) — either is reportable; unknown is not. Watch for the SSE generator's per-wakeup task churn (`flux/server.py:529-541`) as the likely first limit.

### T4 — End-to-end latency delta (the customer number)
Sidecar: **deterministic SSE token emitter** (seeded, fixed inter-token schedule) by default; real `llama-server` (any small GGUF) as an opt-in flavor (`FLUX_PERF_T4_LLAMA=1`). Sealed child tees sidecar SSE lines into `progress()`. 1 stream, then 8.
Record: inter-token latency consumed via Flux vs consumed directly from the sidecar socket (identical token schedule both sides). Delta histograms.
Gate: added p50 ≤ 30 ms, p99 ≤ 150 ms.

### T5 — Slow consumer / head-of-line (policy verification)
8 streams on one worker; consumer of stream 1 throttled to 1 KB/s; siblings full-rate for 120 s.
Pre-registered expectation (§0b): stream 1's server queue fills to 10,000 then drops newest; RSS bounded; siblings unaffected (per-execution queues).
Record: worker + server RSS over time; sibling p99; stream 1's delivered frames and drop pattern.
Gate: bounded memory and unaffected siblings. Divergence from the expected policy → findings doc.

### T5b — Multi-consumer semantics (documentation test, expected-fail-ish)
Two concurrent consumers on one execution mid-stream, then disconnect one.
Pre-registered expectation from code: frames split nondeterministically between consumers; after either disconnects, the survivor receives nothing further (shared buffer popped at `flux/server.py:605`).
Deliverable: `findings/T5b_multi_consumer.md` documenting observed behavior + proposal (per-consumer fan-out queues). No gate — this bounds current semantics so T7 and users don't rediscover it.

### T6 — Violence
(a) Fast-cancel all 8 streams at full rate: cancel-flush burst (`flux/worker.py:1334-1350`) measured and documented; **no frames after the burst completes**; executions reach correct terminal state; RSS returns to baseline ≤ 5 s.
(b) `kill -9` the worker mid-stream: server marks executions terminally failed; measure detection latency (this is the gateway-retry trigger).
(c) Drop and restore the worker's connection mid-stream (proxy-level): document frame fate — flushed, lost (expected: whole batches dropped with warnings), or duplicated — and assert no protocol wedge.

### T7 — Soak
60 min at 3k ev/s (beta load), mixed frame sizes, consumers churning **strictly sequentially** (disconnect fully before reconnect; never two consumers on one execution — see T5b).
Record: RSS drift (worker + server) — gate ≤ 10% after first 10 min; event-store size delta — gate ≈ 0 (T0's long-run form); p99 drift first-10-min vs last-10-min ≤ 20%; event-log length per execution alongside p99 (checkpoint wakes re-hydrate the full context — `flux/server.py:508-515` — so log length is the natural confounder to rule in/out).

## 3. Deliverables

1. `tests/perf/` suite, each test runnable standalone, honest skips when Docker absent (T1/T2/T4/T6a degrade to non-sealed variants with `sealed: false` recorded in results).
2. `RESULTS.md`: summary table (test, gate, measured, pass/fail, environment) + the headline numbers: T0 verdict, per-child ceiling + loss onset, server knee, T4 delta p50/p99, T5 policy verification verdict, T5b semantics statement.
3. `findings/*.md` for any gate failure or surprising behavior, each ending in a concrete proposal sized as an issue. T5b's findings doc is expected regardless.
4. No changes under `flux/` on this branch. Perf suite must not import from `tests/e2e` (keep it liftable). `pyproject.toml` marker registration + version bump are in scope.

## 4. Out of scope

Engine throughput itself (Tessera M1 measures that), multi-server topologies, Postgres tuning beyond defaults, network-level TLS overhead (localhost only), distributed replay/reclaim verification (unit-covered inline), and any fix implementation — findings propose, humans dispose.
