# Agent console — mission control for the web and terminal UIs

**Status:** design v2 (rewritten after adversarial review), awaiting approval
**Surfaces:** web (`--mode web`) and terminal (`--mode terminal`), equal investment
**Reference points:** Cursor's agent product (dashboard/plan/approval patterns); visual
references supplied by the operator (LED-board web identity; btop terminal grammar)

## Problem

The engine has sessions, plans, approvals with standing grants, delegation,
pause/resume, and progress streaming — and the UIs show almost none of it.
Both surfaces are chat transcripts: the web is one 748-line `index.html` with
a single pane and session; the terminal has two parallel implementations. An
operator cannot see what agents are doing without scrolling chat, cannot see
two sessions at once, and finds approvals only by luck.

## Decisions

1. **Goal: surface the engine.** Chat remains, but stops being the whole UI.
2. **Equal investment** in web and terminal, one shared console core.
3. **Home: `flux agent start`.** The console is a per-process client of the
   Flux server. No server-embedded UI.
4. **V1 capabilities:** session dashboard, live plan & progress, approvals
   queue, sub-agent visibility.
5. **Terminal consolidates on Textual.** The plain REPL remains reachable two
   ways, exactly as today: automatically when stdout is not a TTY, and
   explicitly via `--plain` / `FLUX_PLAIN_TERMINAL` (the escape hatch for
   terminals where Textual misrenders).
6. **Trust model: single operator**, localhost by default, process token —
   with the console's own HTTP surface hardened (see Security).
7. **Engine changes: none, with one deliberate exception** — execution
   naming (below), a general capability the console inherits. Everything
   else renders from data the engine already persists or emits; where live
   detail is missing, the **AI-task library** (agent loop, plan, delegation)
   emits richer `progress()` payloads — library code, not server, transport,
   or schema.

## Data model — the event log is the truth; streams are an overlay

