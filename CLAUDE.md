# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

Read `AGENTS.md` alongside this file: it is the shared agent contract (PR flow, version bump, how to
edit the public surface safely). This file describes what the project *is*; `AGENTS.md` describes
*how* to change it. Human contributors start at `CONTRIBUTING.md`.

## Project at a glance

Flux (`flux-core` on PyPI) is a Python distributed workflow orchestration engine. Workflows and tasks
are `async` functions decorated with `@workflow` / `@task`; the runtime persists every state
transition as an `ExecutionEvent`, so executions can be paused, resumed, replayed deterministically,
and dispatched across worker nodes.

- **Python 3.12+** (CI unit matrix covers 3.12–3.14). The pre-commit `pyupgrade` hook targets
  `--py312-plus`; new code must stay 3.12-compatible (PEP 695 syntax is fine; 3.13+ features such as
  PEP 696 type-parameter defaults are not).
- **Poetry** for dependencies and the `flux` console-script entry point. `uv.lock` is gitignored — do
  not introduce uv.
- **Optional extras**: `postgresql` (psycopg v3 — psycopg2 is deliberately not used; it has no async
  LISTEN/NOTIFY for the event-driven dispatcher), `observability`, `ai` (Ollama / OpenAI / Anthropic /
  Gemini). Default install gives SQLite + no telemetry + no LLM providers.

## Common commands

```bash
# Setup
poetry install                                  # base dev environment
poetry install --extras observability           # what CI installs for lint/unit
poetry install --extras postgresql              # adds psycopg v3

# Lint & format — always go through pre-commit, do NOT run ruff/mypy directly
poetry run pre-commit run --all-files           # full sweep (matches CI)
poetry run pre-commit run --files <path>...     # single file(s)

# Tests
poetry run pytest tests/ --ignore=tests/e2e     # unit suite (what CI runs)
poetry run pytest tests/flux/test_worker.py::TestWorker::test_start_registers   # one test
poetry run pytest tests/e2e/ -m "not ollama and not network" -v   # E2E, as CI runs it
poe test-e2e                                    # ALL E2E incl. ollama (needs local Ollama)
poe test-e2e-no-ai                              # E2E minus @pytest.mark.ollama
poetry run pytest -m postgresql                 # PostgreSQL-tagged tests
make test-postgresql                            # full PG suite (docker-compose up + tests + down)
make test-docker                                # docker + airgapped runner tests against a working-tree image
FLUX_PERF=1 poetry run pytest tests/perf/       # perf suite (see tests/perf/PLAN.md)

# Run a server / worker locally
poetry run flux start server                    # FastAPI server + integrated scheduler
poetry run flux start worker                    # worker (auto-named if no arg)
poetry run flux start worker my-worker --server-url http://localhost:8000 \
                              --label gpu=true  # labels enable affinity dispatch
poetry run flux start mcp                       # MCP server exposing workflow tools

# CLI groups (each has --help)
poetry run flux workflow|execution|worker|schedule|secrets|config|agent|db
poetry run flux roles | principals | auth                # security admin
poetry run flux server bootstrap-token                   # print the auto-generated worker token
poetry run flux server admin-key [--rotate]              # print/rotate the first-admin bootstrap key
```

Nested subgroups: `flux worker metadata …`, `flux agent session …`.

### Pytest markers (pyproject.toml)

- `e2e` — autoset on everything in `tests/e2e/`; spawns real server + worker subprocesses.
- `ollama` — needs a local Ollama; auto-skipped in `tests/e2e/conftest.py` when `ollama list` fails.
- `network` — calls live external services; deselected by CI's e2e gate (rate-limit flaky).
- `postgresql` — needs the test PG container (Make targets handle the lifecycle).
- `perf` — opt-in progress-streaming perf tests (`FLUX_PERF=1` or `-m perf`).
- `integration`, `slow`, `asyncio` — exist; rarely the primary filter.

## Architecture

### The two execution paths

1. **Inline** (`workflow.run("input")`) — used in `tests/examples/` and ad-hoc scripts. Runs in-process
   via `asyncio.run`, persists to SQLite, and *auto-registers* the workflow in the catalog on first
   call (`flux/workflow.py::_ensure_registered`). Auto-registration matters because PostgreSQL
   enforces the `executions.workflow_id` FK that SQLite ignores.
