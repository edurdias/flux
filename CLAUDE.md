# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See also `AGENT.md` for tool-agnostic agentic-development conventions (PR flow, version bump, review etiquette). This file focuses on what the project *is*; AGENT.md focuses on *how* to change it.

## Project at a glance

Flux (`flux-core` on PyPI) is a Python distributed workflow orchestration engine. Workflows and tasks are defined as `async` functions decorated with `@workflow` / `@task`; the runtime persists every state transition as an `ExecutionEvent`, so executions can be paused, resumed, replayed deterministically, and dispatched across worker nodes.

- **Python 3.14** (set in `pyproject.toml`; CI matrix is 3.14 only). The pre-commit `pyupgrade` hook still targets `--py39-plus`, but new code should assume 3.14 features (PEP 695, etc.).
- **Poetry** for dependency management and the `flux` console-script entry point. `uv.lock` is gitignored — do not introduce uv.
- **Optional extras**: `postgresql`, `observability`, `ai` (Ollama / OpenAI / Anthropic / Gemini). Default install gives SQLite + no telemetry + no LLM providers.

## Common commands

All commands are run via Poetry. The Makefile is a thin wrapper around these — prefer the explicit `poetry run …` form when in doubt.

```bash
# Setup
poetry install                                  # base dev environment
poetry install --extras observability           # what CI installs for lint/unit
poetry install --extras postgresql              # adds psycopg2

# Lint & format — always go through pre-commit, do NOT run ruff/mypy directly
poetry run pre-commit run --all-files           # full sweep (matches CI)
poetry run pre-commit run --files <path>...     # single file(s)

# Tests
poetry run pytest tests/ --ignore=tests/e2e     # unit suite (what CI runs)
poetry run pytest tests/flux/test_worker.py     # one file
poetry run pytest tests/flux/test_worker.py::TestWorker::test_start_registers   # one test
poetry run pytest tests/e2e/ -m "not ollama" -v # E2E (spawns server + worker)
poe test-e2e                                    # same, via poethepoet
poe test-e2e-no-ai                              # E2E excluding @pytest.mark.ollama
poetry run pytest -m postgresql                 # PostgreSQL-tagged tests
make test-postgresql                            # full PG suite (docker-compose up + tests + down)

# Run a server / worker locally
poetry run flux start server                    # FastAPI server + integrated scheduler
poetry run flux start worker                    # worker (auto-named if no arg)
poetry run flux start worker my-worker --server-url http://localhost:8000 \
                              --label gpu=true  # labels enable affinity dispatch
poetry run flux start mcp                       # MCP server exposing workflow tools
poetry run flux start console                   # Textual TUI

# Common CLI groups (each group has --help)
poetry run flux workflow list|register|run|show|resume|cancel|status|versions|delete
poetry run flux execution list|show
poetry run flux schedule create|list|show|pause|resume|history|delete
poetry run flux secrets   set|get|list|remove
poetry run flux config    set|get|list|remove
poetry run flux agent     create|list|show|update|delete
poetry run flux roles | principals | auth                        # security admin
poetry run flux server bootstrap-token                           # print the auto-generated worker token
```

### Pytest markers (pyproject.toml)

- `e2e` — autoset on everything in `tests/e2e/`; spawns real server + worker subprocesses.
- `ollama` — requires a local Ollama install; auto-skipped in `tests/e2e/conftest.py` when `ollama list` fails.
- `postgresql` — needs the test PG container (Make targets handle the lifecycle).
- `integration`, `slow` — exist; rarely the primary filter.

## Architecture (the big picture)

### The two execution paths

1. **Inline** (`workflow.run("input")`) — used in `tests/examples/` and ad-hoc scripts. Runs in-process via `asyncio.run`, persists to SQLite, and *auto-registers* the workflow in the catalog on first call (`flux/workflow.py::_ensure_registered`). This auto-registration matters because PostgreSQL enforces the `executions.workflow_id` FK that SQLite ignores.
2. **Distributed** (`flux start server` + `flux start worker`) — the production path. Server stores the catalog and execution state; workers claim executions over an SSE stream and report progress through checkpoint POSTs.

