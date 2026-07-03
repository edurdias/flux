# Production Readiness Review — Scale to Thousands of Workers & Transient Workflows for AI Mesh

**Date:** 2026-07-02 · **Scope:** dispatch/claim path, persistence, worker runtime, security/operations, and a design assessment for transient/stateless workflows supporting an AI-mesh use case.

## Executive summary

Flux has a well-factored core — event-sourced `ExecutionContext`, pessimistic `SELECT … FOR UPDATE SKIP LOCKED` claiming, Alembic migrations, HMAC-signed pickles, deny-by-default anonymous policy, cross-replica scheduler singleton — and a lot of recent hardening has landed. **It is production-credible today at the scale of tens of workers and modest execution rates. It is not yet ready for thousands of workers or for high-frequency agent-to-agent traffic**, for three structural reasons:

1. **Dispatch is a per-worker server-side poll loop, not push.** Each connected worker costs the server ~10–12 DB queries/sec even when idle, against a 15-connection pool and a ~32-thread executor. The system saturates in the low hundreds of workers per replica.
2. **Durability is per-task full-context checkpointing.** Every task completion re-ships the entire event history (O(K²) payload over a K-task workflow), checkpoints are fire-and-forget with no retry and no HTTP timeout, and there is no retention story for `execution_events`.
3. **Every execution — including every agent LLM/tool step — pays full durable-workflow cost.** A trivial 1-task workflow costs ≥6 persisted events, 5 locking DB transactions, and ~5 HTTP hops. An agent loop multiplies that per iteration. There is no transient/ephemeral execution mode — but the seam for one already exists and is clean (see §6).

The recommended sequencing: fix the P0 correctness/robustness items (§8), replace polling dispatch with event-driven dispatch (§7), then add a `durability="transient"` execution mode that reuses the existing checkpoint-callable seam. Transient mode is the single highest-leverage change for the AI-mesh use case: it removes ~all DB work from the hot path and converts an agent hop from ~5 round-trips + 5 transactions to 1 relay round-trip.

---

## 1. What is already solid

Credit where due — these are done correctly and should not be re-litigated:

- **Double-claim prevention.** Dispatch and claim use `with_for_update(skip_locked=True)` with worker-name pinning and atomic state flips (`flux/context_managers.py:286,450-521`). No double-claim on PostgreSQL.
- **Cross-replica scheduler singleton** via `pg_try_advisory_lock` per cycle (`flux/schedule_manager.py:386-428`), and idempotent cross-replica orphan reclaim from persisted heartbeats.
- **Migrations.** Real Alembic chain with a frozen baseline, advisory-lock-guarded concurrent upgrade, and legacy `create_all` adoption (`flux/migrations/runner.py`).
- **Deterministic replay identity.** Task IDs and event IDs are content-addressed SHA-256 (`flux/task.py:142-157`, `flux/domain/events.py:96-112`), which makes event dedup and replay short-circuiting stable across processes.
- **Security fundamentals.** Constant-time bootstrap-token compare, static-parse-before-exec authorization ordering on registration, HMAC integrity signing of at-rest pickles (fail-closed when a key is set), worker identity binding on worker endpoints, secrets-batch allow-list enforcement.
- **Terminal-state write guard.** `_accept_state_write` (`flux/context_managers.py:41-58`) prevents a stale worker from resurrecting a completed/cancelled execution.

---

## 2. Scalability to thousands of workers

Findings ranked by severity. The first three interact: together they cap a replica at roughly 100–300 connected workers before pool exhaustion and executor queueing set in.

### S1 (Critical) — Per-worker 0.5s server-side poll loop

`GET /workers/{name}/connect` looks like SSE push, but the server-side generator (`flux/api/worker_routes.py:260-388`) is a poll loop per connected worker: each tick calls `next_execution` + `next_cancellation` + `next_resume` (5–6 queries), and even with the event-based wakeups the fallback timeout is hard-coded to **0.5s** (`worker_routes.py:261`). An idle worker costs ~10–12 queries/sec:

| Connected workers | Idle dispatch queries/sec |
|---:|---:|
| 100 | ~1,000–1,200 |
| 1,000 | ~10,000–12,000 |
| 5,000 | ~50,000–60,000 |

Worse, new-work wakeups broadcast on a single shared `_work_available` event (`worker_routes.py:352-356`) — a **thundering herd**: one enqueued execution wakes every connected worker's loop, each of which runs the full match query stack and all but one loses the `SKIP LOCKED` race.

**Fix direction:** event-driven dispatch. Per-replica in-memory work queues fed by PostgreSQL `LISTEN/NOTIFY` (or a lightweight fan-out table poll by *one* dispatcher task per replica, not one loop per worker), with targeted per-worker wakeups instead of broadcast, and the 0.5s fallback raised to a slow safety-net interval (10–30s).

### S2 (Critical) — 15-connection DB pool and 32-thread executor ceiling

