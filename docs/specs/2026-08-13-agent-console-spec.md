# Agent console — mission control for the web and terminal UIs

**Status:** design approved, awaiting implementation plan
**Surfaces:** web (`--mode web`) and terminal (`--mode terminal`), equal investment
**Reference points:** Cursor's agent product (dashboard/plan/approval patterns); visual
references supplied by the operator (LED-board web identity; btop terminal grammar)

## Problem

The engine already has sessions, plans (`agent_plan`), approvals with standing
grants, delegation/sub-agents, pause/resume, and progress streaming — and the
UIs show almost none of it. Both current surfaces are chat transcripts: the web
is one 748-line `index.html` with a single pane and one session; the terminal
has two parallel implementations (a plain ANSI REPL and a Textual TUI). An
operator cannot see what their agents are doing without scrolling chat, cannot
see two sessions at once, and finds approvals only by luck.

## Decisions (settled during design)

1. **Goal: surface the engine.** The UIs become windows onto work-in-progress;
   chat remains, but stops being the whole UI.
2. **Equal investment** in web and terminal, driven by one shared data layer.
3. **Home: `flux agent start`.** The console stays a per-process client of the
   Flux server (no server-embedded UI). From inside it the operator sees
   active sessions across agents, opens/resumes them, and starts new sessions
   for any catalog agent.
4. **V1 capabilities:** session dashboard, live plan & progress, approvals
   queue, sub-agent visibility. All four exist in the engine; this is
   presentation work plus one thin aggregation layer.
5. **Terminal consolidates on Textual.** The plain REPL survives only as a
   non-TTY fallback (auto-selected when stdout is not a TTY).
6. **Trust model: single operator.** Localhost by default, process token backs
   all calls (unchanged from today). Fleet visibility is whatever that token's
   RBAC allows.
7. **Out of scope for v1:** diff/artifact review (Cursor's centerpiece — Flux
   agents are not primarily code editors yet); chat filtered by plan step
   (needs step-tagged tool events the engine does not emit); multi-operator
   auth (the console API calls are structured so a per-request token can slot
   in later).

## Architecture — one console core, two renderers

New package `flux/agents/console/` with three units:

### `ConsoleService`
The single client of the Flux server for console purposes. Wraps existing HTTP
APIs only: agent catalog (config mirror `agent:<name>`), per-agent session
lists, the cross-execution approvals listing (`GET /approvals`) and the
per-execution decide endpoints, session spawn (runs `agent_chat` exactly as
`process.py` does), session stop. Holds the operator token. No UI knowledge,
no new server routes, no new persistence.

### `EventHub`
Fan-in of live data. Holds one SSE subscription per **open** session and
normalizes wire frames into a typed feed with a session envelope:

```
{session_id, event}
event ∈ token | reasoning | tool_start | tool_done | plan_updated | progress
        | approval_pending | approval_resolved | subagent_spawned
        | subagent_done | session_state
```

Sessions visible in the rail but not open are refreshed by polling the cheap
session-list endpoint on an interval; opening a session upgrades it to live.
Consumers: the web page over **one WebSocket** multiplexing the envelope; the
TUI over an asyncio queue. Identical semantics on both.

The normalization contract is the key test surface: every wire frame type maps
to exactly one hub event.

### `Commands`
The verb set both surfaces call: `open_session`, `new_session(agent, name?)`,
`send(session, text)`, `approve/reject(approval, always?)`, `rename(session,
name)`, `stop_session`. Thin methods on `ConsoleService`. The TUI calls them
in-process; the web calls small JSON endpoints added to the console's existing
FastAPI app (`ApiUI`). Anything the console can do, headless `api` users can
do through the same endpoints.

### Session names
Executions have no title field. Names live in the existing config store under
`agent-session-title:<execution_id>` — written on create/rename, read in bulk
for the rail. No schema change; rename is a config write. Unnamed sessions
auto-title from the first user message (client-derived, then persisted through
the same key).

## Web surface

### Layout — Rail + Stage
- **Left rail**: sessions across agents, grouped active → paused → done. Each
  row: status dot (● running, ◐ paused, ○ done), name, agent, compact
  mono status (`3/5 · 02:14`, `⚠ approval · 08:02`). `+ new session` at the
  bottom. Sectioned small-cap labels.
- **Center stage**: the focused session. Header: name (pencil-on-hover rename),
  agent, live `step n/m`. Chat scrolls; **composer is a footer-pinned
  full-width input** (`Enter` sends, `Shift+Enter` newline) that never scrolls
  away.
- **Right context panel**: PLAN (hero mono figure `3/5` + progress bar + step
  list), ACTIVITY (running/recent tool calls), SUB-AGENTS. Small-cap labels.
- **Top bar**: running count, approvals badge (`⚠ 2`) opening the approvals
  drawer, + new session.

### Interactions (all confirmed via mockups)
- **Plan step click** → expands in place: full step text, status,
  started/finished timestamps, live elapsed on the running step.
