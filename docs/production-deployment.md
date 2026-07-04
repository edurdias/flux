# Production Deployment Guide

How to run Flux in production: the hardening checklist, multi-replica
topology, worker-fleet operations, and the configuration that matters at
scale. Defaults are development-friendly; production requires the explicit
choices below.

## The checklist

Every item here maps to a failure mode observed in the production-readiness
review (`docs/production-readiness-review.md`).

1. **PostgreSQL, not SQLite.** SQLite is single-node only — one server plus
   one colocated worker, or inline `workflow.run()`: `SELECT … FOR UPDATE
   SKIP LOCKED` (claim safety) and FK cascades are silently unenforced
   there, and the server logs a warning when a second worker registers
   against it. Set `FLUX_DATABASE_URL=postgresql://…`. PostgreSQL 14+
   is expected; the driver is psycopg v3 (`pip install 'flux-core[postgresql]'`).
2. **Enable authentication and set the secrets.** With auth off, every
   caller is the anonymous admin. The server refuses to start when auth is
   enabled without these two values (they otherwise fail mid-traffic):
   ```
   FLUX_SECURITY__AUTH__ENABLED=true
   FLUX_SECURITY__EXECUTION_TOKEN_SECRET=<random 32+ bytes>
   FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY=<random 32+ bytes>
   ```
3. **Terminate TLS in front of every replica.** Flux serves plain HTTP.
   Bearer tokens, worker session keys, pickled payloads, and decrypted
   secrets (`/workers/{name}/secrets/batch`) all travel over this channel —
   an ingress/load-balancer with TLS is a hard requirement, not an option.
4. **Event dispatch mode.** `FLUX_DISPATCH__MODE=event` runs one dispatcher
   per replica with LISTEN/NOTIFY wakeups and batch `SKIP LOCKED` claims.
   The default `poll` mode is the legacy per-worker query loop and degrades
   superlinearly with fleet size (measured: at 500 workers it saturates the
   DB executor and takes minutes to accept submissions — see the review's
   stress-test table). Poll mode remains for one release as a fallback.
5. **Enable retention.** Execution history grows without bound otherwise
   (every task is a persisted event row):
   ```
   FLUX_RETENTION__ENABLED=true
   FLUX_RETENTION__RETENTION_DAYS=30
   ```
6. **Size the database pool and executor together.** Defaults are
   `database_pool_size=20`, `database_max_overflow=20`,
   `database_executor_threads=16` per replica. Keep executor ≤ pool so DB
   threads never block waiting for a connection, and give PostgreSQL
   `max_connections ≥ replicas × (pool_size + max_overflow) + workers' LISTEN
   connections + headroom`.
7. **Registration rate limit.** `/workers/register` validates the shared
   bootstrap token and is limited to `30/minute` per client IP by default.
   Large fleets restarting behind one NAT need it raised
   (`FLUX_WORKERS__REGISTER_RATE_LIMIT=300/minute`) — or run the server
   behind a proxy that forwards real client IPs (uvicorn `--proxy-headers`).
8. **Probes.** Point liveness at `GET /health` and readiness at
   `GET /ready`. `/ready` performs a database round-trip and returns 503
   when the DB is unreachable — wire it to load-balancer membership, not
   restarts, so a DB blip drains traffic instead of bouncing pods.
9. **Bootstrap token custody.** The worker bootstrap token is a fleet-wide
   join secret: anyone holding it can register a worker (and workers receive
   workflow source and secrets). Set it explicitly
   (`FLUX_WORKERS__BOOTSTRAP_TOKEN`), store it in your secret manager, and
   rotate it deliberately — rotation invalidates the whole fleet's ability
   to re-register until workers get the new value.

## Multi-replica topology

Multiple server replicas coordinate through PostgreSQL; there is no leader
election to configure. What each mechanism relies on:

- **Dispatch**: every replica's dispatcher batch-claims with
  `FOR UPDATE SKIP LOCKED`; replicas never double-assign. New-work wakeups
  travel via `NOTIFY flux_work`; a missed notification is covered by the
  dispatcher's fallback tick (`dispatch.fallback_interval`, default 15s).
- **Scheduler and retention**: fleet-wide singletons per cycle via
  PostgreSQL advisory locks; a dead holder's lock auto-releases.
- **Worker liveness**: heartbeats persist to `workers.last_seen_at`
  (batched, one UPDATE per interval per replica), so any replica's reaper
  can reclaim executions from a dead replica's workers.
- **Sync/stream callers**: a caller held open on replica A is woken by a
  checkpoint landing on replica B via `NOTIFY flux_exec`; the 30s poll
  fallback remains as the safety net.