2. **Distributed** (`flux start server` + `flux start worker`) — the production path. Server stores the
   catalog and execution state; workers claim executions over an SSE stream and report progress
   through checkpoint POSTs.

Both share the same `ExecutionContext`, event log, and checkpoint mechanism, so the same workflow code
runs in either mode.

### Server ↔ worker dance

```
Worker            Server
  │  POST /workers/register (Bearer <bootstrap_token>)        →
  │  ← session_token, principal API key auto-provisioned
  │  GET  /workers/{name}/connect (SSE, Bearer session_token) →
  │  ← event: workflow.execution.scheduled (workflow + ctx, source base64-encoded)
  │  POST /workers/{name}/claim/{execution_id}                →
  │  …compile module, run workflow, …
  │  POST /workers/{name}/checkpoint/{execution_id} (per event) →
```

Implementation hot-spots:

- **HTTP routes live in `flux/api/<domain>_routes.py`**, each a `*RoutesMixin` class registering
  handlers as `@api.get/post(...)` inside a setup method. `flux/server.py` only composes those mixins
  and owns app/middleware setup — it contains no route decorators. Most handlers depend on
  `flux.security.dependencies.require_permission(...)`; request/response models are in
  `flux/api/schemas.py`.
- `flux/worker.py` — claim loop, checkpoint outbox, heartbeat, reconnect with exponential backoff,
  eviction handling.
