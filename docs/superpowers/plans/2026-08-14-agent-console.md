# Agent Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the agent web and terminal UIs into mission control — session dashboard, live plan/progress, approvals queue, sub-agent visibility — per `docs/specs/2026-08-13-agent-console-spec.md`.

**Architecture:** One console core (`flux/agents/console/`) renders from the persisted execution event log (redacted REST reads) with per-turn SSE as a loss-tolerant live overlay; liveness gaps close via `progress()` emissions in the AI-task library. One engine addition: execution naming. Two renderers: a static web bundle (Rail+Stage, LED-board identity) and the Textual TUI (btop grammar).

**Tech Stack:** Python 3.12, FastAPI, Textual, vanilla JS/CSS (no framework, no build step), pytest + Textual pilot.

## Global Constraints

- Spec: `docs/specs/2026-08-13-agent-console-spec.md` — binding; on conflict, the spec wins.
- No server/engine changes except Task 1 (execution naming). AI-task library (`flux/tasks/ai/`) emissions are allowed (Task 3).
- Reuse `FluxClient` (`flux/agents/flux_client.py`) and `parse_event` (`flux/agents/events.py`); never rebuild them. `parse_event` kinds are frozen strings — extend additively only.
- Migration discipline: new revision `0025_execution_name` chained after `0024_join_token_revoked`; update `HEAD` in BOTH `tests/flux/test_migrations.py` and `tests/flux/test_migrations_postgresql.py`.
- Repo rules: version bump in `pyproject.toml` (use `0.82.0` — feature), pre-commit before every commit (mypy cold via `--all-files` before push), comments explain *why* only, no AI attribution anywhere.
- Web bundle: no framework, no build step, no external requests (tokens as CSS custom properties, `data-theme="dark|light"`).
- Design tokens (verbatim from spec): dark ink `#0b111c`, panel `#0e1524`, line `#1c2536`, text `#c9d2e0`, muted `#8b96a8`, amber `#f0a828`, ok `#4ade80`, danger `#f87171`; light ground `#f2f0ec`, panel `#faf8f5`, line `#ddd8d0`, text `#3a4150`, muted `#7a7468`, amber `#b45309`, ok `#15803d`, danger `#b91c1c`. Live figures always `ui-monospace` + amber.
- Status glyphs: `●` running, `◔` waiting-approval, `◐` idle (PAUSED between turns — normal), `○` completed, `✗` failed/cancelled. Never bucket FAILED with done.
- All commits on branch `feature/agent-console` (branched from `design/agent-console` so the spec rides along).

---

### Task 1: Engine — execution naming

**Files:**
- Create: `flux/migrations/versions/0025_execution_name.py`
- Modify: `flux/models.py` (ExecutionContextModel — add `name` column beside `schedule_id`)
- Modify: `flux/api/workflow_routes.py` (run endpoints: optional `name` query param — the `park_ttl` param at line ~203 is the pattern)
- Modify: `flux/server.py` (`_create_execution` ~lines 483-512 — the actual save site; thread `name` through into `manager.save`)
- Modify: `flux/api/schemas.py` (`AgentSessionSummaryResponse` ~lines 286-295 gains `name: str | None = None` — a bare dict key is dropped by the response model)
- Modify: `flux/api/execution_routes.py` (new `PUT /executions/{execution_id}/name`)
- Modify: `flux/api/admin_routes.py` (`_list_agent_sessions` ~line 398: include `ex.name` in rows)
- Modify: `flux/context_managers.py` (`save(...)` accepts `name: str | None = None`, persists it; a `rename(execution_id, name)` method)
- Modify: `flux/cli.py` (`workflow run` gains `--name`; new `flux execution rename <id> <name>`)
- Test: `tests/flux/test_execution_name.py`
- Modify: `tests/flux/test_migrations.py`, `tests/flux/test_migrations_postgresql.py` (HEAD → `0025_execution_name`)

**Interfaces:**
- Produces: `ExecutionContextModel.name: str | None`; `ContextManager.rename(execution_id: str, name: str) -> None` (raises `ExecutionContextNotFoundError`); `PUT /executions/{id}/name` body `{"name": "<str≤200>"}` → `{"execution_id", "name"}`, permission `workflow:{ns}:{wf}:run` (same as cancel — if you may run/cancel it you may name it); run endpoints accept `?name=`; `GET /agents/sessions` rows gain `"name": str | None`.