Both paths share the same `ExecutionContext`, event log, and checkpoint mechanism, so the same workflow code runs in either mode.

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
- `flux/server.py` — ~4800 lines; FastAPI routes for workflows / executions / schedules / workers / admin / services. Most route handlers depend on `flux.security.dependencies.require_permission(...)`.
- `flux/worker.py` — claim loop, module cache (TTL configurable, default 300s), heartbeat, reconnect with exponential backoff, eviction handling.
- `flux/worker_registry.py` — capability + label tracking; `WorkerInfo` is the matching unit.
- `flux/domain/resource_request.py` — `ResourceRequest` and `matches_labels`; powers both `@workflow.with_options(requests=...)` resource matching and `affinity={...}` label matching.

### Decorators and the programming model

- `flux/task.py` — `task` is a class with `__call__`; `task.with_options(...)` returns a decorator that wraps a function in `task(...)`. Options: `name`, `fallback`, `rollback`, `retry_max_attempts/_delay/_backoff`, `timeout`, `secret_requests`, `config_requests`, `output_storage`, `cache`, `metadata`, `auth_exempt`. The error-handling chain is **retry → fallback → rollback**; each step emits its own event types.
- `flux/workflow.py` — `workflow.with_options(...)` adds `namespace`, `requests` (ResourceRequest), `affinity` (label dict), `schedule` (Schedule from `flux/domain/schedule.py`), plus `name`, `secret_requests`, `output_storage`. A workflow's first parameter is always `ctx: ExecutionContext[T]`; if `T` is a Pydantic `BaseModel`, the catalog publishes its JSON schema (`flux/catalogs.py::extract_workflow_input_schema`).
- `flux/__init__.py` — installs a custom `_FluxModule` class on `sys.modules["flux"]` so that `flux.task` and `flux.workflow` resolve to the *classes* even though they're also submodules. If you import-rename anything at the top of `flux/`, exercise both `from flux import task` and `import flux.task` — the lazy/wildcard machinery is fragile.

### Domain core (`flux/domain/`)

- `execution_context.py` — `ExecutionContext[T]` (Generic over input type). State is a `ContextVar`; tasks call `await ExecutionContext.get()` to retrieve the current context — never pass it manually. `ctx.checkpoint()` flushes events through the registered checkpoint callable (server-side: HTTP POST; inline: SQLAlchemy save).
- `events.py` — `ExecutionState` (CREATED → SCHEDULED → CLAIMED → RUNNING → COMPLETED/FAILED/CANCELLED, with PAUSED/RESUMING/RESUME_SCHEDULED/RESUME_CLAIMED/CANCELLING intermediates) and `ExecutionEventType` (workflow + task lifecycle, plus retry/fallback/rollback variants). Convenience flags on `ExecutionContext`: `has_finished`, `has_succeeded`, `has_failed`, `is_paused`, `is_cancelled`, `is_resuming`.
- `resource_request.py` — used by both resource matching and label affinity.
- `schedule.py` — `cron(...)`, `interval(...)`, `once(...)` factories; `schedule_factory` builds them from raw config.

### Persistence

`flux/models.py` defines the SQLAlchemy ORM (`Base`, `WorkflowModel`, `ExecutionContextModel`, `ExecutionEventModel`, plus `RoleModel`, `APIKeyModel`, `AgentModel`, `ConfigModel`, `WorkerModel`, …). `RepositoryFactory.create_repository()` dispatches on `database_url` (`sqlite://` vs `postgresql://`); engines are cached per (repository class, URL) tuple. Tables are auto-created via `Base.metadata.create_all` on first connect — there is no Alembic.

Higher-level managers wrap the repositories:
- `WorkflowCatalog` (`flux/catalogs.py`) — register / parse / lookup workflows; AST-based parsing of source files extracts each workflow's docstring and resource requests.
- `ContextManager` (`flux/context_managers.py`) — execution persistence + the dispatch queries (`next_execution`, `next_resume`, `next_cancellation`) used by workers when claiming work.
- `SecretManager` / `ConfigManager` — encrypted-at-rest blobs (PyCryptodome AES + PBKDF2). Encryption needs `flux.security.encryption.encryption_key` to be set; the shipped `flux.toml` no longer hardcodes one.

### Security (`flux/security/`)