- PostgreSQL pool defaults: `pool_size=5`, `max_overflow=10` → **15 connections per replica** (`flux/config.py:166-173`, `flux/models.py:120-125`), `pool_timeout=30s`. The per-worker poll loops alone exceed this in the low hundreds of workers; requests then block 30s and error.
- All sync-SQLAlchemy work is offloaded via `asyncio.to_thread`, which uses the default loop executor (~`min(32, cpu+4)` threads; never overridden anywhere). That is the global concurrency ceiling for *all* DB operations — dispatch, checkpoints, admin routes.

**Fix direction:** make pool size and a dedicated, explicitly sized `ThreadPoolExecutor` configurable and sized together (executor ≤ pool); long-term, S1 removes most of the demand. Consider async SQLAlchemy for the hot paths eventually, but sizing + event-driven dispatch buys the most for the least risk.

### S3 (High) — `_is_least_loaded_worker` full aggregate per dispatch attempt

Every `next_execution` call first runs a `GROUP BY` aggregate over **all** active executions (`flux/context_managers.py:338-369`) with no per-worker filter. Cost is O(workers × active_executions) per 0.5s across the fleet, and every idle tick pays it just to bail out.

**Fix direction:** maintain an `active_executions` counter on the `workers` row (incremented/decremented in the same transactions that flip execution state), or compute load in the single per-replica dispatcher (S1) instead of per worker per tick.

### S4 (High) — Single-process server; per-replica in-memory state breaks multi-replica semantics

- Uvicorn runs one process, one event loop, no `workers=` (`flux/server.py:196-202`). SSE loops, the reaper, orphan reclaim (which deliberately runs *on* the event loop to set asyncio Events), and the scheduler all share it. A large stale-worker sweep blocks everything.
- Worker registry, SSE routing, round-robin index, execution-event signaling, and progress buffers are per-replica dicts (`flux/server.py:113-129`). Consequences with >1 replica: `GET /workers` shows only the local slice (`worker_routes.py:631-665`); a checkpoint arriving on replica B never wakes a sync/stream caller waiting on replica A (it falls back to a 30s timeout re-poll, `flux/server.py:449`); work enqueued on replica B doesn't wake workers connected to replica A (0.5s poll masks it — until S1 removes the poll).

**Fix direction:** make the DB (or LISTEN/NOTIFY) the cross-replica signal plane: `GET /workers` reads the `workers` table; checkpoint completion NOTIFYs so any replica's sync waiters wake; document sticky routing for SSE as an interim requirement.

### S5 (Medium) — Heartbeat/reaper costs

- `workers.last_seen_at` is **unindexed** (`flux/models.py:484`); `find_stale` full-scans the workers table every 10s per replica (`flux/worker_registry.py:189-201`).
- Heartbeat pongs are one targeted UPDATE-commit per worker per 10s — ~500 tiny write transactions/sec at 5,000 workers, competing for the same pool. Batch or lengthen the interval at scale.
- Mass eviction of a busy worker unclaims its executions one-by-one, unpaginated, on the event loop (`flux/server.py:595-636`).

### S6 (Medium) — No connection/backpressure limits on the worker plane

Unbounded workers may open `/connect`; each permanently consumes an asyncio task and poll loop. Rate limiting (slowapi) exists but is not applied here, and is keyed by remote address only. Add a max-connected-workers guard and registration rate limits (see also SEC1).

---

## 3. Durability & correctness

### D1 (Critical) — O(K²) checkpoint amplification

Every checkpoint serializes the **entire** context including all accumulated events (`flux/domain/execution_context.py:518-522`; the worker sends `to_dict()` at `flux/worker.py:677-685`). Over a K-task workflow that is ~K²/2 event objects shipped, with every large task output dill-re-serialized on each subsequent checkpoint. Server-side, dedup re-reads the `(event_id, type)` set per checkpoint (`flux/context_managers.py:636-644`) — O(K²) read work — plus a `FOR UPDATE` lock on the execution row each time.

**Fix direction:** delta checkpoints. The worker knows which events are new since the last acknowledged checkpoint; send only those (keep a full-resync path for recovery). This also fixes the payload-size problem (M5 below) and most of the dedup cost.

### D2 (Critical) — Checkpoints are fire-and-forget, coalesced, unretried, and untimed

- Intermediate checkpoints run as background tasks, and each new checkpoint **cancels any still-pending one** (`flux/worker.py:644-659`) — under rapid task completion, nothing is durably persisted until a lull or the terminal checkpoint.
- No retry on failure (`flux/worker.py:661-666,701-704`); events live only in worker memory between successful checkpoints.
- `default_timeout` defaults to `0` → `httpx.AsyncClient(timeout=None)` (`flux/worker.py:100`): claim/checkpoint/pong can hang forever on a black-holed connection. A workflow that completes during a server blip may never persist COMPLETED — the reaper eventually unclaims it and it **re-executes from scratch**.

**Fix direction:** bounded retry with backoff for checkpoints (mandatory for terminal ones), an explicit default HTTP timeout, and an outbound event buffer that survives coalescing (coalesce *sends*, never *events*).