- **`GET /workers`** reads the workers table — every replica returns the
  same fleet view, with liveness derived from persisted heartbeats.

**Sticky routing requirement.** A worker's SSE stream, its dispatch queue,
and its in-flight execution signals live on the replica it connected to.
Configure the load balancer with connection affinity (source-IP or cookie
stickiness) for `/workers/{name}/connect`, and let workers reconnect through
the same path. Plain HTTP round-robin works for everything else.

## Worker fleet operations

- **Capacity**: workers advertise `FLUX_WORKERS__MAX_CONCURRENT_EXECUTIONS`
  (default 16, `0` = unlimited) at registration; the server never assigns
  beyond a worker's free slots on any dispatch path. With the default
  subprocess runner each concurrent execution is its own process (size
  against memory); with `runner="inprocess"` workflow code shares the
  worker's event loop (size against what the loop can genuinely run).
- **Deploys**: send SIGTERM and let the worker drain — it stops accepting
  work, finishes running executions (up to `FLUX_WORKERS__DRAIN_TIMEOUT`,
  default 60s), flushes terminal checkpoints, then exits. A second signal
  aborts the drain. Give the orchestrator a termination grace period of
  `drain_timeout + 30s`.
- **Auth resolution is cached per replica**
  (`FLUX_SECURITY__AUTH__RESOLUTION_CACHE_TTL`, default 30 s, 0 disables):
  token→identity and principal→permissions lookups — the dominant idle
  database load at fleet scale — are served from memory between changes.
  Role/key/principal mutations invalidate the local replica immediately;
  other replicas converge within the TTL, so a revoked credential can
  outlive revocation there by up to the TTL — keep it short.
- **Credentials rotate themselves**: worker API keys are minted with a TTL
  (`FLUX_SECURITY__AUTH__API_KEYS__WORKER_KEY_TTL`, default 7 days); on the
  first 401 after expiry the worker re-registers and gets a fresh key.
  Principals of workers offline past `offline_ttl` are disabled and their
  keys revoked automatically; a returning worker re-enables on registration.
- **Fencing**: if a partitioned worker is evicted and its executions
  reassigned, its later checkpoints are rejected (stale claim generation)
  and it aborts those local runs — expect `stale-claim` warnings in worker
  logs after partitions heal; they are the mechanism working.

## Runners: where workflow code executes

Each execution runs through a **runner** (Prefect-style). Workers enable
runners via `[flux.workers] runners` (advertised at registration) and pick
`default_runner` for workflows that don't declare one; a workflow can pin
one with `@workflow.with_options(runner=...)` and will only dispatch to
workers advertising it. (As an env var the list is JSON:
`FLUX_WORKERS__RUNNERS='["inprocess","subprocess"]'`.)

Execution-side processes never touch the database: secrets, configs, and
the **approval gate** (`requires_approval`) all resolve through the server
— workers call worker-scoped endpoints, and runner children reach them
through their parent worker's pipe. Only the server (and inline
`workflow.run()` executions, which own their own database) read or write
approval rows.

- **`subprocess` (the default)** — each execution gets its own child
  process. A crash, OOM, or event-loop-blocking call cannot take down the
  worker or co-resident executions; cancellation and drain are enforced
  with SIGTERM → `subprocess_term_grace` → SIGKILL, which works even
  against code stuck in sync C calls; `subprocess_memory_limit` (Linux)
  bounds per-child address space. The child holds **no worker or fleet
  credentials**: its environment is sanitized (the bootstrap token,
  `FLUX_SECURITY__*`, and `FLUX_DATABASE_URL` are stripped), and
  checkpoints, progress, secrets, configs, and approval-gate operations
  all flow through the parent worker over a pipe — the server-facing
  protocol (delta checkpoints, claim fencing, transient suppression) is
  identical to in-process execution. The only credential in the child is
  the short-lived, single-execution token used for `call()` hops. Cost:
  roughly a second of process spawn + import per execution. Note: if the
  worker reads secrets from a `flux.toml` on disk rather than env vars,
  file permissions are your containment boundary — the docker runner
  isolates the filesystem too.
- **`inprocess`** — the workflow runs as a task on the worker's event
  loop. Lowest latency, no isolation: reserve it for trusted, async-clean,
  latency-sensitive workflows — transient mesh hops especially.