- [ ] **Step 1: Failing tests** — in `tests/flux/test_execution_name.py` (fixture pattern: copy the `manager` fixture from `tests/flux/test_cancellation_sweep.py` — Configuration override + `DatabaseRepository._engines.clear()`):

```python
class TestExecutionName:
    def test_save_persists_name(self, manager):
        ctx = _make_ctx()  # helper as in test_cancellation_sweep._cancelling_execution, without cancel
        manager.save(ctx, name="fix CI")
        row = _row(manager, ctx.execution_id)
        assert row.name == "fix CI"

    def test_rename(self, manager):
        ctx = _make_ctx(); manager.save(ctx)
        manager.rename(ctx.execution_id, "renamed")
        assert _row(manager, ctx.execution_id).name == "renamed"

    def test_rename_missing_execution_raises(self, manager):
        with pytest.raises(ExecutionContextNotFoundError):
            manager.rename("nope", "x")

    def test_name_survives_state_updates(self, manager):
        ctx = _make_ctx(); manager.save(ctx, name="keep me")
        ctx.start("w1"); manager.save(ctx)
        assert _row(manager, ctx.execution_id).name == "keep me"
```

- [ ] **Step 2: Run — must fail** (`poetry run pytest tests/flux/test_execution_name.py -q`; expect AttributeError/TypeError).
- [ ] **Step 3: Migration 0025** (copy `0024_join_token_revoked.py`'s idempotent add-column shape; table `executions`, column `name` `sa.String(200)` nullable). ORM column + docstring: "Operator-facing label (issue: agent console); sessions inherit it as titles."
- [ ] **Step 4: `save(name=...)` + `rename()`** in `context_managers.py` (thread `name` through `save`/`save_checked` kwargs; only set when not None so state updates never clear it).
- [ ] **Step 5: Tests green; update both migration-HEAD tests; run `tests/flux/test_migrations.py`.**
- [ ] **Step 6: Routes** — run endpoints `name: str | None = None` (validate ≤200 chars, 400 otherwise) → threaded through `flux/server.py::_create_execution` into `manager.save(..., name=name)`; `PUT /executions/{id}/name` in `execution_routes.py`: resolve execution → `require` the workflow's `:run` permission exactly as the cancel route does (`workflow_routes.py:667-675` pattern) → `manager.rename`. Session list: add `"name": ex.name`.
- [ ] **Step 7: Route tests** (extend `tests/flux/test_execution_name.py` with a `TestClient` class using the harness from `tests/flux/test_worker_release_route.py`): rename 200 + persisted; rename 404; name>200 → 400; run-with-name lands in session list.
- [ ] **Step 8: CLI** — `--name` on `workflow run` (pass as query param); `flux execution rename` (PUT). CliRunner tests (pattern: `tests/flux/test_cli.py::TestWorkerLabelSources`).
- [ ] **Step 9: Full check + commit** — `poetry run pytest tests/flux/ -q`, pre-commit, commit `feat(executions): operator-facing execution names`.

---

### Task 2: Prerequisite — lazy `flux/agents/__init__` + console import budget

**Files:**
- Modify: `flux/agents/__init__.py` (eager `AgentManager` import → module `__getattr__`, pattern: `flux/runners/__init__.py.__getattr__` from #237)
- Test: extend `tests/flux/test_startup_import_budget.py`

**Interfaces:**
- Produces: `from flux.agents import AgentManager` still works; `import flux.agents.ui.api` no longer pulls `sqlalchemy`/`flux.models`.

- [ ] **Step 1: Failing test:**

```python
@pytest.fixture(scope="module")
def console_modules() -> dict[str, bool]:
    return _presence("flux.agents.ui.api")  # existing helper in this file


class TestConsoleBudget:
    def test_console_process_stays_lean(self, console_modules):
        assert not console_modules["sqlalchemy"]
        assert not console_modules["flux.models"]
```

Note: `flux/agents/__init__.py` eagerly imports `AgentManager`, `AgentProcess`, and `types` — all three move behind the module `__getattr__`.

- [ ] **Step 2: Run — fails** (sqlalchemy present via eager `from flux.agents.manager import AgentManager`).
- [ ] **Step 3: Lazy `__getattr__`** re-export in `flux/agents/__init__.py`; chase any remaining edge the test reports (worker_registry-style method-local imports).
- [ ] **Step 4: Green + `poetry run pytest tests/flux/ -q` (no regressions from lazy init) + commit** `perf(agents): lazy AgentManager so UI processes stay lean`.

---

### Task 3: Emitter enrichment — plan and sub-agent progress payloads

**Files:**
- Modify: `flux/tasks/ai/agent_plan.py` (after every plan mutation that runs through a plan tool — create/update/complete-step — emit `await progress({"type": "plan", "plan": ctx.plan.to_dict()})`; the persistence site at ~line 230 marks where revisions settle)
- Modify: `flux/tasks/ai/delegation.py` (inside `delegate` ~line 143: emit `{"type": "subagent", "call_id": <task id>, "agent": name, "status": "started", "brief": <input>}` on entry, `{"type": "subagent", "call_id": ..., "status": "done"|"failed", "result_tail": <last 500 chars>}` on exit)
- Modify: `flux/agents/events.py` (additive kinds `KIND_PLAN = "plan"`, `KIND_SUBAGENT = "subagent"`; branches in `parse_event`'s progress-frame arm)
- Test: `tests/flux/agents/test_events_console.py` + extend `tests/flux/tasks/ai/` plan/delegation tests

**Interfaces:**
- Produces: `AgentEvent(kind="plan", data={"plan": {...}})`; `AgentEvent(kind="subagent", data={"call_id", "agent", "status", ...})`. Existing kinds untouched.

- [ ] **Step 1: Failing parse tests** — progress frame `{"type":"TASK_PROGRESS","value":{"type":"plan","plan":{"steps":[]}}}` → one `KIND_PLAN` event; same for `subagent`; regression: existing frames still parse identically (copy 2-3 cases from existing events tests).
- [ ] **Step 2: fails → Step 3: implement branches → Step 4: green.**
- [ ] **Step 5: Emission tests** — drive plan-tool mutation / a delegate call with a stubbed `progress` collector (existing plan/delegation test fixtures show the harness); assert payload shapes above.
- [ ] **Step 6: Full `tests/flux/` green, commit** `feat(ai): plan and sub-agent progress emissions for live consoles`.

---

### Task 4: Console core — `ConsoleService`

**Files:**
- Create: `flux/agents/console/__init__.py`, `flux/agents/console/service.py`
- Create: `tests/flux/agents/console/__init__.py`
- Test: `tests/flux/agents/console/test_service.py`

**Interfaces:**
- Consumes: `FluxClient` (`start_agent`, `resume`, `stream_execution`, `get_agent`, `get_execution`, `decide_approval`), Task 1's rename route.
- Produces (exact signatures — later tasks depend on these):

```python
@dataclass(frozen=True)
class SessionRow:
    execution_id: str; agent_name: str; state: str
    name: str | None; started_at: str | None; workflow_name: str
    derived_title: str | None = None  # display fallback, never persisted

@dataclass(frozen=True)
class ApprovalRow:
    execution_id: str; task_call_id: str; task_name: str
    target_value: str | None; requested_at: str | None

class ConsoleService:
    def __init__(self, server_url: str, token: str | None): ...
    async def list_agents(self) -> list[dict]            # GET /admin/agents
    async def list_sessions(self, agent: str | None = None) -> list[SessionRow]  # GET /agents/sessions (+name from Task 1)
    async def list_approvals(self) -> list[ApprovalRow]  # GET /approvals
    async def get_detail(self, execution_id: str) -> dict  # GET /executions/{id}?detailed=true (redacted log)
    async def spawn(self, agent_name: str, name: str | None) -> str  # returns execution_id; custom-workflow branch!
    async def send(self, execution_id: str, agent_name: str, workflow_name: str, text: str) -> AsyncIterator[dict]  # resume + stream
    async def respond_to_elicitation(self, execution_id: str, workflow_name: str, payload: dict) -> None
    async def decide(self, execution_id: str, task_call_id: str, approve: bool,
                     always: bool = False, always_for_target: bool = False) -> str  # "decided" | "already_decided"
    async def rename(self, execution_id: str, name: str) -> None  # PUT /executions/{id}/name
    async def stop(self, execution_id: str, namespace: str, workflow_name: str) -> None  # GET /workflows/{ns}/{wf}/cancel/{id}
    async def aclose(self) -> None
```

- **FluxClient additive extensions (part of this task — extend, never rebuild):**
  `decide_approval(..., always_for_target: bool = False)` (body key mirrors the server's `ApprovalDecideRequest`);
  `get_execution(execution_id, detailed: bool = False)` (adds `?detailed=true`);
  `start_agent(..., name: str | None = None)` (adds the `?name=` query param from Task 1).
- Spawn rule (spec): read the agent definition; if it declares `workflow_file`, ensure+run `agent_custom_<name>` (replicate `flux/cli.py:3057-3088` decision), else `agent_chat`. Never unconditionally `agent_chat`.
- `send(execution_id, agent_name, workflow_name, text)` — takes the session's `workflow_name` (from `SessionRow`) so custom-workflow sessions resume `agent_custom_<name>`, not the default.
- `respond_to_elicitation(execution_id, workflow_name, payload: dict) -> None` — resumes the paused execution with the elicitation response, per-session (the legacy `/elicitation` route resumes with process-level names and is wrong for picker-spawned sessions).
- Already-decided detection: `FluxClient.decide_approval` swallows the 409 and returns the body — check `body.get("error") == "already_decided"`, NOT the status code.
- `derived_title`: `None` here; computed by callers from the log (Task 5 helper).

- [ ] **Step 1: Failing tests** using the house HTTP-stub pattern — `tests/flux/agents/test_flux_client.py::_patched_async_client` (line ~44; monkeypatches `httpx.AsyncClient` — `respx` is NOT installed and FluxClient takes no transport parameter): list_sessions maps rows (incl. `name`); spawn picks `agent_custom_x` when definition has `workflow_file` (assert requested workflow name); decide already-decided body → `"already_decided"`; stop hits the workflow-scoped cancel GET; rename PUTs; send resumes with the given workflow_name.
- [ ] **Step 2 fail → 3 implement → 4 green → 5 commit** `feat(console): ConsoleService — the single server client for console surfaces`.

---

### Task 5: Console core — `EventHub` + derived titles

**Files:**
- Create: `flux/agents/console/hub.py`, `flux/agents/console/titles.py`
- Test: `tests/flux/agents/console/test_hub.py`, `test_titles.py`

**Interfaces:**
- Consumes: `parse_event`, `ConsoleService.send/get_detail`.
- Produces:

```python
@dataclass(frozen=True)
class ConsoleEvent:
    session_id: str
    event: AgentEvent           # parse_event vocabulary + plan/subagent (Task 3)

class EventHub:
    def __init__(self, service: ConsoleService): ...
    def subscribe(self) -> asyncio.Queue[ConsoleEvent]      # consumers: TUI queue, web SSE pump
    async def run_turn(self, session_id: str, agent_name: str, workflow_name: str, text: str) -> None
        # drives service.send, fans parse_event output to subscribers,
        # then emits ConsoleEvent(session_id, AgentEvent("log_delta", {"detail": ...}))
        # from a fresh get_detail — the turn-boundary reconciliation.
    async def open_session(self, session_id: str) -> dict    # get_detail now; returns detail for initial render

def derived_title(detail: dict) -> str | None
    # first USER message text from the execution detail's event log,
    # stripped, first 48 chars, word-boundary truncation with '…'; None if no user message yet.
```

- A stream error mid-turn does NOT raise to subscribers: emit `AgentEvent("error", ...)` then still run the `log_delta` reconciliation (the log catches up — spec's loss-tolerance).
- `KIND_LOG_DELTA = "log_delta"` added in `console/hub.py` (console-only kind, not in `events.py`'s frozen wire contract).

- [ ] **Step 1: Failing tests**: fan-out (two subscribers each get every event with the right session_id); turn ends with exactly one `log_delta` carrying the get_detail dict; mid-stream exception → `error` event then `log_delta` (loss-tolerance contract); `derived_title` cases (long text truncates on word boundary; no user message → None; deterministic).
- [ ] **Step 2 fail → 3 implement → 4 green → 5 commit** `feat(console): EventHub — session-enveloped fan-out with turn-boundary log reconciliation`.

---

### Task 6: Console app — endpoints, security hardening, static mount

**Files:**
- Create: `flux/agents/console/app.py` (builds on `ApiUI`'s FastAPI app)
- Modify: `flux/agents/ui/api.py` (register console routes + the Origin/CSRF dependency on ALL state-changing routes including existing `/chat`, `/approval`, `/elicitation`)
- Modify: `flux/agents/ui/web.py` (StaticFiles mount of `flux/agents/web/`; `GET /console/state`)
- Test: `tests/flux/agents/console/test_app_security.py`, `test_app_endpoints.py`

**Interfaces:**
- Produces JSON endpoints (all `POST`/`PUT` require header `X-Flux-Console: 1` AND, when an `Origin` header is present, an allowlist match of the console's own origin — 403 otherwise):
  - `GET  /console/state` → `{"agent": str | None, "session": str | None, "server_url": str, "can_write": bool}` — `can_write` is the read-only-degradation signal: false when a probe of the operator token lacks write verbs (a 403 from any state-changing call also carries the server's structured `missing_permission`, which the UI surfaces verbatim)
  - `GET  /console/agents`, `GET /console/sessions`, `GET /console/approvals`
  - `GET  /console/sessions/{id}/detail`
  - `POST /console/sessions` `{"agent": str, "name": str | None}` → `{"execution_id"}`
  - `POST /console/sessions/{id}/send` `{"text"}` → SSE of hub events (house mechanism — SSE, not WebSocket, per review note)
  - `POST /console/approvals/{execution_id}/{task_call_id:path}` (`:path` like the server's own decide routes) `{"approve": bool, "always": bool, "always_for_target": bool}` → `{"result": "decided"|"already_decided"}`
  - `POST /console/sessions/{id}/elicitation` `{"payload": {...}}` → 204 (per-session resume via `ConsoleService.respond_to_elicitation`)
  - `PUT  /console/sessions/{id}/name` `{"name"}`
  - `POST /console/sessions/{id}/stop`
- `api` mode serves the same endpoints with its per-request Bearer contract; WebUI keeps the operator-token dependency (unchanged trust model).
- **Console app constructor**: the app builder gains `agent_name: str | None` (today `ApiUI.__init__` requires `str` — `ui/api.py:32-40`); when None, legacy single-agent routes (`/chat`) return 404 with "console runs multi-session — use /console/*", and `/console/state` reports `agent: null`. `flux/agents/process.py` (`AgentProcess`) accepts the optional name in Task 9.
- **derived_title in list responses**: computed from a hub-side cache of details seen at open/turn boundaries — NEVER per-row detailed fetches (spec's cheap-list rule). Never-opened sessions report `derived_title: null` and the UI falls back to `"agent · date"`.

- [ ] **Step 1: Failing security tests** (TestClient): POST `/console/sessions` without `X-Flux-Console` → 403; with header + hostile `Origin: https://evil.example` → 403; with header + own origin → passes auth layer; **regression-hardening**: existing `/chat` without the header now 403s; GETs unaffected.
- [ ] **Step 2 fail → 3 implement dependency + wire routes to ConsoleService/EventHub → 4 green.**
- [ ] **Step 5: Endpoint tests** with a stubbed ConsoleService: sessions list carries `name` + `derived_title`; send streams SSE frames; decide returns `already_decided` on 409 path; static mount serves `/static/console.css` 200.
- [ ] **Step 6: Commit** `feat(console): console endpoints with Origin/CSRF hardening and static bundle mount`.

---

### Task 7: Web bundle — Rail + Stage, LED board

**Files:**
- Create: `flux/agents/web/console.html` (shell), `flux/agents/web/console.css` (tokens + layout), `flux/agents/web/console.js` (one module)
- Modify: `flux/agents/ui/web.py` (`GET /` serves `console.html`)
- Delete: `flux/agents/web/index.html` (retired in place, per spec)
- Test: `tests/flux/agents/console/test_web_shell.py` (served shell references only local assets; token blocks present for both themes)

**Layout/behavior contract (from the confirmed mockups — implement exactly):**
- Three-region grid: rail 190px, stage flex, context 200px; top bar with running count, `⚠ n` approvals button → drawer, `+ new session`.
- Rail rows: glyph (Global Constraints table) + title (`name ?? derived_title ?? "agent · date"`) + agent + mono status line; grouped active → idle → done; click opens (fetch detail → render → subscribe SSE).
- Stage: header (title with pencil → inline input → PUT name; agent; amber mono step figure when latest `plan` payload exists), chat log (user/agent/tool blocks), **composer pinned via grid row at the bottom** (`Enter` send / `Shift+Enter` newline).
- Context panel: PLAN (hero `n/m` mono amber + bar + step list; step click toggles `.expanded` showing full text), ACTIVITY (tool tasks from the log: name, duration; click expands ARGS/OUTPUT pretty-printed, truncated at 2KB with "show full"), SUB-AGENTS (cards from `subagent` payloads + log).
- New-session modal: filter input over `/console/agents` (name · model · description), optional name field with placeholder `agent · <local datetime>`, Start → POST → open.
- Approvals drawer: rows from `/console/approvals` with Approve / Reject / Always / Always-for-target (last only when `target_value` non-null); 409 result renders "already decided elsewhere".
- Elicitation events render an inline form in the stage (port the existing `index.html` elicitation block's behavior).
- Theme: `data-theme` on `<html>`, toggle in top bar, persisted to `localStorage`; both palettes from Global Constraints; amber glow (`text-shadow`) on hero figures **dark only**.
- Empty states verbatim, one per panel: rail "No sessions yet — press + New session"; approvals drawer "No pending approvals"; PLAN "No plan yet"; ACTIVITY "No tool calls yet"; SUB-AGENTS "No sub-agents".
- Read-only degradation: when `/console/state.can_write` is false, every state-changing control renders disabled with a tooltip naming the missing permission (from the server's structured 403) — never a silent failure.
- JS structure: `render(state)` pure-ish DOM builders + `handleEvent(consoleEvent)` reducer — the seams named by the spec; no framework.

- [ ] **Step 1: Failing shell test** (serves `console.html`, no external URLs, both token sets present as CSS custom properties).
- [ ] **Step 2 fail → 3 build the bundle → 4 green.**
- [ ] **Step 5: Manual smoke against a live stack** (`flux agent start --mode web` from Task 9's CLI — if Task 9 not yet done, `--mode web` with a NAME): create, chat, approve, rename, theme toggle. Fix what smoke finds.
- [ ] **Step 6: Commit** `feat(console): web mission control — Rail+Stage, LED-board identity, both themes`.

---

### Task 8: TUI — btop grammar on Textual

**Files:**
- Modify: `flux/agents/ui/textual_app.py` (grows into the console), `flux/agents/ui/textual_widgets.py` (new panels), `flux/agents/ui/textual_messages.py` (extend, don't replace)
- Create: `flux/agents/ui/console_screens.py` (new-session + approvals overlays)
- Test: `tests/flux/agents/console/test_tui.py` (Textual `pilot`)

**Behavior contract:**
- Three panels titled in-border `1sessions` / `2chat` / `3context`; keys `1/2/3` focus (focused border amber `#f0a828`); `n` new-session overlay (filterable agent list + name input), `a` approvals overlay, `r` rename (input overlay on highlighted row), `Enter` opens session / expands focused row (`Collapsible` for tool/plan/sub-agent items), `Ctrl+D` quit.
- Responsive: `on_resize` — width < 100 hides panels 1/3 (toggle with `1`/`3`); < 80 chat-only, status line carries `step n/m · ⚠k`.
- Status line (btop footer style): key hints + session state + approvals count.
- Data: same `ConsoleService`/`EventHub` in-process; rail from `list_sessions` on a 3s timer; open session renders detail then live events.
- Read-only degradation mirrors the web: disabled bindings shown dimmed in the footer with the missing permission in the status line. Empty states per panel as in Task 7.
- App class: `ConsoleApp(App)` in `textual_app.py`, constructor `(service: ConsoleService, hub: EventHub, initial_agent: str | None, initial_session: str | None)` — the object Task 9's CLI constructs.
- Colors: web dark tokens mapped to Textual CSS.

- [ ] **Step 1: Failing pilot tests**: app composes three panels; `2` focuses chat (border class asserted); `n` pushes NewSessionScreen; resize(90×30) hides rail; resize(70×30) chat-only; approval overlay lists a stubbed ApprovalRow and `Enter` on Approve calls the stubbed service with the right ids.
- [ ] **Step 2 fail → 3 implement → 4 green → 5 commit** `feat(console): Textual mission control — btop grammar, hotkey panels, overlays`.

---

### Task 9: CLI semantics

**Files:**
- Modify: `flux/cli.py` (`agent start`: `NAME` optional; NAME-less + no TTY + mode≠api → clear error `"the console needs a terminal; use --mode api or provide an agent name"`; `--plain` preserved; `agent session resume <id>` opens the console focused on that session)
- Modify: `flux/agents/process.py` (`AgentProcess.agent_name` becomes `str | None`; None is only valid for console modes, asserted with a clear error otherwise)
- Test: extend `tests/flux/agents/test_process.py` + CliRunner tests

- [ ] **Step 1: Failing tests**: NAME-less terminal invocation on TTY constructs `ConsoleApp` (Task 8) with `initial_agent=None` (rail-first); NAME-less non-TTY exits 1 with the message; `--plain` with NAME still selects the plain REPL (existing test at test_process.py:125 keeps passing); resume path passes session id through.
- [ ] **Step 2 fail → 3 implement → 4 green → 5 commit** `feat(cli): flux agent start opens the console; NAME optional`.

---

### Task 10: E2E, docs, screenshots, PR

**Files:**
- Create: `tests/e2e/test_agent_console.py` (module-scoped console-api harness on the pattern of `tests/e2e/test_agent_harness_server_modes.py::agent_harness_env`, plus the seeding above)
- Create: `tests/flux/agents/console/__init__.py` (package-style test tree — created in Task 4, listed here as a guard)
- Create: `docs/advanced-features/agent-console.md` + modify `docs/advanced-features/agent-harness.md` (serving-modes section) — AGENTS.md requires the docs entry
- Create: `docs/images/console-web-dark.png`, `console-web-light.png`, `console-tui.svg` (+ png)
- Modify: `pyproject.toml` (0.82.0)

**Harness seeding (the stock `agent_harness_env` is NOT enough):** the module fixture must additionally seed via the server API — two agents through `POST /admin/agents` (one plain, one declaring a `workflow_file` so the custom-workflow spawn is assertable) and one approval-gated workflow (pattern: `tests/e2e/test_approvals.py`) so the approval round-trip has a gate to trip.

**E2E flow (api mode drives the console core through a real stack):**
- [ ] **Step 1: Failing e2e** — spawn console in api mode against the harness env; `POST /console/sessions` (picker agent) → session appears in `GET /console/sessions` with derived title after first send; send streams tokens; tool activity appears in `/detail`; approval round-trip incl. second decide → `already_decided`; `PUT name` → rename visible; custom-workflow agent spawns `agent_custom_<name>` (assert workflow name in the execution row); stop cancels.
- [ ] **Step 2 red on missing pieces → fix → green.** Run the full gates: `poetry run pytest tests/flux/ -q`, e2e file, `pre-commit run --all-files` (cold mypy).
- [ ] **Step 3: Screenshots** — TUI: pilot script sizes 120×32, opens a seeded session, `app.export_screenshot()` SVG → save; convert to PNG if a converter is available, else ship SVG. Web: run a live stack + `flux agent start --mode web`, capture dark and light with the browser tools; save under `docs/images/`, reference them in `agent-console.md`.
- [ ] **Step 4: Docs page** — quick start, layout tour (embedding the screenshots), permissions-per-verb table (from spec Security; rename documents `workflow:{ns}:{wf}:run` explicitly), the four filed follow-ups referenced. Add the page to `mkdocs.yml` nav (~lines 87-94 — the nav is explicit; an unlisted page does not render).
- [ ] **Step 5: Version bump, final full-suite run, commit, push, PR** — PR body: spec + plan links, screenshots inline, verification matrix; merge-order note if other PRs are open. File the four "filed separately" issues from the spec.

---

## Self-review (run after writing, fixed inline)

- Spec coverage: naming→T1, budget prereq→T2, emissions→T3, service→T4, hub/titles→T5, security+endpoints+static→T6, web→T7, TUI→T8, CLI→T9, e2e/docs/screens/PR→T10. Deferred list untouched. ✓
- No placeholders; every task carries real interfaces and first tests. ✓
- Type consistency: `SessionRow`/`ApprovalRow`/`ConsoleEvent` names match across T4–T8. ✓