The v1 architecture rests on one verified fact: **agent tool calls are Flux
`@task` invocations** (`tool_executor.py` — "each invocation produces task
events"). Therefore the persisted execution event log already carries, with
server timestamps and through the **redacted** REST read path:

- every tool call's name, arguments, output, and real duration;
- every plan revision (plan mutations happen through plan tools → task
  events whose payloads are the plan state);
- every delegation's brief and result (delegation tools → task events).

**Rendering model:**
- The console renders session detail from the event log: fetched when a
  session is opened and re-read at turn boundaries.
- During an active turn, the existing per-turn SSE stream overlays live
  tokens, reasoning, and tool start/done ticks. Streams ending at `PAUSED`
  (the steady state between turns) is *normal*, not a fault: the stream is
  per-turn by design, and the log catches up whatever the stream missed.
  This makes the console loss-tolerant by construction — the destructive
  single-consumer progress queue and absent replay are tolerable because
  nothing rendered from the stream is load-bearing.
- **Emitter-side enrichment** (in `flux/tasks/ai/`, allowed): `agent_plan`
  emits a `plan` progress payload on each revision; `delegation` emits
  sub-agent lifecycle/output-tail payloads attributed by call id. These
  improve *liveness* only; on (re)open the same facts come from the log.
- Rail rows for **non-open** sessions show only what cheap existing reads
  carry: `GET /agents/sessions` (state, agent, started) and
  `GET /approvals` (pending flags). No per-row detailed fetches — step
  counts appear on the open session only. This is a deliberate v1 limit.

## Engine addition — execution naming

The one engine change, useful well beyond the console:

- `executions.name` — nullable string column, Alembic migration (next in
  sequence; parity-test HEADs updated in both files per repo convention).
- Settable at submission (optional `name` on the run request) and via a
  small rename endpoint; surfaced in execution/session list responses
  (`GET /agents/sessions` joins executions — the name rides along).
- CLI: `--name` on run; `flux execution rename <id> <name>`.

Titles in the console: **persisted names come only from explicit acts**
(new-session modal's name field, rename). Unnamed sessions display a
*derived* title — deterministic truncation of the first user message, read
from the log — identical across consoles, never written anywhere. No
auto-title writes, therefore no rename/auto-title races.

## Architecture — one console core, two renderers

New package `flux/agents/console/`:

### `ConsoleService`
The single server client for console purposes, **built on the existing
`FluxClient`** (which already owns multi-line SSE buffering and the
409-decide handling — it is reused, not rebuilt). Wraps existing APIs:
agent catalog (`GET /admin/agents`), session lists (`GET /agents/sessions`),
cross-execution approvals (`GET /approvals`, which carries `target_value`),
per-execution approval decides, execution detail reads, session spawn, and
cancel via the real route (`GET /workflows/{ns}/{wf}/cancel/{id}`).
Spawn honors the **custom-workflow branch**: agents with a `workflow_file`
run `agent_custom_<name>`, exactly as `flux/cli.py` decides today — the
picker must not spawn `agent_chat` unconditionally.

### `EventHub`
Fan-in and normalization, **layered on `flux/agents/events.py::parse_event`**
— the existing frozen contract ("UIs depend on these strings"), which is
one-to-many (a PAUSED-with-approval frame fans out to state + approval
events). The hub adds only the session envelope `{session_id, event}` and
the log-refresh events (`log_delta` on turn boundaries). The vocabulary is
`parse_event`'s — including `elicitation` (MCP OAuth flows must not hang)
and `chat_response` (the only reply carrier for non-streaming agents) —
plus the new library-emitted `plan` and `subagent` progress payloads.
There is no `approval_resolved` wire event; resolution is observed via the
decide response and the next log read.

### `Commands`
`open_session`, `new_session(agent, name?)`, `send(session, text)`,
`approve/reject(approval, always?, always_for_target?)`,
`respond_to_elicitation(...)`, `rename(execution, name)`, `stop_session`
(via the cancel route). Decide calls treat HTTP 409 as *already decided*
(the server is not idempotent; the client is graceful — the pattern
`FluxClient` already implements). TUI calls in-process; web calls JSON
endpoints on the console's FastAPI app; `api` mode serves the same
endpoints headlessly, preserving its **per-request Bearer** contract
(WebUI remains the process-token exception, as today).

## Security (console-side; in scope)

- **Origin/CSRF defense on the console app**: all state-changing endpoints
  require a custom header (`X-Flux-Console: 1`) — which forces a CORS
  preflight — plus an Origin allowlist (the console's own origin). This
  also retro-hardens the existing `/chat`, `/approval`, `/elicitation`
  endpoints, which are drive-by-callable today. Without this, a malicious
  webpage could invoke approve-with-`always` under the operator token —
  one forged click becoming a durable standing grant.
- **Secrets**: tool ARGS/OUTPUT render from the event log via the redacted
  REST path — never from raw progress frames. (The server-side gap —
  progress SSE bypassing redaction — is filed as a separate security issue;
  it is pre-existing and not console-specific.)
- **Permissions are documented per verb** (approve needs
  `workflow:<ns>:<wf>:task:<task>:approve`, stop needs `...:run`, rename
  needs the execution-rename permission introduced with the endpoint).
  A token lacking write verbs degrades the console to read-only: buttons
  disabled with the missing permission named, never a silent 403.

## Web surface

### Layout — Rail + Stage (unchanged from v1 design, confirmed via mockups)
- **Left rail**: sessions grouped active → idle → done. Row: status glyph,
  title (name or derived), agent, compact mono status. `+ new session`.
- **Status glyphs are honest about the 12-state enum**: `●` running (green),
  `◔` waiting on approval (amber), `◐` idle/paused between turns (slate —
  the *normal* state of a healthy chat session), `○` completed (muted),
  `✗` failed/cancelled (red — never bucketed with done).
- **Center stage**: focused session. Header: title (pencil rename), agent,
  live step figure when a plan exists. Chat scrolls; **composer pinned to
  the footer** (`Enter` sends, `Shift+Enter` newline).
- **Right context panel**: PLAN (mono hero figure + bar + steps), ACTIVITY
  (tool calls), SUB-AGENTS. Small-cap labels.
- **Top bar**: running count, approvals badge → drawer, + new session.

### Interactions (confirmed via mockups)
- **Plan step click** → expands: full step text, status; timestamps/elapsed
  shown when derivable from surrounding task events (plan steps carry no
  native timestamps — derived, or omitted, never invented).
- **Tool call click** (chat block ≡ ACTIVITY row) → ARGS pretty-printed,
  OUTPUT (truncated, "show full"), duration — all from the redacted log.
  Running calls show ARGS + elapsed ticking from the live overlay.
- **Sub-agent click** → card: brief, status, output tail (live via the new
  delegation progress payloads; brief/result from the log).
- **New session** → modal: searchable agent picker (name · model ·
  description from the catalog), optional name (persisted as the execution
  name), Start. Custom-workflow agents spawn their declared workflow.
- **Approvals drawer** → per approval: session, task, target value,
  Approve / Reject / Always / Always-for-target (when a target is bound).
  409 renders as "already decided elsewhere".
- **Elicitation** → inline prompt in the stage (as today's UIs do), so MCP
  auth flows complete.

### Visual identity — the LED board (both themes; confirmed via mockups)
Live figures (step counts, timers, durations) are **always monospace,
always amber-weighted**; everything else stays quiet.

Dark: ink `#0b111c`, panel `#0e1524`, line `#1c2536`, text `#c9d2e0`, muted
`#8b96a8`, amber `#f0a828` (subtle glow allowed on hero figures), ok
`#4ade80`, danger `#f87171`.
Light (warm, formal; glow dropped, amber deepened): ground `#f2f0ec`, panel
`#faf8f5`, line `#ddd8d0`, text `#3a4150`, muted `#7a7468`, amber
`#b45309`, ok `#15803d`, danger `#b91c1c`.
Type: system UI for prose; `ui-monospace` for every live figure/status/code;
letter-spaced small caps for section labels.

### Web implementation shape
`flux/agents/web/` becomes a small static bundle **replacing the current
`index.html` in place**: one HTML shell, one CSS file (tokens as custom
properties, `data-theme` switch), one JS module. No framework, no build
step. Served via a FastAPI `StaticFiles` mount on the console app (today
only `/` is hardcoded — the mount is part of this work); the shell receives
its boot context (agent name, session) from a small `GET /console/state`
JSON call instead of the retired `{{AGENT_NAME_ATTR}}` substitution.

## Terminal surface

Layout A in **btop's grammar** (unchanged from v1 design):
- Border-embedded, number-prefixed panel titles as hotkeys:
  `┌1sessions─┐ ┌2chat─┐ ┌3context─┐`; focused border takes amber.
- Collapse below ~100 cols (panels 1/3 become toggles); chat-only below
  ~80 with the status line carrying the figures.
- Keys: `Enter` open, `r` rename, `n` new-session overlay, `a` approvals
  overlay, `Enter`/`Space` expand in place; composer footer-pinned; footer
  border carries key hints.
- Colors map 1:1 from the web dark tokens. `textual_app.py` grows into
  this; existing `textual_messages.py` widgets are extended, not replaced.

## CLI semantics

`flux agent start [NAME] --mode terminal|web|api [--plain] [--session ID]`:
- With NAME: console opens focused on a new (or attached) session of that
  agent — today's behavior, including the custom-workflow branch.
- Without NAME: console opens on the rail with the picker. Without NAME
  **and** without a TTY (and no `--mode api`): exit with a clear error —
  the plain fallback is a single-session chat loop and cannot host the
  rail; scripting belongs to `api` mode.
- `flux agent session resume <id>` keeps its exact semantics: console
  opens focused on that session (agent resolved from the session row).
- `flux execution rename <id> <name>` and `--name` on run (engine
  addition).

## Error handling

- Server connection lost → status-bar banner, rail grays, jittered-backoff
  reconnect; the console never exits on connection loss.
- A turn-stream ending at `PAUSED` is not an error (steady state). A
  stream dropping mid-turn shows `reconnecting…` on that session; the next
  log read reconciles whatever was missed.
- Decide 409 → "already decided elsewhere", row clears on next refresh.
- Every panel has a directive empty state.

## Testing

- **Console core**: unit tests against a mocked server; the normalization
  layer is tested as *an extension of* `parse_event`'s contract (including
  the one-to-many fan-outs, elicitation, chat_response); command methods
  verify request shapes and the 409 path.
- **TUI**: Textual `pilot` tests for hotkeys, expand/collapse, overlays,
  collapse breakpoints. E2E cannot exercise Textual (no TTY in the e2e
  harness — the fallback would be selected); TUI coverage is pilot-based
  by design, stated openly.
- **Web**: JS module seams (event-in → DOM-out); theme/token sanity.
- **E2E** (drives `api`/plain surfaces + the console core through a real
  stack): spawn via picker flow (including a custom-workflow agent) →
  stream → tool activity from the log → approval round-trip incl. 409 →
  rename → resume.
- **Import budget**: a console budget entry is added; prerequisite: fix
  `flux/agents/__init__.py`'s eager `AgentManager` import (pulls
  sqlalchemy/fastapi into any agent-UI process today — same lazy-import
  class as #241).

## Filed separately (pre-existing, discovered during review)

1. Progress SSE bypasses secret redaction (server-side).
2. `flux agent stop` POSTs to a route that does not exist (404s today).
3. Eager `AgentManager` import in `flux/agents/__init__.py` blows any
   honest agent-process import budget.
4. `/chat`, `/approval`, `/elicitation` on the current web console are
   drive-by callable (fixed here for the new app; filed for awareness of
   released versions).

## Later (explicitly deferred)

- Diff/artifact review panel.
- Chat filtered by plan step (needs step-tagged tool events).
- Multi-operator auth on the web console.
- Sub-agent full-transcript drill-in (needs delegations as sessions).
- Step counts on non-open rail rows (needs a cheap change-signal endpoint,
  e.g. exposing `last_event_ordinal`).
- Docs: `docs/advanced-features/` console page + `agent-harness.md` update
  ship with implementation (AGENTS.md requirement), not with this spec.