- **`docker`** (opt-in) — each execution runs in its own container via
  `docker run -i`, speaking the same stdio protocol, so containers hold no
  credentials either and SIGTERM-based cancellation works unchanged
  (`--sig-proxy`; escalation is `docker kill`). Configure `docker_image`
  with an image that has flux-core installed at a worker-compatible
  version — the child entrypoint and context wire format must match. The
  official image works directly (pin its tag to the worker's flux-core
  version; see DOCKER.md), or build on top of it to add workflow
  dependencies. Optional knobs: `docker_network` / `docker_memory` /
  `docker_cpus` / `docker_extra_args` (volumes, env, `--user`,
  `--cap-drop`). Use it for untrusted code,
  conflicting dependency sets, or filesystem isolation. Workers advertising
  `docker` must have a reachable daemon — the worker fails at startup
  otherwise. **Precompile bytecode in the image**
  (`RUN python -m compileall -q /usr/local/lib/python3.13/site-packages`):
  containers are ephemeral, so without baked `.pyc` files every execution
  re-compiles flux's imports from source — measured, that alone halves
  per-execution latency.

Measured on a single machine (1-task workflow, warm caches, overlayfs;
sequential median / 8-concurrent effective throughput per worker):

| runner | per-execution overhead | 8 concurrent |
|---|---|---|
| `inprocess` | ~0.1 ms | thousands/s (workflow-bound) |
| `subprocess` | ~0.55–0.7 s | ~4.6–5.0 exec/s |
| `docker` (official image: precompiled + tini) | ~1.1–1.6 s (~0.3 s container + imports) | ~2.0–2.5 exec/s |
| `docker` (no `.pyc` in image) | ~3.1 s | ~1.1 exec/s |

Ranges reflect run-to-run variance on a shared host. Concurrency amortizes
the spawn cost — the per-execution *wall* cost at 8 concurrent drops to
~0.2 s (subprocess) and ~0.4–0.5 s (docker).

**Crash semantics follow durability.** If a child dies without reporting a
result (segfault, OOM kill, `os._exit`), a durable execution is *released*
back to the server (fenced by claim generation, pending checkpoints flushed
first) and re-dispatched — deterministic replay resumes from the last
persisted task, so completed tasks do not re-run. A transient execution
fails terminally (`WorkerProcessCrashed`) per its at-most-once contract —
the caller retries.

Since every subprocess execution pays a spawn, size
`max_concurrent_executions` against memory as well as CPU: each concurrent
execution is a full Python process (~50-100 MB baseline plus workflow
memory).

## Transient executions (AI mesh)

Workflows registered with `durability="transient"` keep the normal outer
lifecycle — execution row, dispatch, terminal state, visible in
`flux execution list` — but persist **no task-level state**: the worker
suppresses every intermediate checkpoint and the terminal checkpoint carries
only `WORKFLOW_*` events. Use it for high-frequency agent/mesh workflows
whose intermediate step payloads (LLM inputs/outputs per task) would
otherwise dominate `execution_events` growth. Measured on an 8-task
workflow: 4.0 persisted event rows per execution vs 19.9 durable, with
unchanged latency. Limits:

- Pause and approvals are hard errors (they need replayable task history);
  schedules are rejected at decoration time.
- A retried/requeued transient execution re-runs all tasks from scratch —
  at-least-once for side effects, with no replay short-circuit.
- Works in both dispatch modes and with sync/async/stream callers.
- Pair with `runner="inprocess"` to also skip the per-execution process
  spawn — the lowest-latency configuration for trusted mesh hops.

**Same-worker fast path.** A `call()` whose target is a **transient
workflow object** (not a string reference) executes in-process on the
calling worker: no dispatch round-trip, no execution row, no checkpoints.
Measured: **~2.3 ms median per hop** versus ~526 ms for a server-relayed
transient execution — the true agent-to-agent path. The parent's task
event is the per-hop audit record; aggregate visibility comes from the
`flux_transient_hops_total` / `flux_transient_hop_duration_seconds`
metrics. String references, durable targets, and `mode="async"` always
relay through the server (which owns service discovery and the durable
lifecycle). Disable fleet-wide with
`[flux.workers] transient_fast_path = false`. Note the hop runs inside
the parent's capacity slot and its secret access is audited under the
parent execution.

## Registering workflows is code execution

Workflow registration executes the uploaded Python on the server (metadata
enrichment) and on every worker that runs it. Treat
`workflow:<namespace>:*:register` as a deploy permission: grant it to CI
identities, not humans-at-large, and never enable `allow_anonymous` on a
network where untrusted parties can reach the server.

## Observability

- `GET /metrics` (Prometheus) requires `admin:metrics:read` — provision a
  scrape credential. Worker/schedule names are deliberately not metric
  labels (unbounded cardinality); dashboards aggregate by event/reason.
- OpenTelemetry traces/metrics export via `[flux.observability]` with the
  `observability` extra installed.
- Capacity planning numbers (measured, single 4-core host): see the
  stress-test table in `docs/production-readiness-review.md` §7.6 and
  `scripts/stress_dispatch.py` to reproduce against your own hardware.