### D3 (High) — At-least-once without fencing → split-brain on partition

Eviction resets RUNNING executions to CREATED and re-dispatches (`flux/context_managers.py:544-575`). A network-partitioned-but-alive worker keeps executing the original; nothing fences its subsequent checkpoints from interleaving with the new owner's (`_accept_state_write` only guards terminal states). Two live workers can run the same execution concurrently.

**Fix direction:** a claim-generation fencing token: bump a `claim_generation` column on every (re)claim, embed it in the execution token/checkpoint, and reject checkpoints bearing a stale generation.

### D4 (High) — Unbounded growth: no retention for executions/events

There is no pruning, TTL, archival, or partitioning for `executions`/`execution_events` anywhere. Events carry inline pickled BLOB values. At AI-mesh rates (each agent LLM/tool step = 2 events + a checkpoint) this is millions of rows per hour. **Fix direction:** a retention job (age- and state-based), and per-workflow retention overrides. Transient mode (§6) removes most of the volume at the source.

### D5 (Medium) — Full-history load and unpickle on every read

`ContextManager.get()` materializes and dill-unpickles every event row, every time (`flux/models.py:645`), and it is called on status polls, claims, sync-wait loop iterations, unclaims, and the secrets batch. Long executions make their own status checks progressively more expensive. **Fix direction:** state/summary reads that skip event hydration (a `summary()` exists on the context — the server should have a query-level equivalent), and lazy event loading.

### D6 (Medium) — Replay determinism is assumed, not enforced or documented

Resume re-executes the entire workflow body; only `@task` results short-circuit from the log (`flux/task.py:230-268`). Non-task I/O, `datetime.now()`, `random`, or wall-clock branching in a workflow body silently corrupts replay and can desync task IDs. This is a standard event-sourcing contract, but it appears nowhere in user-facing docs. Document it prominently; consider a debug-mode replay-divergence detector.

### D7 (Medium) — SQLite/PostgreSQL behavioral drift

On SQLite: `SKIP LOCKED` is a no-op (double-claim protection silently absent), FK cascades are unenforced (no `PRAGMA foreign_keys=ON`), and the single writer serializes checkpoints. SQLite must be positioned explicitly as dev/single-node only; consider emitting a startup warning when a worker fleet >1 registers against SQLite.

---

## 4. Worker runtime robustness

### W1 (Critical) — No isolation, no per-workflow timeout, no concurrency cap

Workflow source is `exec()`'d and run on the worker's single event loop (`flux/worker.py:594,614`). One CPU-bound or sync-blocking workflow freezes the SSE reader, heartbeat pongs, and checkpoints for every co-tenant execution — missed pongs then get the *whole worker* evicted and all its executions mass-orphaned. There is **no per-worker max-concurrency knob**: every dispatch unconditionally `create_task`s (`flux/worker.py:259`); server-side least-loaded gating is a balancing heuristic, not a cap. Per-task `timeout` uses `asyncio.wait_for` and cannot bound sync code.

**Fix direction (ordered):** (1) `max_concurrent_executions` per worker, advertised at registration and respected by dispatch — also the backpressure primitive the AI mesh needs; (2) optional subprocess execution mode per workflow for untrusted/CPU-heavy code, with a real wall-clock kill; (3) worker self-health: detect event-loop starvation and stop claiming.

### W2 (High) — No graceful draining

Shutdown cancels the main task; nothing awaits `_running_workflows` or pending checkpoints (`flux/worker.py:123-134`). Rolling deploys abandon in-flight executions to the ~60s reaper and full re-execution. **Fix:** a drain phase — stop claiming, await running executions up to a configurable deadline, flush terminal checkpoints, deregister.

### W3 (Medium) — Token lifecycle gaps

Only the SSE connect path recovers from 401/403 by re-registering (`flux/worker.py:174-179`); checkpoint/claim/pong/authorize have no 401 recovery, so a mid-execution key revocation silently drops durability. Registration **revokes all prior keys** for the principal (`flux/api/worker_routes.py:128`), so two workers sharing a name thrash each other's tokens. **Fix:** 401→re-register→retry-once wrapper on all worker HTTP calls; reject duplicate live registrations for the same name instead of rotating keys.

### W4 (Medium) — Module cache staleness and leaks

Cache key is `namespace:name:version` with a 300s TTL (`flux/worker.py:540-597`): re-registering different source under the same version serves stale code for up to 5 minutes, and `sys.modules` entries for old versions are never freed (unbounded growth on version churn). **Fix:** include a source hash in the cache key; add an LRU size cap and evict `sys.modules` alongside.

---

## 5. Security & operations

### SEC1 (Critical) — No rate limiting on authentication paths

slowapi is wired up but only `/auth/test-token` is limited. `POST /workers/register` — gated by a single fleet-wide bootstrap secret — and every API-key/JWT authentication are brute-forceable at full request rate (`flux/api/worker_routes.py:87-101`, `flux/security/auth_service.py:85-102`). **Fix:** default limits on register/auth endpoints, keyed with `X-Forwarded-For` awareness.