- Two auth layers: **OIDC** (`providers/oidc.py`) and **API keys** (`providers/api_key.py`). Both feed into a `FluxIdentity`; `AuthService` resolves it to a permission set via `RoleModel` rows. Built-in roles: `admin` (`*`), `operator`, `viewer`, `worker`.
- **Bootstrap token** (`bootstrap_token.py`) — used once by a worker to obtain an API key + service-principal during `POST /workers/register`. If the server's `[flux.workers] bootstrap_token` is unset, one is auto-generated and persisted to `<home>/bootstrap-token` on first start; surface it via `flux server bootstrap-token`.
- **Execution token** (`execution_token.py`) — short-lived JWT scoped to a single execution; used by the worker when calling back into the server during a workflow.
- Most server routes go through `Depends(require_permission("workflow:{namespace}:{name}:run"))` etc. Permissions follow `resource:scope:scope:verb` with `*` wildcards.

### Tasks library (`flux/tasks/`)

- `builtins.py` — `parallel`, `pipeline`, `now`, `sleep`, `uuid4`, `choice`, `randint`.
- `graph.py` — `Graph` for DAG composition with cycle detection.
- `pause.py`, `call.py`, `progress.py`, `config_task.py`.
- `ai/` — the agent system. `agent.py` is the user-facing `agent()` task; `agent_loop.py` is the shared tool-execution loop; provider modules (`ollama.py`, `openai.py`, `anthropic.py`, `gemini.py`) are each a `(factory, formatter)` pair conforming to the ABC in `formatter.py`. Other pieces: `agent_plan.py` (multi-step planning + replanning), `delegation.py` (sub-agents / workflow agents), `dreaming.py` (memory consolidation), `memory/`, `skills.py`, `tools/`, `tool_executor.py`, `approval.py` (human-in-the-loop tool approval).
- `mcp/` — MCP *client* (Flux calling external MCP servers from a workflow). The MCP *server* exposing Flux workflows is `flux/mcp_server.py` / `flux/service_mcp.py`.

### Other subsystems

- `flux/agents/` — first-class **AI agent harness**: `manager.py` (CRUD), `process.py` + `session.py` (conversation lifecycle), `template.py`, `tools_resolver.py`, plus `ui/` (terminal + Textual + web) and a static `web/index.html`. Agents are stored in the `agents` table and *also* mirrored into the configs table under `agent:<name>` so workflow templates can fetch them via `get_config`.
- `flux/console/` — `flux start console` Textual TUI. `app.py` shell, screens in `screens/` (dashboard / executions / workflows / schedules / workers / logs), reusable widgets in `widgets/` (Gantt, run history, status badge, JSON viewer, …). The TUI talks to the server via the async `client.py` (REST wrapper).
- `flux/service_*` modules + `flux/service_mcp.py` — **Workflow services**: a workflow can be exposed as an HTTP endpoint or MCP tool with a stable name. `service_resolver.py` handles collision detection; `service_proxy.py` provides standalone MCP endpoints with lazy discovery.
- `flux/schedule_manager.py` — runs inside the server process; polls scheduled workflows, dispatches them, and tracks history.
- `flux/observability/` — OpenTelemetry tracing/metrics + a Prometheus `/metrics` endpoint, gated by `[flux.observability] enabled` and the `observability` extra.

## Configuration

Loaded by `flux/config.py` via `pydantic-settings` with this precedence (highest first):

1. Environment variables prefixed `FLUX_` (nested via `__`, e.g. `FLUX_WORKERS__BOOTSTRAP_TOKEN`, `FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY`)
2. `flux.toml` in the project root (see the shipped file for the full surface)
3. `[tool.flux]` in `pyproject.toml`
4. Defaults

`Configuration.get().override(...)` is the official way to mutate config in tests; `Configuration.get().reset()` restores defaults. `tests/conftest.py` autouse-fixtures `bootstrap_token` and `encryption_key` so unit tests don't trip the missing-config errors that production now raises by design.

## Testing layout

- `tests/flux/` — framework unit tests. Subdirs: `agents/`, `console/`, `domain/`, `observability/`, `output_storage/`, `tasks/`. Shared fixtures in `tests/flux/fixtures/` (config, database). `tests/flux/conftest.py` adds an autouse fixture that clears `DatabaseRepository._engines` between tests.
- `tests/examples/` — runs every example in `examples/` end-to-end through the **inline** path. Use the same pattern when adding examples: `assert ctx.has_finished and ctx.has_succeeded`.
- `tests/e2e/` — `tests/e2e/conftest.py` is the important one: a session-scoped `cli` fixture spawns `flux start server` + `flux start worker` as subprocesses on a free port, seeds env vars (`FLUX_E2E_PORT`, `FLUX_DATABASE_URL`, `FLUX_WORKERS__BOOTSTRAP_TOKEN`, `FLUX_SECURITY__AUTH__ENABLED=false`, …), and exposes a `FluxCLI` wrapper that shells out to `poetry run flux …`. Tests register example workflows from `examples/` and `tests/e2e/fixtures/` and assert on JSON CLI output. Set `FLUX_E2E_KEEP_LOGS=1` to preserve logs at teardown.
- `tests/security/` — auth/permissions/principals/providers; uses real OIDC mocks.
- `tests/test_scheduling.py`, `tests/test_scheduling_examples.py`, `tests/test_validate_examples.py` — top-level cross-cutting tests.
- `tests/conftest.py` — autouse fixture seeding the two required-but-no-longer-defaulted settings.