- `flux/runners/` — pluggable execution runners, pinned per workflow via
  `@workflow.with_options(runner=...)`; workers advertise enabled runners at registration and dispatch
  matches on them. `subprocess` (default) runs each execution in a credential-less child
  (`runners/child.py` — sanitized env: no bootstrap token, no `FLUX_SECURITY__*`, no DB URL) that
  streams checkpoint/progress/secret/config/approval requests to the parent over a stdio pipe;
  `inprocess` runs on the worker's event loop; `docker` runs the same child protocol via `docker run
  -i`. The workflow module cache (TTL + LRU, source-hash keyed) is in `runners/loader.py`. Child
  crashes map to durability: durable → claim released for re-dispatch, transient → terminal FAILED.
  Approval rows are server-owned (`/workers/{name}/approvals/…`); only inline executions use
  `approvals.py::LocalApprovalStore`.
- `flux/worker_registry.py` — capability + label tracking; `WorkerInfo` is the matching unit.
- `flux/domain/resource_request.py` — `ResourceRequest` and `matches_labels`; powers both
  `@workflow.with_options(requests=...)` and `affinity={...}` matching.
- `flux/routing.py` — **dynamic routing**, the score stage of dispatch, ranking workers that hard
  constraints already filtered (e.g. `routing=score(prefer(label("x") == input("y")), least(load()))`).
  Policies are data: factories compile to a JSON spec that `catalogs.py::_extract_routing` reads
  statically via AST (unparseable policies fail registration loudly), it travels in
  `wf_metadata["routing"]`, and `pick_worker` evaluates it in `next_executions_batch` — per-term 0–1
  normalization across candidates, weighted sum, deterministic ties, malformed policies degrading to
  least-loaded. Event dispatch mode only, and a policy owns its whole score stage: the sticky hint
  applies only via an explicit `sticky()` term.
- `flux/worker_metrics.py` — built-in `flux.*` metrics (loop lag, slot occupancy, EWMA cpu/memory/load,
  failure and crash rates, throughput, duration p95, warm modules), collected worker-side over fixed
  windows and published on the heartbeat pong alongside `[flux.workers] metrics_provider` output. The
  `flux.` prefix is reserved — provider keys under it are stripped; `validate_worker_metrics` caps user
  keys at 32 and the total at 64. Persisted change-gated to `workers.metrics`; the dispatcher reads the
  in-memory copy.

### Decorators and the programming model

- `flux/task.py` — `task` is a class with `__call__`; `task.with_options(...)` returns a decorator.
  Options: `name`, `fallback`, `rollback`, `retry_max_attempts/_delay/_backoff`, `timeout`,
  `secret_requests`, `config_requests`, `output_storage`, `cache`, `metadata`, `auth_exempt`,
  `digest_exclude` (kwargs kept out of the identity digest — the task_id doubles as cache key and
  approval call_id, so excluded kwargs make calls differing only in them identical; how
  `call(routing_input=...)` keeps routing values out of replay identity), plus:
  - `requires_approval` (bool or runtime predicate) — pauses the workflow until an operator runs
    `flux execution approve|reject` (or POSTs the equivalent). Rejection raises `ApprovalRejected` and
    **bypasses retry/fallback/rollback**, since the body never ran. `approve --always` records a
    standing grant (`scope="execution"`) so later gates on the same task name auto-approve at
    `register` time, each writing an audit row naming the grant. Declaring `approval_target="<arg>"`
    also enables `approve --always-for-target`, authorizing only calls whose declared argument
    resolves to the same value (fail-closed when nothing is bound).
  - `risk` (`read`/`write`/`exec`/`external`) — in an agent turn only `read` runs concurrently with
    sibling tool calls; anything else is an ordering barrier in emission order. Undeclared plus a
    truthy `requires_approval` counts as non-read.
  - `sensitive` — recorded args/output stored as `[REDACTED:sensitive]`, and **replay re-executes the
    body** instead of short-circuiting, so the task must be safe to re-run. Incompatible with `cache`.

  The error-handling chain is **retry → fallback → rollback**; each step emits its own event types.
- `flux/workflow.py` — `workflow.with_options(...)` adds `namespace`, `requests`, `affinity`,
  `routing`, `runner`, `schedule`, plus `name`, `secret_requests`, `output_storage`. A workflow's first
  parameter is always `ctx: ExecutionContext[T]`; if `T` is a Pydantic `BaseModel` the catalog
  publishes its JSON schema (`catalogs.py::extract_workflow_input_schema`).
- Agents split autonomy into `autonomy` (strict/default/autonomous) × `approval_routing`
  (inline/notify); the legacy `approval_mode` is mapped and deprecated.
- `flux/__init__.py` — installs a custom `_FluxModule` class on `sys.modules["flux"]` so `flux.task`
  and `flux.workflow` resolve to the *classes* even though they're also submodules. If you
  import-rename anything at the top of `flux/`, exercise both `from flux import task` and
  `import flux.task` — the lazy/wildcard machinery is fragile.

### Domain core (`flux/domain/`)

- `execution_context.py` — `ExecutionContext[T]`, generic over input type. State is a `ContextVar`;
  tasks call `await ExecutionContext.get()` — never pass it manually. `ctx.checkpoint()` flushes events
  through the registered checkpoint callable (server-side: HTTP POST; inline: SQLAlchemy save).
- `events.py` — `ExecutionState` (CREATED → SCHEDULED → CLAIMED → RUNNING →
  COMPLETED/FAILED/CANCELLED, with PAUSED/RESUMING/RESUME_SCHEDULED/RESUME_CLAIMED/CANCELLING
  intermediates) and `ExecutionEventType` (workflow + task lifecycle, plus retry/fallback/rollback
  variants). Convenience flags: `has_finished`, `has_succeeded`, `has_failed`, `is_paused`,
  `is_cancelled`, `is_resuming`.
- `schedule.py` — `cron(...)`, `interval(...)`, `once(...)`, each taking `overlap="skip"|"allow"` (NULL
  rows predating the policy read as allow); `schedule_factory` builds them from raw config.

### Persistence

`flux/models.py` defines the SQLAlchemy ORM; `RepositoryFactory.create_repository()` dispatches on
`database_url`, and engines are cached per (repository class, URL). Schema changes go through Alembic
(`flux/migrations/`, applied automatically on first connect by `migrations/runner.py`): add a numbered
revision **and** the ORM column together, and update `HEAD` in **both**
`tests/flux/test_migrations.py` (parity test against full metadata) and
`tests/flux/test_migrations_postgresql.py` (postgresql-marked, so a stale value there only surfaces in
CI's `migrations-postgres` job).

Higher-level managers:

- `WorkflowCatalog` (`flux/catalogs.py`) — register / parse / lookup; AST-based source parsing extracts
  each workflow's docstring, resource requests, and routing policy.
- `ContextManager` (`flux/context_managers.py`) — execution persistence plus the dispatch queries
  (`next_execution`, `next_executions_batch`, `next_resume`, `next_cancellation`).
- `SecretManager` / `ConfigManager` — encrypted-at-rest blobs (PyCryptodome AES + PBKDF2). Requires
  `flux.security.encryption.encryption_key`; the shipped `flux.toml` does not hardcode one.

### Security (`flux/security/`)

- Two auth layers: **OIDC** (`providers/oidc.py`) and **API keys** (`providers/api_key.py`), both
  producing a `FluxIdentity` that `AuthService` resolves to a permission set via `RoleModel` rows.
  Built-in roles: `admin` (`*`), `operator`, `viewer`, `worker`. Permissions are
  `resource:scope:scope:verb` with `*` wildcards.
- Two init-only bootstraps, both writing a plaintext credential to `<home>/` on first start and
  surfaced host-locally by CLI: `bootstrap_token.py` (worker side — exchanged once at
  `POST /workers/register` for an API key + service principal; `flux server bootstrap-token`) and
  `admin_bootstrap.py` (human side — mints a random admin key bound to the `admin` role when no
  enabled admin principal exists; `flux server admin-key [--rotate]`).
- **Redaction** — `redaction.py` scrubs known secret values from execution-read API responses by value
  identity (`[flux.security] redact_secrets_in_responses`, default on). Presentation-only; the
  `sensitive` task option is the storage-level counterpart.
- **Execution token** (`execution_token.py`) — short-lived JWT scoped to a single execution, used when
  a running workflow calls back into the server.

### Tasks library (`flux/tasks/`)

- `builtins.py` — `parallel`, `pipeline`, `now`, `sleep`, `uuid4`, `choice`, `randint`.
- `graph.py` — `Graph` for DAG composition with cycle detection.
- `pause.py` — `pause(name, until=|after=|on_complete=)`; wake conditions fire from the scheduler tick,
  distributed path only. Also `call.py`, `progress.py`, `config_task.py`.
- `ai/` — the agent system. `agent.py` is the user-facing task, `agent_loop.py` the shared
  tool-execution loop; provider modules (`ollama.py`, `openai.py`, `anthropic.py`, `gemini.py`) are
  each a `(factory, formatter)` pair against the ABC in `formatter.py`. Also `agent_plan.py`,
  `delegation.py`, `dreaming.py`, `memory/`, `skills.py`, `tools/`, `approval.py`, `tool_executor.py`
  (owns the `risk` partition), and `capabilities.py` (`resolve_capabilities("provider/model")` —
  curated matrix + heuristics, no network; gates tool support, streaming, parallel dispatch).
- `mcp/` — MCP *client* (Flux calling external MCP servers). The MCP *server* exposing Flux workflows
  is `flux/mcp_server.py` / `flux/service_mcp.py`.

### Other subsystems

- `flux/agents/` — first-class **AI agent harness**: `manager.py` (CRUD), `process.py` + `session.py`
  (conversation lifecycle), `template.py`, `tools_resolver.py`, `ui/`. Agents live in the `agents`
  table and are *also* mirrored into configs under `agent:<name>` so workflow templates can fetch them
  via `get_config`.
- `flux/service_*` + `flux/service_mcp.py` — **Workflow services**: expose a workflow as an HTTP
  endpoint or MCP tool under a stable name. `service_resolver.py` handles collision detection;
  `service_proxy.py` provides standalone MCP endpoints with lazy discovery.
- `flux/schedule_manager.py` — runs in the server process. Under the cross-replica dispatch lock, the
  scheduler tick polls due schedules, runs the overlap-skip check, sweeps the park-TTL (failing
  executions unclaimed past `executions.park_deadline`), resumes executions whose
  `wake_at`/`wake_on_complete` fired — wake columns are stamped atomically with the PAUSED state write
  in `context_managers.py::_sync_wake_columns` — resolves orphaned CANCELLING executions whose
  delivery target is gone (`resolve_orphaned_cancellations`, issue #225), and reaps dead worker
  join tokens hourly (`Server._purge_join_tokens`).
- `flux/observability/` — OpenTelemetry tracing/metrics + a Prometheus `/metrics` endpoint, gated by
  `[flux.observability] enabled` and the `observability` extra.

## Configuration

`flux/config.py` loads settings via `pydantic-settings`, highest precedence first:

1. Environment variables prefixed `FLUX_` (nested with `__`, e.g. `FLUX_WORKERS__BOOTSTRAP_TOKEN`)
2. `flux.toml` in the project root (the shipped file documents the full surface)
3. `[tool.flux]` in `pyproject.toml`
4. Defaults

`Configuration.get().override(...)` is the official way to mutate config in tests;
`Configuration.get().reset()` restores defaults.

## Testing layout

- `tests/flux/` — framework unit tests (`agents/`, `domain/`, `observability/`, `output_storage/`,
  `tasks/`). Shared fixtures in `tests/flux/fixtures/`; `tests/flux/conftest.py` autouse-clears
  `DatabaseRepository._engines` between tests.
- `tests/examples/` — runs every example in `examples/` through the **inline** path. Follow the same
  pattern for new examples: `assert ctx.has_finished and ctx.has_succeeded`.
- `tests/e2e/` — `tests/e2e/conftest.py` is the important one: a session-scoped `cli` fixture spawns
  `flux start server` + `flux start worker` on a free port, seeds env vars (`FLUX_E2E_PORT`,
  `FLUX_DATABASE_URL`, `FLUX_WORKERS__BOOTSTRAP_TOKEN`, `FLUX_SECURITY__AUTH__ENABLED=false`, …), and
  exposes a `FluxCLI` wrapper shelling out to `poetry run flux …`. `FLUX_E2E_KEEP_LOGS=1` preserves
  logs at teardown.
- `tests/security/` — auth, permissions, principals, providers (real OIDC mocks).
- `tests/perf/` — opt-in perf suite; see `tests/perf/PLAN.md`.
- `tests/conftest.py` — autouse fixture seeding `bootstrap_token` and `encryption_key`, which are
  required and no longer defaulted.

## CI gates (`.github/workflows/pull-request.yml`)

| Job | What it runs |
|---|---|
| `version-check` | `pyproject.toml` version must be strictly greater than the target branch's |
| `lint` | `pre-commit run --all-files --show-diff-on-failure` |
| `unit` | `pytest tests/ --ignore=tests/e2e --cov=flux` on 3.12 / 3.13 / 3.14 |
| `e2e` | `pytest tests/e2e/ -m "not ollama and not network" -v` (15-min timeout) |
| `migrations-postgres` | PostgreSQL-only tests, incl. the migration parity check |
| `runner-docker` | docker + airgapped runner tests against an image built from the working tree |
| `perf-postgres` | perf suite on the `ci` profile |

Run pre-commit, the unit suite, and the E2E suite locally before pushing — the version-bump check
fails fast, and waiting on CI for a first green is slow.

## Project-specific gotchas

- **Bootstrap token + encryption key are not defaulted.** Anything touching the secrets store, secret
  encryption, or worker auth must seed them via `Configuration.get().override(...)` or env vars
  (`FLUX_WORKERS__BOOTSTRAP_TOKEN`, `FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY`). The autouse fixture
  in `tests/conftest.py` handles the standard pytest tree.
- **`flux/__init__.py` is non-trivial.** The custom module class resolves the
  `flux.task`/`flux.workflow` submodule-vs-attribute collision and lazily wildcard-imports
  `flux.encoders`, `flux.output_storage`, `flux.secret_managers`, `flux.tasks`, `flux.catalogs`,
  `flux.context_managers`. New top-level exports go in `_LAZY_IMPORTS` (or `_WILDCARD_MODULES`).
- **Workflow source travels base64-encoded** server → worker, then is `exec`-loaded under a synthetic
  module name (`flux_workflow__<ns>__<name>__v<version>`) cached for `module_cache_ttl` seconds. Two
  consequences: module-cache collisions across versions are real bugs (see `33c7a7b`), and anything
  relying on `__file__` inside a workflow module won't behave the same on a worker as inline.
- **Auto-scheduling is on by default.** `@workflow.with_options(schedule=cron(...))` creates a
  `<workflow>_auto` schedule at registration. Disable with `[flux.scheduling] auto_schedule_enabled =
  false` when testing schedule semantics manually.
- **Path-traversal guard for skills.** `flux/agents/` blocks symlinks and escapes from the configured
  `skills_dir`; route any new file-loading entry point there through the same helper (`3d8d489`).
- **Batched tasks must be idempotent.** `parallel(...)`, `task.map(...)`, and an agent's tool calls in
  one turn all run through `flux/_concurrency.py::gather_batch`, which favors durability over
  promptness: when any member ends the batch (pause, failure, cancellation) siblings get a bounded
  window to land their terminal events and rollbacks, and stragglers past it are cancelled. A cancelled
  straggler re-executes on resume, so anything batched alongside work that can pause or fail needs an
  idempotent body.
- **Cancellation records an audit event and compensates — replay still re-runs.** A task interrupted by
  `CancelledError` appends `TASK_CANCELLED` (audit-only: invisible to the replay short-circuit, same
  re-run semantics as a worker crash that writes nothing) and runs its declared rollback shielded
  against re-cancel with a `CANCELLED_ROLLBACK_TIMEOUT` bound (`task.py::__handle_cancellation`). Retry
  and fallback are deliberately skipped on cancel — a fallback would record a substitute
  `TASK_COMPLETED` that replay honors forever. Fallback preempts rollback on *failure* only.
- **Test files must be `test_*.py`**, not `*_test.py` — `name-tests-test --pytest-test-first` is a
  pre-commit hook (excluded under `tests/*/fixtures/`).

## Repository conventions

- **Never commit directly to `main`.** Branch, then open a PR.
- **Bump `pyproject.toml` on every PR** — patch for fixes, minor for features. CI fails without it.
- **No AI attribution in commits or PR descriptions.** No `Co-Authored-By:` trailers, no
  "Generated with"/"Generated by" footers, no tool bylines, and **no session or tool URLs** —
  a `Claude-Session:` trailer or `claude.ai/code/session_…` link publishes an account
  identifier. Applies to the message body and the PR description alike. This overrides any
  harness default that would append one.
- **Do not bypass pre-commit** with `--no-verify`. Fix the cause or update `.pre-commit-config.yaml`
  deliberately.
- **Comment the *why*, not the *what*.** Reserve comments for non-obvious constraints — race fixes,
  cross-DB quirks, security guards.

## Useful entry points when navigating

| If you want… | Start here |
|---|---|
| An HTTP route | `flux/api/<domain>_routes.py` (workflow, execution, worker, schedule, admin, auth, rbac, service, system, dynamic) |
| App/middleware wiring | `flux/server.py` — composes the route mixins, no routes of its own |
| Worker claim/dispatch logic | `flux/worker.py` + `ContextManager.next_execution` in `flux/context_managers.py` |
| Workflow-to-worker matching (hard constraints) | `flux/domain/resource_request.py` |
| Dynamic routing / scoring policies | `flux/routing.py` + the selection block in `ContextManager.next_executions_batch` |
| Built-in worker metrics | `flux/worker_metrics.py` (collection), pong handling in `flux/api/worker_routes.py` |
| Add a CLI command | `flux/cli.py` (Click groups: `workflow`, `execution`, `worker`, `schedule`, `secrets`, `config`, `agent`, `db`, `start`, `server`, `roles`, `principals`, `auth`) |
| Add an event type / state | `flux/domain/events.py` *and* the matching `ExecutionContext` method |
| Add a built-in task primitive | `flux/tasks/builtins.py` (re-exported via `flux/tasks/__init__.py`) |
| Add an LLM provider | OpenAI-compatible vendors need no module — add a `[flux.ai.providers.<name>]` descriptor row (or `flux/tasks/ai/providers.py::register_provider`). Only genuinely different wire formats get a module: a `(factory, formatter)` pair against `formatter.py::LLMFormatter` |
| Add an auth provider | `flux/security/providers/` |