### SEC2 (Critical) — Plaintext transport; secrets shipped decrypted over HTTP

The server binds plain HTTP with no TLS support in-app (`flux/server.py:196`), Docker/compose default to `http://`, and `/workers/{name}/secrets/batch` returns decrypted secret values (`flux/api/worker_routes.py:580`). Encryption-at-rest is good; in-transit is entirely delegated to an undocumented external layer. **Fix:** first-class TLS config (or at minimum a prominent, hard requirement in deployment docs), and consider envelope-encrypting secret payloads to a worker-held key.

### SEC3 (High) — Bootstrap token is a fleet-wide master secret; worker keys are immortal

One shared bootstrap token grants registration under **any** worker name (names are caller-controlled), yielding the `worker` role (read all configs/executions, claim work → receive secrets). Worker API keys are minted with no expiry and no `last_used_at`; principals are never garbage-collected on eviction, so the principals table grows unbounded with churn (`flux/api/worker_routes.py:112-133`). Execution tokens are 7-day HS256 JWTs with no revocation list (`flux/security/execution_token.py:66-143`). **Fix:** key expiry + refresh for workers, principal GC tied to worker pruning, shorter execution-token TTL (they're scoped to one execution — hours, not a week), and per-registration one-time join tokens as an upgrade path from the shared bootstrap secret.

### SEC4 (High, accepted-by-design) — `exec()` of registered source is RCE by contract

Registration → `exec` on server (schedule extraction, enrich) and on every worker. The permission gate ordering is correct and SECURITY.md documents the trust boundary, but the only control is "restrict `workflow:*:register`". With auth disabled + `allow_anonymous=true`, registration is anonymous RCE. This is an inherent property of the programming model; it needs to stay loudly documented, and W1's subprocess isolation is the mitigation path.

### SEC5 (Medium) — Operational hygiene

- **Metrics cardinality:** `worker_name` (caller-controlled) is a Prometheus label on four worker metrics (`flux/observability/metrics.py:258-278`) — unbounded cardinality and a DoS vector at thousands of churning workers. Drop or hash the label.
- **Probes:** `/health` conflates liveness and readiness (a DB blip can flap pod restarts); `/metrics` requires an admin-scoped credential, which is friction for scrapers. Add `/ready`; consider a scrape-scoped role.
- **Body limits:** workflow upload is capped, but checkpoint, run-input, and progress bodies are not — unbounded dill payloads into memory. Add a global body-size middleware.
- **Late config failure:** missing `encryption_key` silently disables pickle signing and surfaces only at first secret use; missing `execution_token_secret` fails at first mint. Validate at startup.
- **Defaults:** auth disabled and CORS `*` by default are acceptable for a dev-first tool but deserve a one-page "production checklist" doc (auth on, key set, TLS, PostgreSQL, pool sizing).
- **Pagination:** `GET /workers`, principals, and roles listings are unpaginated.
- **Dispatch payload rebuild:** compiled dispatch payloads are built per event; fine today, worth caching per workflow version at mesh rates.

---

## 6. Transient/stateless workflows for the AI mesh

### Why the current model can't serve a mesh

Measured minimum cost of one trivial distributed execution today:

| Cost | Count | Where |
|---|---|---|
| Persisted events | 6 (SCHEDULED, CLAIMED, STARTED, TASK_STARTED, TASK_COMPLETED, COMPLETED) | `context_managers.py`, `workflow.py:132-149`, `task.py:280-393` |
| Locking DB transactions | 5 (create, schedule, claim, 2× checkpoint) | each with `FOR UPDATE` + dedup SELECT |
| HTTP round-trips | ~5 (run, SSE push, claim, 2× checkpoint) | +1 authorize POST per task with auth on |

An agent invocation multiplies this: **each LLM call and each tool call is a nested `@task`** (`flux/tasks/ai/agent_loop.py:88-101`, `flux/tasks/ai/tool_executor.py:238-309`) — 2 events + 1 checkpoint each. A modest 3-iteration agent with 2 tools/iteration ≈ 10 tasks → ~20 events + ~10 checkpoints. And **every cross-workflow call is a full new durable execution**: `flux/tasks/call.py` POSTs `/workflows/{ns}/{name}/run/sync`, and `workflow_agent()` (`flux/tasks/ai/delegation.py:229-261`) does the same. Completion visibility for the caller is a held-open sync request that re-reads the full context from the DB on every wake (30s timeout loop), SSE, or polling — there are no webhooks. Chain three agents and the floor is dozens of transactions per user request.

### The seam already exists

This is the encouraging part — the architecture is unusually well-prepared for a transient mode:

1. **`ExecutionContext._checkpoint` is an injected callable defaulting to a no-op** (`flux/domain/execution_context.py:50,426-429`). The context has zero DB knowledge; `task.py`/`workflow.py` call `ctx.checkpoint()` blindly. A transient execution is literally a context whose checkpoint stays the no-op — events still accumulate in memory, so within-run replay/retry semantics keep working. **Zero changes to the task/workflow engine needed.**
2. **`ContextManager` is an ABC with a single hard-coded implementation** (`flux/context_managers.py:61,156-158`) — an `InMemoryContextManager` slots in cleanly for server-side lookups of transient executions.
3. The context already nulls its checkpoint callable on pickle (`execution_context.py:499-513`) — it is designed to travel without its persistence hook.

### Proposed design: `durability="transient"`

**API surface.**

```python
@workflow.with_options(durability="transient")   # default: "durable"
async def route_request(ctx: ExecutionContext[AgentRequest]): ...
```

Also accept a per-invocation override on the run endpoint (`POST /workflows/{ns}/{name}/run/sync?durability=transient`) so the same workflow can serve both modes; `call()` and `workflow_agent()` grow a matching `durability=` passthrough.

**Execution path (server-relayed, phase 1).**

1. `POST …/run/sync` with transient: **no `executions` row, no catalog FK write, no events**. The server assigns an ID, registers it in an in-memory transient registry (TTL-bounded), and pushes the dispatch frame down an eligible worker's existing SSE stream — same matching, no `next_execution` DB query (pick from the in-memory registry of connected workers; this rides on the S1 dispatch rework).
2. The worker executes with the no-op checkpoint and POSTs a single **result** message (output or exception) to a new `/workers/{name}/result/{execution_id}` endpoint — no claim round-trip (the SSE push *is* the claim; transient work is never re-dispatched, so claim races don't exist).
3. The held-open sync request is completed from memory. Net cost: **1 client round-trip + 1 SSE push + 1 result POST, 0 DB transactions.**
4. Failure semantics are **at-most-once with error propagation**: worker death or TTL expiry fails the caller's request with a structured error; the *caller* retries. No reaper, no unclaim, no replay.

**Hard constraints (fail loudly, don't degrade silently).**

- `pause()`, `requires_approval`, and cross-worker resume are inherently durable — they require rows another process can act on (`flux/task.py:474-593` writes approval rows via its own manager, bypassing the checkpoint seam). A transient execution hitting any of these must raise a clear error at that point — and registration should reject `durability="transient"` combined with `schedule=` or approval-gated tasks where statically detectable.
- Within-run retry/fallback/rollback keep working unchanged (they're in-memory event mechanics).
- Transient executions are invisible to `flux execution list`/history by design; emit OTel metrics (count, latency, outcome) so the mesh is still observable in aggregate.

**Phase 2 — mesh fast paths.**

- `call(durability="transient")` becomes the cheap agent-to-agent hop; in-process delegation (`build_delegate`) already avoids the round-trip and stays the first choice when the target lives in the same worker.
- Sticky same-worker scheduling: when a transient sub-call's target workflow is loadable on the calling worker, execute in-process (module cache already holds compiled workflows) and skip the server entirely — this is the true mesh path, with the server relay as fallback and service discovery.
- Optional bounded result cache (in-memory, TTL) so `mode=async` + late `GET /executions/{id}` works for transient runs within the TTL.

**Prerequisites from the rest of this review.** Transient mode shifts pressure from the DB to the worker fleet, so W1 (per-worker concurrency cap + advertised capacity → backpressure with a `429`-style "mesh busy" signal), D2 (HTTP timeouts), and S1 (event-driven dispatch) are prerequisites, not nice-to-haves. Multi-replica relay needs sticky routing or a cross-replica result channel (S4).

### Sizing sanity check

At 1,000 sustained agent requests/sec with 3 transient hops each: today's model would be ~15,000 locking DB transactions/sec plus O(K²) checkpoint traffic — not feasible. The transient model is ~4,000 in-memory HTTP/SSE messages/sec spread across replicas and workers, with the DB touched only by durable workflows. That is an achievable target for a FastAPI/uvicorn fleet once S1/S2 land.

---

## 7. Proposed solution — target architecture, stack, and dependencies

This section turns the fix directions above into one coherent design. The guiding principle: **PostgreSQL stays the only required piece of infrastructure**, the FastAPI/uvicorn/SQLAlchemy stack is kept, and scale comes from changing *access patterns* (push instead of poll, deltas instead of full snapshots, memory instead of rows for transient work) rather than from new middleware.

### 7.1 Target architecture

```
                       ┌───────────────────────── control plane ─────────────────────────┐
   clients             │  server replica 1..R (FastAPI + uvicorn, sticky SSE routing)    │
     │                 │                                                                 │
     │ run/sync ──────▶│  ┌──────────────┐   assign    ┌────────────────────────────┐    │
     │ run/async       │  │  Dispatcher  │────────────▶│ per-worker outbound queues │    │
     │ (durable or     │  │ (1 task/rep.)│             │  (existing SSE streams)    │    │
     │  transient)     │  └──────┬───────┘             └─────────────┬──────────────┘    │
     │                 │         │ batch claim                       │ push frames       │
     │                 │         ▼ (SKIP LOCKED)                     │                    │
     │                 │  ┌─────────────────┐    LISTEN/NOTIFY  ┌────┴───────────────┐   │
     │                 │  │   PostgreSQL    │◀═════════════════▶│ Transient registry │   │
     │                 │  │ durable source  │  (wakeups only,   │ (in-memory, TTL,   │   │
     │                 │  │ of truth + WAL  │   R listeners)    │  sync-wait futures)│   │
     │                 │  └─────────────────┘                   └────────────────────┘   │
     └────────────────▶│  retention job · reaper · scheduler (advisory-lock singletons)  │
                       └──────────────────────────────┬──────────────────────────────────┘
                                                      │ SSE (dispatch) / HTTP (results,
                                                      │ delta checkpoints, heartbeats)
                       ┌───────────────────────────── ▼ ── data plane ───────────────────┐
                       │  worker 1..W (capacity slots, drain, delta-checkpoint buffer)   │
                       │   ├─ in-process executor (async workflows, trusted)             │
                       │   ├─ subprocess pool (isolation="process", wall-clock kill)     │
                       │   └─ mesh fast path: transient sub-calls run in-process when    │
                       │      the target workflow is locally loadable                    │
                       └──────────────────────────────────────────────────────────────────┘
```

Key inversion vs today: **the per-worker server-side poll loops disappear.** Each replica runs exactly one dispatcher task; DB load scales with `replicas × work rate`, not `workers × 2/sec`. Workers' SSE handlers become passive consumers of an in-memory queue.

### 7.2 Component designs

**Dispatcher (replaces the per-worker poll, fixes S1/S3/S6).** One asyncio task per replica. It holds an in-memory view of locally connected workers — labels, resource capabilities, advertised `max_concurrent_executions`, current in-flight count (from dispatch/complete accounting, replacing the `GROUP BY` aggregate). Wakeup sources: a `LISTEN flux_work` notification (fired by a trigger or by the enqueue transaction), a local enqueue, a worker slot freeing up, or a 10–30s fallback tick. On wakeup it claims a **batch** of `CREATED`/`RESUME_SCHEDULED`/`CANCELLING` rows with one `FOR UPDATE SKIP LOCKED LIMIT n` query, matches them against local free slots in memory (label/resource matching moves out of SQL row iteration), flips them to `SCHEDULED` with `worker_name` and a bumped `claim_generation` in one transaction, and pushes dispatch frames onto the target workers' queues. Unmatched rows are released for other replicas. `SKIP LOCKED` makes concurrent dispatchers on other replicas coordination-free. NOTIFY delivery is best-effort by design — notifications are pure wakeups; the DB rows are the truth and the fallback tick covers missed signals. The worker's claim POST is kept as a cheap PK-targeted ack (`CLAIMED`), no scan.

**Checkpoint pipeline v2 (fixes D1/D2/D3).** Add a per-execution monotonic `seq` to events and a `claim_generation` column to executions. The worker keeps a per-execution outbound buffer and high-water mark of the last *acknowledged* seq; each checkpoint POST carries only `events[acked+1:]`, the current state, and the claim generation. The server appends, acks the new high-water mark, and **rejects stale generations** (a fenced-out partitioned worker gets a 409 and aborts its copy). Coalescing applies to *sends*, never to buffered events. Retries use capped exponential backoff (tenacity); terminal checkpoints must succeed before the execution is considered delivered, within the drain deadline. On resync (worker restart, 409, unknown seq) the worker falls back to one full-context POST — today's behavior becomes the recovery path instead of the hot path. All worker HTTP calls get explicit timeouts and a 401 → re-register → retry-once wrapper.

**Transient execution plane (§6, fixes the mesh cost).** A `TransientExecutionRegistry` per replica: `execution_id → {future, worker, deadline}` with a TTL sweeper. `POST /workflows/{ns}/{name}/run/sync` with `durability=transient` skips the `executions` insert entirely, registers the future, and hands the dispatch frame straight to the dispatcher's matching stage (same worker-selection code path, no DB). The worker runs it with the no-op checkpoint and POSTs one result message; the registry completes the future and the held-open request returns. Worker death, disconnect, or TTL expiry fails the future with a structured error — the caller retries (at-most-once). Because both the SSE stream and the sync waiter live on the same replica, no cross-replica channel is needed in phase 1 (sticky routing); phase 2 can add a NOTIFY-relayed result path or the optional Redis backend for replica-crossing calls. `call()` and `workflow_agent()` pass `durability` through; when the target workflow is loadable on the calling worker (module cache), the sub-call executes in-process and never leaves the worker — the true mesh path.

**Worker runtime v2 (fixes W1–W4).** Registration advertises `max_concurrent_executions`; the dispatcher never exceeds a worker's free slots, giving real backpressure (mesh callers get a fast "no capacity" failure instead of a queue). Shutdown gains a drain phase: deregister-from-dispatch → await running executions up to `drain_timeout` → flush terminal checkpoints. The module cache key gains a source hash and an LRU cap with `sys.modules` eviction. A new per-workflow option `isolation="process"` routes execution to a small `concurrent.futures.ProcessPoolExecutor`: the child runs `asyncio.run(wfunc(ctx))`, streams events back over a pipe, and can be killed on a wall-clock deadline — the mitigation for untrusted/CPU-bound code and the `exec()` trust boundary (SEC4), with no new dependency.

**Data layer.** Pool size, overflow, and a dedicated DB `ThreadPoolExecutor` (sized ≤ pool) become config knobs and replace the default 32-thread loop executor. Index `workers.last_seen_at`; batch heartbeat updates per replica (`UPDATE … WHERE name = ANY(…)` every interval). A retention job (advisory-lock singleton, like the scheduler) deletes terminal executions/events past `retention_days` in batches; `execution_events` optionally moves to native monthly partitioning via an Alembic migration for high-volume installs. Status reads get a summary query that skips event hydration.

**Security hardening.** slowapi default limits on `/workers/register` and auth-bearing routes (with `X-Forwarded-For` key function); worker API keys minted with expiry + a refresh endpoint; principal GC tied to worker pruning; execution-token TTL down to hours; startup validation of `encryption_key`/`execution_token_secret`; optional in-app TLS via uvicorn's `ssl_certfile`/`ssl_keyfile` passthrough plus documented terminate-at-ingress guidance; global body-size middleware; `worker_name` dropped from metric labels; `/ready` endpoint separate from `/health`.

### 7.3 Stack decisions

| Layer | Decision | Rationale |
|---|---|---|
| Language/runtime | **Keep** Python 3.12 + asyncio; add optional `uvloop` | No rewrite; uvloop is a drop-in ~2× event-loop win for SSE-heavy replicas and workers on Linux |
| HTTP/app server | **Keep** FastAPI + uvicorn (single process per replica, scale by replicas) | SSE + in-memory dispatcher state make process-per-replica the natural unit; `--limit-concurrency` guards the connection plane |
| Durable store & queue | **Keep** PostgreSQL as the only required infra; `SKIP LOCKED` batch claims + `LISTEN/NOTIFY` wakeups | Postgres-as-queue at batch granularity is a proven pattern; avoids operating a broker; transactional with execution state |
| DB driver | **Migrate `psycopg2` → `psycopg` v3** (`postgresql+psycopg` dialect) | One driver serves sync SQLAlchemy *and* the async `LISTEN` connection; psycopg2 has no async path |
| ORM | **Keep** sync SQLAlchemy behind a right-sized executor; async SQLAlchemy only if profiling demands it later | Sizing + push dispatch removes the pressure that would justify an async migration's risk |
| Signal plane | **LISTEN/NOTIFY first**; optional Redis backend behind a small `SignalPlane` interface | R listeners (replicas), not W (workers); payloads are wakeups only, so the 8 KB/non-durable limits don't bite |
| Isolation | stdlib subprocess pool (`isolation="process"`) | No dependency; real kill semantics; container/K8s handles the outer boundary |
| Message broker (Kafka/RabbitMQ/NATS) | **Explicit non-goal** | Dispatch needs transactional consistency with execution rows, not throughput a broker adds; a broker would double the ops burden for every Flux install |

### 7.4 New dependencies

| Dependency | Where | Required? | Purpose |
|---|---|---|---|
| `psycopg[binary,pool] >= 3.2` | `postgresql` extra (replaces `psycopg2`) | Yes, for multi-node | Async `LISTEN/NOTIFY` listener + unified sync/async driver |
| `tenacity >= 9` | core | Yes | Checkpoint/claim/result retry policies with capped backoff and jitter |
| `uvloop >= 0.21` | new `performance` extra | Optional | Event-loop throughput for server replicas and workers (Linux/macOS) |
| `redis >= 5` | new `mesh-redis` extra | Optional | Alternative signal plane + cross-replica transient result relay beyond LISTEN/NOTIFY scale |

Everything else in the design — subprocess isolation, delta checkpoints, capacity slots, retention, rate limiting (slowapi already present), body-size middleware — uses the stdlib or existing dependencies.

### 7.5 Configuration surface added

```toml
[flux.dispatch]
batch_size = 64            # rows claimed per dispatcher wakeup
fallback_interval = 15     # safety-net tick (s); replaces the 0.5s per-worker poll
max_connected_workers = 2000   # per replica; excess registrations get 503

[flux.workers]
max_concurrent_executions = 16 # advertised capacity; dispatcher never exceeds free slots
drain_timeout = 60             # graceful-shutdown drain deadline (s)
default_timeout = 30           # worker HTTP timeout — no longer 0/None

[flux.database]
pool_size = 20                 # sized with executor_threads; executor <= pool
max_overflow = 20
executor_threads = 16          # dedicated DB thread pool (replaces default loop executor)

[flux.retention]
enabled = true
retention_days = 30            # terminal executions + events
sweep_interval = 3600

[flux.transient]
result_ttl = 120               # seconds a transient result/future may wait
```

### 7.6 Capacity model after the changes

At 5,000 workers across 3 replicas (~1,700 SSE connections each — comfortable for uvicorn with mostly-idle streams):

| Load source | Today | Proposed |
|---|---|---|
| Idle dispatch queries | ~50–60k/s (5–6 per worker per 0.5s) | ~0.2/s (one fallback tick per replica per 15s) |
| Dispatch under load | thundering herd × full match stack per worker | `work_rate / batch_size` batch claims, herd eliminated |
| Heartbeat writes | ~500 single-row commits/s | ~0.3 batched statements/s |
| K-task workflow checkpoint bytes | O(K²) | O(K) (deltas; full snapshot only on resync) |
| Transient agent hop | 6 events, 5 locking txns, ~5 HTTP hops | 0 DB txns, 3 in-memory messages (0 when same-worker in-process) |

The DB write load becomes proportional to durable execution throughput alone — which is exactly what a durable store should be paying for.

### 7.7 Rollout path

Each step is independently shippable and backward-compatible: (1) driver swap psycopg2→psycopg3 and pool/executor knobs — no behavior change; (2) checkpoint v2 with protocol negotiation (workers advertise delta support at registration; server accepts both, full-snapshot remains the recovery path); (3) dispatcher behind a feature flag, per-worker poll kept as fallback for one release, then removed; (4) capacity slots + drain; (5) retention job; (6) transient mode (new surface, additive); (7) subprocess isolation (opt-in per workflow). Steps 1–2 are P0-aligned, 3–5 deliver the thousands-of-workers target, 6–7 deliver the mesh.

---

## 8. Prioritized roadmap

**P0 — correctness & safety (before any scale push)**

1. Checkpoint retry + explicit HTTP timeouts + never-drop event buffering (D2) — silent event loss is the worst current behavior.
2. Delta checkpoints (D1) — removes O(K²) payloads and most server-side dedup cost.
3. Rate limits on `/workers/register` and auth paths (SEC1); startup validation of `encryption_key`/`execution_token_secret` (SEC5).
4. Per-worker `max_concurrent_executions` (W1) and graceful drain (W2).
5. Claim-generation fencing token (D3).
6. TLS story — in-app or as a documented hard requirement — given secrets-in-transit (SEC2).

**P1 — scale to thousands of workers**

7. Event-driven dispatch: one dispatcher per replica + LISTEN/NOTIFY + targeted worker wakeups; kill the 0.5s per-worker poll and the broadcast herd (S1).
8. Pool + executor sizing knobs, sized together (S2).
9. Replace the least-loaded aggregate with maintained per-worker counters (S3).
10. Index `workers.last_seen_at`; batch heartbeat writes (S5).
11. Cross-replica signal plane: DB-backed `GET /workers`, NOTIFY-driven sync-waiter wakeups (S4).
12. Retention/pruning job for executions/events (D4); summary reads without event hydration (D5).
13. Worker key expiry + refresh, principal GC, shorter execution-token TTL (SEC3); bounded metrics label cardinality (SEC5).

**P2 — transient mode & AI mesh**

14. `durability="transient"`: no-op checkpoint + in-memory transient registry + server-relayed sync execution (§6, phase 1).
15. `call()`/`workflow_agent()` transient passthrough; sticky same-worker in-process execution (§6, phase 2).
16. Subprocess isolation mode for untrusted/CPU-bound workflows (W1); module-cache source-hash keys + LRU (W4).
17. Production deployment guide: PostgreSQL-only for fleets, replica topology, probes (`/ready`), pool sizing, security checklist.

---

## Decisions log

- **2026-07-03 — PostgreSQL-only for multi-node.** Multi-node deployments require PostgreSQL; SQLite remains fully supported for dev and single-node inline use. The dispatcher, LISTEN/NOTIFY signal plane, and fencing all assume PG semantics. Add a startup warning when more than one worker registers against SQLite.
- **2026-07-03 — Transient semantics: at-most-once at the workflow level.** Transient executions are never re-dispatched by the server; worker death or TTL expiry returns a structured error and the caller retries. Task-level retry/fallback/rollback inside a transient run is unchanged (in-memory event mechanics are kept, only persistence is skipped). Pause, approval gates, and schedules on transient workflows are hard errors.
- **2026-07-03 — Dependency posture.** fastmcp floored at 2.14.2 (clears the fixable advisories); the 3.2.0-only advisories are accepted as unused-feature risk until the tracked fastmcp 3.x migration; diskcache advisory accepted (transitive, no fixed release).

---

*Methodology: five parallel code-level reviews (dispatch/claim path, persistence, worker runtime, execution-overhead/transient seams, security/ops) over the repository at commit `305962c`, consolidated and de-duplicated. File/line references are to that commit.*