- **Tool call click** (chat block or ACTIVITY row — same object; expanding one
  highlights the other) → ARGS pretty-printed, OUTPUT (truncated with "show
  full"), duration, status. Running calls show ARGS + ticking elapsed; OUTPUT
  streams in on completion.
- **Sub-agent click** → card: brief, live status, streamed output tail, error
  if failed. No full transcript drill-in in v1 (delegations stream through the
  parent; the card is the honest unit).
- **New session** → modal: searchable agent picker (name · model ·
  description), optional name field (placeholder shows the default
  `agent · date, time`; hint explains auto-titling), Start.
- **Approvals drawer** → each pending approval: session, task, target value,
  Approve / Reject / Always (and Always-for-target when the task declares a
  target). Actions are idempotent server-side.

### Visual identity — the LED board (both themes required)
Live figures (step counts, timers, durations) are **always monospace, always
amber-weighted** — the console reads like an instrument panel. That is the
signature; everything around it stays quiet.

Dark tokens: ink `#0b111c`, panel `#0e1524`, line `#1c2536`, text `#c9d2e0`,
muted `#8b96a8`, amber `#f0a828` (glow allowed: subtle text-shadow on hero
figures), ok-green `#4ade80`.

Light tokens (warm and formal — the glow is a dark-only effect and is dropped;
amber deepens so the identity survives the ground change): ground `#f2f0ec`,
panel `#faf8f5`, line `#ddd8d0`, text `#3a4150`, muted `#7a7468`, amber
`#b45309`, ok-green `#15803d`.

Type: system UI face for prose; `ui-monospace` stack for every live figure,
status, and code; 9px letter-spaced small caps for section labels.

### Web implementation shape
The single-file `index.html` is retired in favor of a small static bundle
(`flux/agents/web/`: one HTML shell, one CSS file carrying the token system as
custom properties with `data-theme` switching, one JS module). No framework,
no build step — the constraint that kept the current page maintainable stays.
The JS module's seams (event-in → DOM-out) are the unit-test surface.

## Terminal surface

Layout A (mirror of the web: rail / chat / context) wearing **btop's
grammar**:

- Panel titles embedded in the top border with a number that is the hotkey:
  `┌1sessions─┐ ┌2chat─┐ ┌3context─┐`. Pressing `1/2/3` focuses the panel;
  the focused border takes the amber accent.
- Responsive collapse: below ~100 columns panels 1 and 3 become toggles;
  below ~80 the app is chat-only with the status line carrying
  `step 3/5 · ⚠2`.
- Keyboard-first mirror of the web interactions: `Enter` opens a session row,
  `r` renames, `n` new-session overlay (same picker + name), `a` approvals
  overlay, `Enter`/`Space` expands plan steps / tool calls / sub-agent cards
  in place (Textual `Collapsible`). Composer footer-pinned. Footer border
  carries key hints btop-style.
- Colors map 1:1 from the web dark tokens.

`textual_app.py` grows into this. `terminal.py` shrinks to the non-TTY
fallback: plain line output, no panels, selected automatically.

## CLI semantics

`flux agent start [NAME] --mode terminal|web|api`:
- `NAME` becomes **optional**. With it: console opens focused on a new (or
  `--session`-attached) session of that agent — today's behavior preserved.
- Without it: console opens on the session rail with the new-session picker.
- `api` mode is unchanged apart from gaining the console JSON endpoints
  (sessions/approvals/rename), which it serves headlessly.

## Error handling

- Server connection lost → status-bar banner, rail grays out, auto-reconnect
  with jittered backoff. The console never exits on connection loss.
- One session's SSE dropping does not affect others; its row shows
  `reconnecting…` and the hub resubscribes.
- Approval actions are idempotent; repeated clicks are safe.
- Every panel has a directive empty state ("No sessions yet — press n /
  + New session"; "No pending approvals").

## Testing

- **Unit — console core:** `ConsoleService` and `EventHub` against a mocked
  server API. The normalization contract (each wire frame type → exactly one
  hub event) is exhaustively covered; command methods verify request shapes.
- **Unit — TUI:** Textual `pilot` tests for focus hotkeys, expand/collapse,
  overlays (new session, approvals), responsive collapse breakpoints.
- **Unit — web:** the JS module's event-in/DOM-out seams; token/theme switch
  sanity.
- **E2E:** a real console process against the existing agent e2e stack:
  create session → stream tokens → tool activity appears → approval round-trip
  → rename → resume after console restart.
- **Import budget:** the console process must not pull the server graph beyond
  what `ApiUI` already does (guarded in the existing import-budget suite).

## Later (explicitly deferred)

- Diff/artifact review panel.
- Chat filtered by plan step (requires step-tagged tool events in the engine).
- Multi-operator auth on the web console.
- Sub-agent full-transcript drill-in (requires delegations as first-class
  sessions).