## CI gates (`.github/workflows/pull-request.yml`)

Every PR must pass:

1. **version-check** — `pyproject.toml`'s version must be strictly greater than the target branch's. PRs that don't bump it fail. Bump the patch version for fixes, minor for features.
2. **lint** — `poetry run pre-commit run --all-files --show-diff-on-failure`.
3. **unit** — `poetry run pytest tests/ --ignore=tests/e2e --cov=flux …`.
4. **e2e** — `poetry run pytest tests/e2e/ -m "not ollama" -v` (15-minute timeout).

Run `pre-commit`, the unit suite, and the E2E suite locally before pushing — relying on CI for the first green is slow and the version-bump check fails fast on local mistakes too.

## Project-specific gotchas

- **Bootstrap token + encryption key are no longer defaulted.** If you write a script or test that touches the secrets store, secret encryption, or worker auth, seed them via `Configuration.get().override(...)` or env vars (`FLUX_WORKERS__BOOTSTRAP_TOKEN`, `FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY`). The autouse fixture in `tests/conftest.py` handles this for the standard pytest tree.
- **`flux/__init__.py` is non-trivial.** It uses a custom module class to resolve the `flux.task`/`flux.workflow` submodule-vs-attribute collision and lazily wildcard-imports `flux.encoders`, `flux.output_storage`, `flux.secret_managers`, `flux.tasks`, `flux.catalogs`, `flux.context_managers`. New top-level exports go in `_LAZY_IMPORTS` (or `_WILDCARD_MODULES` for bulk).
- **Workflow source travels base64-encoded** server → worker, then is `exec`-loaded under a synthetic module name (`flux_workflow__<ns>__<name>__v<version>`) cached for `module_cache_ttl` seconds. Two consequences: (a) module-cache collisions across versions are real bugs (see `33c7a7b`), (b) anything that relies on `__file__` inside a workflow module won't behave the same on a worker as it does inline.
- **Auto-scheduling is on by default.** `@workflow.with_options(schedule=cron(...))` creates a `<workflow>_auto` schedule on registration. Disable with `[flux.scheduling] auto_schedule_enabled = false` if testing schedule semantics manually.
- **Path-traversal guard for skills.** `flux/agents/` blocks symlinks/escapes from the configured `skills_dir`; if you add new file-loading entry points there, route them through the same helper (see commit `3d8d489`).
- **`name-tests-test --pytest-test-first`** is enabled in pre-commit. Test files must be named `test_*.py`, not `*_test.py` (excluded under `tests/*/fixtures/`).

## Useful entry points when navigating

| If you want… | Start here |
|---|---|
| All HTTP routes | `flux/server.py` (one large file, search for `@app.` decorators) |
| Worker claim/dispatch logic | `flux/worker.py` + `ContextManager.next_execution` in `flux/context_managers.py` |
| Workflow-to-worker matching | `flux/domain/resource_request.py` |
| Add a CLI command | `flux/cli.py` (Click groups: `workflow`, `execution`, `schedule`, `secrets`, `config`, `agent`, `roles`, `principals`, `auth`, `start`, `server`) |
| Add an event type / state | `flux/domain/events.py` *and* the corresponding `ExecutionContext` method in `execution_context.py` |
| Add a built-in task primitive | `flux/tasks/builtins.py` (re-exports via `flux/tasks/__init__.py`) |
| Add an LLM provider | `flux/tasks/ai/<provider>.py` — implement a `(factory, formatter)` pair against `formatter.py::LLMFormatter` |
| Add an auth provider | `flux/security/providers/` |
| Add a TUI screen | `flux/console/screens/` + register in `flux/console/app.py` |
