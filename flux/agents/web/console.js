/* Flux console — Rail + Stage mission control.
 *
 * Two seams, no framework: `render()` rebuilds regions from `state` with
 * plain DOM builders, and `handleEvent(consoleEvent)` is the only reducer
 * that live events go through. Everything the operator sees is derived from
 * `state`; nothing reads back out of the DOM.
 *
 * Truth model (mirrors the server's): the persisted execution log is the
 * source of truth and the live stream is an overlay. Every turn ends with a
 * `log_delta` frame carrying a fresh detail read, and that read *replaces*
 * the chat blocks — a stream frame this renderer missed can never leave the
 * transcript permanently wrong. Progress-only state (plan, sub-agents) is
 * never persisted, so reconciliation deliberately keeps it.
 *
 * No innerHTML anywhere: agent output, tool arguments and MCP prompts are
 * untrusted text and only ever reach the page through textContent.
 */

const WRITE_HEADERS = {
  // Required by the console's CSRF gate: a custom header forces a preflight
  // that a drive-by POST from another origin cannot silently pass.
  "X-Flux-Console": "1",
  "Content-Type": "application/json",
};

const THEME_KEY = "flux.console.theme";
const POLL_MS = 5000;
const OUTPUT_LIMIT = 2048; // 2 KB, then "show full"
const TITLE_LIMIT = 48; // matches the server's derived_title truncation

// Status glyphs are part of the design system, not decoration: failed is
// never bucketed with done.
const GLYPH = {
  running: "●",
  waiting: "◔",
  idle: "◐",
  done: "○",
  failed: "✗",
};

// Tasks the runtime records for its own machinery — the LLM call, the chat
// pause, config reads, working-memory bookkeeping. They are real log events
// but they are not tool calls, and showing them would bury the ones that
// are. A user tool named like one of these is mis-filtered; that is the
// documented cost of the log having no "this was a tool" marker.
const INTERNAL_TASK = /^(pause$|get_config|get_secret|agent_|llm_|wm_)/;

const TERMINAL_STATES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

const RUNNING_STATES = new Set([
  "CREATED",
  "SCHEDULED",
  "CLAIMED",
  "RUNNING",
  "RESUMING",
  "RESUME_SCHEDULED",
  "RESUME_CLAIMED",
  "CANCELLING",
]);

const dom = {
  runningStat: document.getElementById("running-stat"),
  approvalsBtn: document.getElementById("approvals-btn"),
  newSessionBtn: document.getElementById("new-session-btn"),
  themeBtn: document.getElementById("theme-btn"),
  serverLabel: document.getElementById("server-label"),
  rail: document.getElementById("rail"),
  stageHead: document.getElementById("stage-head"),
  chat: document.getElementById("chat"),
  input: document.getElementById("composer-input"),
  context: document.getElementById("context"),
  drawer: document.getElementById("drawer"),
  modal: document.getElementById("modal"),
};

const state = {
  agent: null,
  serverUrl: "",
  canWrite: true,
  missingPermission: null,
  notice: null,
  noticeOwner: null, // who wrote the notice — see setNotice

  sessions: [],
  approvals: [],
  decisions: new Map(), // "exec|call" -> note shown on the approval row

  activeId: null,
  localTitle: null, // derived from the open session's own log, pre-poll
  blocks: [],
  tools: new Map(), // id -> tool record, shared by the chat block and ACTIVITY
  plan: null,
  subagents: new Map(),
  stream: "",
  reasoning: null,
  busy: false,

  expandedTools: new Set(),
  fullOutputs: new Set(),
  expandedSteps: new Set(),
  expandedSubagents: new Set(),
  renaming: false,
  stopConfirm: false, // stop is two-click: cancelling a session is not undoable
  drawerOpen: false,
  modalOpen: false,
  agents: [],
  agentFilter: "",
  agentSelected: null,

  chatVersion: 0, // bumped by anything that changes blocks (not by tokens)
};

/* ── tiny DOM builder ──────────────────────────────────────────────── */

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "onclick") node.addEventListener("click", value);
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key === "disabled" || key === "hidden") node[key] = Boolean(value);
      else node.setAttribute(key, value);
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function label(text) {
  return el("div", { class: "label", text });
}

function empty(text) {
  return el("div", { class: "empty", text });
}

function clear(node) {
  node.replaceChildren();
}

/* ── formatting ────────────────────────────────────────────────────── */

function toMs(value) {
  if (!value) return null;
  if (typeof value === "number") return value;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

// Every TASK_COMPLETED value in the log is an OutputStorageReference
// envelope (flux/output_storage.py), not the return value itself; the
// default inline storage parks the real value under metadata.value.
// Rendering the envelope would show storage bookkeeping where the tool's
// output belongs. A non-inline reference does not carry the value in the log
// at all, so name where it went. Mirrored by the TUI's unwrap_output().
function unwrapOutput(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  if (!("storage_type" in value)) return value;
  if (value.storage_type === "inline" && value.metadata && typeof value.metadata === "object") {
    return value.metadata.value;
  }
  return `[stored in ${value.storage_type}: ${value.reference_id}]`;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function fmtElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(seconds)}`
    : `${pad(minutes)}:${pad(seconds)}`;
}

function fmtDuration(ms) {
  // Finished calls read like an instrument: sub-minute in seconds, longer
  // ones in the same mm:ss the live timers use.
  return ms < 60000 ? `${(ms / 1000).toFixed(2)}s` : fmtElapsed(ms);
}

function fmtClock(iso) {
  const ms = toMs(iso);
  if (ms === null) return "";
  const date = new Date(ms);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function fmtDate(iso) {
  const ms = toMs(iso);
  if (ms === null) return "";
  return new Date(ms).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtAge(iso) {
  const ms = toMs(iso);
  if (ms === null) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  if (seconds < 172800) return "yesterday";
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function truncate(text, limit = TITLE_LIMIT) {
  const value = String(text);
  return value.length <= limit ? value : `${value.slice(0, limit).trimEnd()}…`;
}

function pretty(value) {
  if (value === undefined) return "";
  if (typeof value === "string") {
    // Tool arguments often arrive as a JSON string from the model.
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch (err) {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch (err) {
    return String(value);
  }
}

function inlineArgs(args) {
  const text = typeof args === "string" ? args : pretty(args);
  return truncate(text.replace(/\s+/g, " ").trim(), 64);
}

function sizeOf(text) {
  const bytes = new Blob([text]).size;
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

/** Append `text` to `parent`, honouring fenced and inline code only. */
function appendText(parent, text) {
  const parts = String(text ?? "").split("```");
  parts.forEach((part, index) => {
    if (index % 2 === 1) {
      parent.appendChild(el("pre", { text: part.replace(/^[\w-]*\n/, "") }));
      return;
    }
    part.split(/`([^`\n]+)`/).forEach((chunk, position) => {
      if (!chunk) return;
      if (position % 2 === 1) parent.appendChild(el("code", { text: chunk }));
      else parent.appendChild(document.createTextNode(chunk));
    });
  });
}

/* ── HTTP ──────────────────────────────────────────────────────────── */

class HttpError extends Error {
  constructor(status, body) {
    super(`HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

/**
 * Pull the missing permission out of a 403 body.
 *
 * The console re-raises the Flux server's error body verbatim, so it arrives
 * nested (`{detail: {detail: {...}}}`) and in two shapes: the structured
 * `{error, missing_permission}` one, and plain prose from the generic
 * permission dependency ("Permission denied: requires 'x'").
 */
function missingPermissionFrom(body, depth = 0) {
  if (body === null || body === undefined || depth > 6) return null;
  if (typeof body === "string") {
    const match = body.match(/requires '([^']+)'/);
    return match ? match[1] : null;
  }
  if (typeof body !== "object") return null;
  if (typeof body.missing_permission === "string") return body.missing_permission;
  if (Array.isArray(body.missing_permissions) && body.missing_permissions.length) {
    return String(body.missing_permissions[0]);
  }
  for (const value of Object.values(body)) {
    const hit = missingPermissionFrom(value, depth + 1);
    if (hit) return hit;
  }
  return null;
}

function noteFailure(status, body, isWrite) {
  if (status !== 403) return;
  const permission = missingPermissionFrom(body);
  if (permission) state.missingPermission = permission;
  // Only a denied *write* means read-only; a 403 on a listing is a different
  // permission gap entirely (the server scopes its own signal the same way).
  if (isWrite) state.canWrite = false;
}

async function request(path, options = {}) {
  const isWrite = Boolean(options.method) && options.method !== "GET";
  const response = await fetch(path, options);
  let body = null;
  if (response.status !== 204) {
    try {
      body = await response.json();
    } catch (err) {
      body = null;
    }
  }
  if (!response.ok) {
    noteFailure(response.status, body, isWrite);
    throw new HttpError(response.status, body);
  }
  return body;
}

const api = {
  state: () => request("/console/state"),
  agents: () => request("/console/agents"),
  sessions: () => request("/console/sessions"),
  approvals: () => request("/console/approvals"),
  detail: (id) => request(`/console/sessions/${encodeURIComponent(id)}/detail`),
  create: (agent, name) =>
    request("/console/sessions", {
      method: "POST",
      headers: WRITE_HEADERS,
      body: JSON.stringify({ agent, name: name || null }),
    }),
  rename: (id, name) =>
    request(`/console/sessions/${encodeURIComponent(id)}/name`, {
      method: "PUT",
      headers: WRITE_HEADERS,
      body: JSON.stringify({ name }),
    }),
  decide: (executionId, callId, decision) =>
    request(
      `/console/approvals/${encodeURIComponent(executionId)}/${encodeURIComponent(callId)}`,
      { method: "POST", headers: WRITE_HEADERS, body: JSON.stringify(decision) },
    ),
  elicitation: (id, payload) =>
    request(`/console/sessions/${encodeURIComponent(id)}/elicitation`, {
      method: "POST",
      headers: WRITE_HEADERS,
      body: JSON.stringify({ payload }),
    }),
  stop: (id) =>
    request(`/console/sessions/${encodeURIComponent(id)}/stop`, {
      method: "POST",
      headers: WRITE_HEADERS,
    }),
};

/**
 * Notices are owned by whatever wrote them.
 *
 * The topbar has one line for both background failures ("sessions: ...") and
 * the outcome of an operator action ("stop: 403 ..."). Every action refreshes
 * the listings right after it lands, so a refresh that cleared the line on
 * success erased the error the action had just reported — a stop the token
 * cannot cancel was reported nowhere at all. Pollers now clear only their own
 * notice; an action clears the line as it starts, so what stays on it is that
 * action's own outcome.
 */
function setNotice(owner, text) {
  state.notice = text;
  state.noticeOwner = owner;
}

function clearNotice(owner) {
  if (state.noticeOwner === owner) {
    state.notice = null;
    state.noticeOwner = null;
  }
}

/** Start an operator action: its outcome supersedes whatever is on the line. */
function beginAction() {
  state.notice = null;
  state.noticeOwner = null;
}

function errorText(err) {
  if (err instanceof HttpError) {
    const detail = err.body && err.body.detail !== undefined ? err.body.detail : err.body;
    if (typeof detail === "string") return `${err.status}: ${detail}`;
    if (detail) return `${err.status}: ${JSON.stringify(detail)}`;
    return `HTTP ${err.status}`;
  }
  return err && err.message ? err.message : String(err);
}

/* ── log → chat blocks ─────────────────────────────────────────────── */

function pushBlock(block) {
  state.blocks.push(block);
  state.chatVersion += 1;
}

/**
 * Rebuild the transcript from an execution detail dict.
 *
 * Only the log can express what actually happened, so this is authoritative
 * for chat blocks and tool calls. Plan and sub-agent state come from
 * progress events, which are explicitly never persisted (see
 * flux/tasks/progress.py) — reconciliation therefore leaves them alone
 * rather than blanking a live plan on every turn boundary.
 */
function applyDetail(detail) {
  const blocks = [];
  const tools = new Map();
  const approvals = new Map();
  let firstMessage = null;
  // Prompts answer against the execution they were raised in, never against
  // whatever happens to be open when the operator gets round to them.
  const sessionId = detail.execution_id || state.activeId;

  for (const event of detail.events || []) {
    const value = event.value;
    const time = event.time;
    switch (event.type) {
      case "WORKFLOW_RESUMED": {
        const message = value && typeof value.message === "string" ? value.message : null;
        if (message && message.trim()) {
          if (firstMessage === null) firstMessage = message.trim();
          blocks.push({ kind: "user", text: message, time });
        }
        break;
      }
      case "WORKFLOW_PAUSED": {
        const output = value && typeof value === "object" ? value.output : null;
        if (!output || typeof output !== "object") break;
        if (output.type === "chat_response" && output.content) {
          blocks.push({ kind: "agent", text: String(output.content), time });
        } else if (output.type === "elicitation") {
          blocks.push({
            kind: "elicitation",
            data: output,
            time,
            answered: false,
            sessionId,
          });
        } else if (output.type === "session_end") {
          blocks.push({
            kind: "system",
            text: `Session ended: ${output.reason || ""} (${output.turns || 0} turns)`,
            time,
          });
        }
        break;
      }
      case "TASK_STARTED": {
        if (INTERNAL_TASK.test(event.name || "")) break;
        const tool = {
          id: event.source_id,
          name: event.name,
          args: value,
          output: undefined,
          status: "running",
          started: toMs(time),
          ended: null,
        };
        tools.set(event.source_id, tool);
        blocks.push({ kind: "tool", tool });
        break;
      }
      case "TASK_COMPLETED":
      case "TASK_FAILED":
      case "TASK_CANCELLED": {
        let tool = tools.get(event.source_id);
        if (!tool) {
          // A completion with no start: every task begun in a resumed run
          // looked like this until #244 (the engine suppressed TASK_STARTED
          // for the whole resumed run), which is every tool an agent called
          // after its first turn. Executions logged before that fix still
          // read back this way, so render from the completion alone.
          if (INTERNAL_TASK.test(event.name || "")) break;
          tool = {
            id: event.source_id,
            name: event.name,
            args: undefined,
            output: undefined,
            status: "running",
            // No start event means no start time; leave it null so the
            // duration is omitted rather than invented.
            started: null,
            ended: null,
          };
          tools.set(event.source_id, tool);
          blocks.push({ kind: "tool", tool });
        }
        tool.status =
          event.type === "TASK_COMPLETED"
            ? "success"
            : event.type === "TASK_FAILED"
              ? "failed"
              : "cancelled";
        tool.output = unwrapOutput(value);
        tool.ended = toMs(time);
        break;
      }
      case "TASK_AWAITING_APPROVAL": {
        const block = {
          kind: "approval",
          time,
          data: {
            execution_id: detail.execution_id,
            task_call_id: (value && value.task_call_id) || event.source_id,
            task_name: (value && value.task_name) || event.name,
            target_value: value ? value.target_value : null,
          },
        };
        approvals.set(event.source_id, block);
        blocks.push(block);
        break;
      }
      case "TASK_APPROVED":
      case "TASK_REJECTED": {
        const block = approvals.get(event.source_id);
        if (block) block.decided = event.type === "TASK_APPROVED" ? "approved" : "rejected";
        break;
      }
      case "WORKFLOW_FAILED": {
        const message = value && typeof value === "object" ? value.message : value;
        blocks.push({ kind: "error", text: String(message || "Execution failed"), time });
        break;
      }
      case "WORKFLOW_CANCELLED":
        blocks.push({ kind: "system", text: "Execution cancelled", time });
        break;
      default:
        break;
    }
  }

  state.blocks = blocks;
  state.tools = tools;
  state.localTitle = firstMessage ? truncate(firstMessage) : null;
  state.stream = "";
  state.reasoning = null;
  state.chatVersion += 1;
}

/* ── the reducer ───────────────────────────────────────────────────── */

/**
 * The single entry point for live events; every kind lands here.
 *
 * Frames are addressed: the hub multiplexes every session this console
 * process watches, and a turn keeps streaming after the operator switches
 * away from the session that started it. Anything not addressed to the open
 * session is dropped here — appending it would write one session's tokens
 * into another's transcript, and a `log_delta` would replace it wholesale.
 */
function handleEvent(consoleEvent) {
  if (!consoleEvent || consoleEvent.session_id !== state.activeId) return;
  const kind = consoleEvent.kind;
  const data = consoleEvent.data || {};

  switch (kind) {
    case "token":
      state.stream += data.text || "";
      break;

    case "reasoning": {
      if (!state.reasoning) {
        state.reasoning = { kind: "reasoning", text: "", expanded: false };
        pushBlock(state.reasoning);
      }
      state.reasoning.text += data.text || "";
      state.chatVersion += 1;
      break;
    }

    case "tool_start": {
      const tool = {
        id: data.id || `tool-${state.tools.size}`,
        name: data.name || "tool",
        args: data.args,
        output: undefined,
        status: "running",
        started: Date.now(),
        ended: null,
      };
      state.tools.set(tool.id, tool);
      pushBlock({ kind: "tool", tool });
      break;
    }

    case "tool_done": {
      const tool = state.tools.get(data.id);
      if (tool) {
        tool.status = data.status === "success" ? "success" : data.status || "done";
        tool.ended = Date.now();
        state.chatVersion += 1;
      }
      break;
    }

    case "chat_response": {
      const content = data.content || state.stream;
      state.stream = "";
      state.reasoning = null;
      if (content) pushBlock({ kind: "agent", text: String(content), time: null });
      break;
    }

    case "approval_required": {
      pushBlock({
        kind: "approval",
        time: data.requested_at || null,
        data: {
          execution_id: data.execution_id || state.activeId,
          task_call_id: data.task_call_id,
          task_name: data.task_name,
          target_value: data.target_value ?? null,
        },
      });
      // The drawer's badge should light up without waiting for the poll.
      refreshApprovals();
      break;
    }

    case "elicitation":
      pushBlock({
        kind: "elicitation",
        data,
        time: null,
        answered: false,
        sessionId: consoleEvent.session_id,
      });
      break;

    case "plan":
      state.plan = data.plan || null;
      break;

    case "subagent": {
      const id = data.call_id || "subagent";
      const card = state.subagents.get(id) || {
        id,
        agent: "",
        brief: "",
        result_tail: "",
        status: "started",
        started: Date.now(),
      };
      // The closing frame carries only call_id/status/result_tail, so merge
      // rather than replace or the agent name and brief would disappear.
      if (data.agent) card.agent = data.agent;
      if (data.brief) card.brief = data.brief;
      if (data.result_tail) card.result_tail = data.result_tail;
      if (data.status) card.status = data.status;
      state.subagents.set(id, card);
      break;
    }

    case "session_end":
      pushBlock({
        kind: "system",
        text: `Session ended: ${data.reason || ""} (${data.turns || 0} turns)`,
        time: null,
      });
      break;

    case "error":
      pushBlock({ kind: "error", text: data.message || "Unknown error", time: null });
      break;

    case "log_delta": {
      if (data.detail) {
        applyDetail(data.detail);
      } else {
        // Reconciliation failed: keep the last known state, say so, and let
        // the next turn (or the poll) catch up.
        pushBlock({
          kind: "error",
          text: `Could not reconcile the execution log: ${data.error || "unknown error"}`,
          time: null,
        });
      }
      refreshSessions();
      break;
    }

    default:
      break;
  }

  scheduleRender();
}

/* ── render ────────────────────────────────────────────────────────── */

let renderQueued = false;

function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    render();
  });
}

function permissionTooltip() {
  return state.missingPermission
    ? `Read-only: requires ${state.missingPermission}`
    : "Read-only: this token cannot make changes";
}

/** Disable a state-changing control in read-only mode, never silently. */
function guard(node) {
  if (!state.canWrite) {
    node.disabled = true;
    node.title = permissionTooltip();
  }
  return node;
}

function bucketOf(row) {
  const executionState = String(row.state || "").toUpperCase();
  if (executionState === "FAILED" || executionState === "CANCELLED") return "failed";
  if (executionState === "COMPLETED") return "done";
  if (state.approvals.some((approval) => approval.execution_id === row.execution_id)) {
    return "waiting";
  }
  if (executionState === "PAUSED") return "idle";
  if (RUNNING_STATES.has(executionState)) return "running";
  return "idle";
}

function sessionTitle(row) {
  if (row.name) return row.name;
  if (row.derived_title) return row.derived_title;
  if (row.execution_id === state.activeId && state.localTitle) return state.localTitle;
  return `${row.agent_name} · ${fmtDate(row.started_at) || row.execution_id.slice(0, 8)}`;
}

function activeRow() {
  return state.sessions.find((row) => row.execution_id === state.activeId) || null;
}

function planCounts() {
  const steps = (state.plan && state.plan.steps) || [];
  const done = steps.filter((step) => step.status === "completed").length;
  return { steps, done, total: steps.length };
}

/**
 * Rebuild a region only when its inputs changed.
 *
 * `render()` runs once per animation frame while tokens stream, and every
 * region here contains focusable controls — rebuilding the rail or the plan
 * list on each frame would both burn frames and throw away the operator's
 * keyboard focus mid-turn.
 */
const memoized = new Map();

function memo(region, signature, build) {
  const key = JSON.stringify(signature);
  if (memoized.get(region) === key) return;
  memoized.set(region, key);
  build();
}

function invalidate(region) {
  memoized.delete(region);
}

function render() {
  renderTopbar();
  renderRail();
  renderStageHead();
  renderChat();
  renderComposer();
  renderContext();
  renderDrawer();
  renderModal();
}

function renderTopbar() {
  const running = state.sessions.filter((row) => bucketOf(row) === "running").length;
  clear(dom.runningStat);
  dom.runningStat.append(
    el("span", { class: "ok", text: GLYPH.running }),
    ` ${running} running`,
  );

  const pending = state.approvals.length;
  dom.approvalsBtn.textContent = `⚠ ${pending} approvals`;
  dom.approvalsBtn.className = pending ? "btn btn-alert" : "btn";
  dom.approvalsBtn.setAttribute("aria-expanded", String(state.drawerOpen));

  dom.newSessionBtn.disabled = false;
  dom.newSessionBtn.title = "";
  guard(dom.newSessionBtn);

  const where = [state.agent, state.serverUrl].filter(Boolean).join(" @ ");
  dom.serverLabel.textContent = state.notice ? state.notice : where;
  dom.serverLabel.className = state.notice ? "topbar-meta fail" : "topbar-meta";
  dom.themeBtn.textContent = currentTheme() === "dark" ? "◐" : "☀";
}

function railRow(row) {
  const bucket = bucketOf(row);
  const isActive = row.execution_id === state.activeId;
  const counts = planCounts();
  const sub = [];

  if (isActive && counts.total) sub.push(`${counts.done}/${counts.total}`);
  else if (bucket === "waiting") sub.push("⚠ approval");
  else if (bucket === "idle") sub.push("idle");
  else if (bucket === "running") sub.push(String(row.state || "").toLowerCase());
  else if (bucket === "failed") sub.push(String(row.state || "failed").toLowerCase());
  else sub.push("done");
  const age = fmtAge(row.started_at);
  if (age) sub.push(age);

  const glyphClass =
    bucket === "running"
      ? "ok"
      : bucket === "waiting"
        ? "figure"
        : bucket === "failed"
          ? "fail"
          : "dim";

  return el(
    "button",
    {
      type: "button",
      // Only a normally-finished session dims. A failed one keeps full
      // contrast and its danger-toned glyph -- it is an outcome to notice,
      // not history to fade out.
      class: `rail-row${isActive ? " selected" : ""}${bucket === "done" ? " done" : ""}`,
      onclick: () => openSession(row.execution_id),
      title: sessionTitle(row),
    },
    el(
      "span",
      { class: "rail-title" },
      el("span", { class: glyphClass, text: GLYPH[bucket] }),
      " ",
      truncate(sessionTitle(row), 28),
    ),
    el(
      "span",
      { class: `rail-sub${bucket === "waiting" ? " waiting" : ""}` },
      sub.join(" · "),
      " ",
      el("span", { class: "rail-agent", text: row.agent_name }),
    ),
  );
}

function renderRail() {
  const counts = planCounts();
  memo(
    "rail",
    [
      state.sessions,
      state.approvals.map((approval) => approval.execution_id),
      state.activeId,
      state.localTitle,
      state.canWrite,
      counts.done,
      counts.total,
    ],
    buildRail,
  );
}

function buildRail() {
  // Failed gets its own heading: bucketing it under DONE would file a
  // session that needs attention with the ones that need none.
  const groups = [
    { name: "ACTIVE", rows: [] },
    { name: "IDLE", rows: [] },
    { name: "DONE", rows: [] },
    { name: "FAILED", rows: [] },
  ];
  for (const row of state.sessions) {
    const bucket = bucketOf(row);
    if (bucket === "running" || bucket === "waiting") groups[0].rows.push(row);
    else if (bucket === "idle") groups[1].rows.push(row);
    else if (bucket === "done") groups[2].rows.push(row);
    else groups[3].rows.push(row);
  }

  clear(dom.rail);
  dom.rail.appendChild(label("Sessions"));
  if (!state.sessions.length) {
    dom.rail.appendChild(empty("No sessions yet — press + New session"));
  }
  for (const group of groups) {
    if (!group.rows.length) continue;
    const section = el("div", { class: "rail-group" }, label(group.name));
    for (const row of group.rows) section.appendChild(railRow(row));
    dom.rail.appendChild(section);
  }
  dom.rail.appendChild(
    guard(
      el("button", {
        type: "button",
        class: "btn btn-accent rail-new",
        text: "+ new session",
        onclick: openModal,
      }),
    ),
  );
}

function renderStageHead() {
  const row = activeRow();
  const counts = planCounts();
  memo(
    "stage-head",
    [
      state.activeId,
      row ? sessionTitle(row) : state.localTitle,
      row ? row.agent_name : state.agent,
      state.renaming,
      state.canWrite,
      state.missingPermission,
      state.stopConfirm,
      row ? row.state : null,
      counts.done,
      counts.total,
    ],
    buildStageHead,
  );
}

/**
 * Whether a session can still be stopped.
 *
 * Non-terminal, not merely running: a PAUSED session is the normal state of a
 * healthy chat waiting on its next turn, and ending one is exactly what the
 * control is for. Only an execution that already reached an end state has
 * nothing left to cancel.
 */
function isStoppable(row) {
  return Boolean(row) && !TERMINAL_STATES.has(String(row.state || "").toUpperCase());
}

function buildStageHead() {
  clear(dom.stageHead);
  const row = activeRow();
  if (!row && !state.activeId) {
    dom.stageHead.appendChild(el("span", { class: "stage-title dim", text: "No session open" }));
    return;
  }

  const title = row ? sessionTitle(row) : state.localTitle || state.activeId;
  if (state.renaming) {
    const input = el("input", {
      class: "rename-input",
      value: row && row.name ? row.name : title,
      "aria-label": "Session name",
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        commitRename(input.value);
      } else if (event.key === "Escape") {
        state.renaming = false;
        scheduleRender();
      }
    });
    input.addEventListener("blur", () => {
      state.renaming = false;
      scheduleRender();
    });
    dom.stageHead.appendChild(input);
    // Focus after insertion; the node does not exist before this point.
    requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
  } else {
    dom.stageHead.appendChild(
      el(
        "span",
        {},
        el("span", { class: "stage-title", text: title }),
        " ",
        guard(
          el("button", {
            type: "button",
            class: "btn btn-quiet",
            text: "✎",
            "aria-label": "Rename session",
            title: state.canWrite ? "Rename session" : "",
            onclick: () => {
              state.renaming = true;
              scheduleRender();
            },
          }),
        ),
        " ",
        isStoppable(row)
          ? guard(
              el("button", {
                type: "button",
                class: `btn btn-quiet${state.stopConfirm ? " fail" : ""}`,
                // Two clicks, not a modal: cancelling a session cannot be
                // undone, and a header button is one stray click away from
                // the rename pencil beside it.
                text: state.stopConfirm ? "stop — sure?" : "◼ stop",
                "aria-label": "Stop session",
                title: state.canWrite ? "Cancel this session's execution" : "",
                onclick: () => stopSession(),
              }),
            )
          : null,
      ),
    );
  }

  const counts = planCounts();
  dom.stageHead.appendChild(
    el(
      "span",
      { class: "stage-meta" },
      row ? row.agent_name : state.agent || "",
      counts.total ? " · " : "",
      counts.total
        ? el("span", { class: "figure", text: `step ${counts.done}/${counts.total}` })
        : null,
    ),
  );
}

function toolBlock(tool) {
  const expanded = state.expandedTools.has(tool.id);
  const running = tool.status === "running";
  const preview = inlineArgs(tool.args);
  const argsPreview = preview === "{}" || preview === "null" ? "" : preview;
  const statusNode = running
    ? el(
        "span",
        { class: "figure", dataset: { elapsedFrom: String(tool.started || Date.now()) } },
        fmtElapsed(Date.now() - (tool.started || Date.now())),
      )
    : el(
        "span",
        {
          class: tool.status === "success" ? "ok" : "fail",
          text: tool.status === "success" ? "✓" : "✗",
        },
      );

  const head = el(
    "button",
    {
      type: "button",
      class: "tool-head",
      onclick: () => {
        toggle(state.expandedTools, tool.id);
        scheduleRender();
      },
    },
    expanded ? "▾" : "▸",
    running ? " ⚙ " : " ",
    el("span", { class: "tool-name", text: tool.name }),
    argsPreview ? el("span", { class: "tool-args-inline", text: `· ${argsPreview}` }) : null,
    statusNode,
    !running && tool.ended && tool.started
      ? el("span", { class: "tool-duration", text: fmtDuration(tool.ended - tool.started) })
      : null,
    running ? el("span", { class: "caret", text: "▌" }) : null,
    // A tool recovered from its completion alone has no start time; its end
    // time is the honest clock to show, ahead of "now".
    el("span", { class: "tool-time", text: fmtClock(tool.started || tool.ended || Date.now()) }),
  );

  const node = el("div", { class: `tool${expanded ? " expanded" : ""}` }, head);
  if (!expanded) return node;

  const body = el("div", { class: "tool-body" }, label("Args"), el("pre", { text: pretty(tool.args) }));
  if (tool.output !== undefined) {
    const text = pretty(tool.output);
    const full = state.fullOutputs.has(tool.id);
    const shown = full ? text : text.slice(0, OUTPUT_LIMIT);
    body.append(label("Output"), el("pre", { text: shown }));
    if (text.length > OUTPUT_LIMIT) {
      body.appendChild(
        el("button", {
          type: "button",
          class: "show-full",
          text: full ? `(${sizeOf(text)} · show less)` : `(${sizeOf(text)} · show full)`,
          onclick: () => {
            toggle(state.fullOutputs, tool.id);
            scheduleRender();
          },
        }),
      );
    }
  }
  node.appendChild(body);
  return node;
}

function approvalPrompt(block) {
  const data = block.data;
  const key = `${data.execution_id}|${data.task_call_id}`;
  const note = block.decided ? `Already ${block.decided}.` : state.decisions.get(key);
  const prompt = el(
    "div",
    { class: "prompt" },
    el(
      "div",
      {},
      "⚠ ",
      el("span", { class: "approval-task", text: data.task_name || "task" }),
      " requires approval",
    ),
    data.target_value ? el("div", { class: "approval-target", text: `→ ${data.target_value}` }) : null,
  );
  if (note) {
    prompt.appendChild(el("div", { class: "prompt-note", text: note }));
    return prompt;
  }
  prompt.appendChild(approvalActions(data));
  return prompt;
}

function approvalActions(data) {
  const decide = (decision) => decideApproval(data.execution_id, data.task_call_id, decision);
  const actions = el(
    "div",
    { class: "prompt-actions" },
    guard(
      el("button", {
        type: "button",
        class: "btn",
        text: "Approve",
        onclick: () => decide({ approve: true, always: false, always_for_target: false }),
      }),
    ),
    guard(
      el("button", {
        type: "button",
        class: "btn",
        text: "Reject",
        onclick: () => decide({ approve: false, always: false, always_for_target: false }),
      }),
    ),
    guard(
      el("button", {
        type: "button",
        class: "btn",
        text: "Always",
        title: state.canWrite ? "Approve every later call of this task in this execution" : "",
        onclick: () => decide({ approve: true, always: true, always_for_target: false }),
      }),
    ),
  );
  if (data.target_value) {
    actions.appendChild(
      guard(
        el("button", {
          type: "button",
          class: "btn",
          text: "Always for target",
          title: state.canWrite ? `Approve later calls targeting ${data.target_value}` : "",
          onclick: () => decide({ approve: true, always: false, always_for_target: true }),
        }),
      ),
    );
  }
  return actions;
}

function elicitationPrompt(block) {
  const data = block.data || {};
  const prompt = el(
    "div",
    { class: "prompt" },
    el(
      "div",
      {},
      "⚡ ",
      el("b", { text: data.server_name || "" }),
      data.server_name ? ": " : "",
      data.message || "",
    ),
  );

  if (data.url) {
    // The url comes from a remote MCP server: only absolute http(s) becomes a
    // link, so a javascript:/relative payload can never navigate this origin.
    let browsable = false;
    try {
      const parsed = new URL(data.url);
      browsable = parsed.protocol === "http:" || parsed.protocol === "https:";
    } catch (err) {
      browsable = false;
    }
    prompt.appendChild(
      browsable
        ? el("div", {}, el("a", { href: data.url, target: "_blank", rel: "noopener noreferrer", text: data.url }))
        : el("div", { class: "prompt-note", text: `Refusing a non-browsable url: ${data.url}` }),
    );
  }

  if (block.answered) {
    prompt.appendChild(el("div", { class: "prompt-note", text: `Answered: ${block.answered}` }));
    return prompt;
  }

  const actions = el("div", { class: "prompt-actions" });
  for (const action of ["accept", "decline", "cancel"]) {
    actions.appendChild(
      guard(
        el("button", {
          type: "button",
          class: "btn",
          text: action.charAt(0).toUpperCase() + action.slice(1),
          onclick: () => respondToElicitation(block, action),
        }),
      ),
    );
  }
  prompt.appendChild(actions);
  return prompt;
}

function reasoningBlock(block) {
  const node = el(
    "div",
    { class: "reasoning" },
    el("button", {
      type: "button",
      class: "reasoning-toggle",
      text: `${block.expanded ? "▾" : "▸"} thinking (${block.text.split("\n").length} lines)`,
      onclick: () => {
        block.expanded = !block.expanded;
        state.chatVersion += 1;
        scheduleRender();
      },
    }),
  );
  if (block.expanded) node.appendChild(el("div", { class: "reasoning-body", text: block.text }));
  return node;
}

function blockNode(block) {
  switch (block.kind) {
    case "user":
      return el("div", { class: "msg-user" }, el("span", { class: "who", text: "you:" }), ` ${block.text}`);
    case "agent": {
      const node = el("div", { class: "msg-agent" });
      appendText(node, block.text);
      return node;
    }
    case "tool":
      return toolBlock(block.tool);
    case "approval":
      return approvalPrompt(block);
    case "elicitation":
      return elicitationPrompt(block);
    case "reasoning":
      return reasoningBlock(block);
    case "error":
      return el("div", { class: "msg-error", text: `✗ ${block.text}` });
    case "system":
    default:
      return el("div", { class: "msg-system", text: block.text });
  }
}

let renderedChatVersion = -1;
let streamNode = null;

function renderChat() {
  const atBottom = dom.chat.scrollHeight - dom.chat.scrollTop - dom.chat.clientHeight < 60;

  // Token bursts must not rebuild the transcript — patch the live node and
  // leave the rest of the DOM (and any text selection in it) alone.
  if (renderedChatVersion === state.chatVersion && streamNode) {
    setStreamText(streamNode, state.stream);
    if (atBottom) dom.chat.scrollTop = dom.chat.scrollHeight;
    return;
  }
  renderedChatVersion = state.chatVersion;
  streamNode = null;

  clear(dom.chat);
  if (!state.activeId) {
    dom.chat.appendChild(
      el("div", { class: "chat-empty", text: "No session open — pick one from the rail, or press + new session" }),
    );
    return;
  }
  for (const block of state.blocks) dom.chat.appendChild(blockNode(block));

  if (state.stream) {
    streamNode = el("div", { class: "msg-agent" });
    setStreamText(streamNode, state.stream);
    dom.chat.appendChild(streamNode);
  } else if (state.busy) {
    dom.chat.appendChild(el("div", { class: "msg-system", text: "working…" }));
  }
  if (atBottom) dom.chat.scrollTop = dom.chat.scrollHeight;
}

function setStreamText(node, text) {
  clear(node);
  appendText(node, text);
  node.appendChild(el("span", { class: "caret", text: "▌" }));
}

function renderComposer() {
  const row = activeRow();
  const agent = row ? row.agent_name : state.agent;
  dom.input.placeholder = agent ? `Message ${agent}…` : "Message…";
  dom.input.disabled = !state.canWrite || !state.activeId || state.busy;
  dom.input.title = state.canWrite ? "" : permissionTooltip();
}

function renderPlan() {
  const section = el("div", { class: "panel-section" }, label("Plan"));
  const { steps, done, total } = planCounts();
  if (!total) {
    section.appendChild(empty("No plan yet"));
    return section;
  }
  section.append(
    el("div", { class: "hero" }, String(done), el("span", { class: "hero-total", text: `/${total}` })),
    el("div", { class: "bar" }, el("div", { class: "bar-fill" , style: `width:${Math.round((done / total) * 100)}%` })),
  );
  for (const step of steps) {
    const expanded = state.expandedSteps.has(step.name);
    const status = step.status || "pending";
    const glyph =
      status === "completed" ? "✓" : status === "in_progress" ? "▶" : status === "failed" ? "✗" : "○";
    const glyphClass =
      status === "completed" ? "ok" : status === "in_progress" ? "figure" : status === "failed" ? "fail" : "dim";
    const node = el(
      "button",
      {
        type: "button",
        class: `step ${status === "in_progress" ? "active" : status === "completed" ? "done" : "pending"}${expanded ? " expanded" : ""}`,
        onclick: () => {
          toggle(state.expandedSteps, step.name);
          scheduleRender();
        },
      },
      el("span", { class: "step-text" }, el("span", { class: glyphClass, text: glyph }), ` ${step.name}`),
    );
    if (expanded) {
      if (step.description) node.appendChild(el("div", { class: "step-detail", text: step.description }));
      if (step.result) node.appendChild(el("div", { class: "step-detail", text: step.result }));
      if (step.error) node.appendChild(el("div", { class: "step-detail fail", text: step.error }));
      node.appendChild(el("div", { class: "step-status", text: status.replace("_", " ") }));
    }
    section.appendChild(node);
  }
  return section;
}

function renderActivity() {
  const section = el("div", { class: "panel-section" }, label("Activity"));
  const tools = [...state.tools.values()];
  if (!tools.length) {
    section.appendChild(empty("No tool calls yet"));
    return section;
  }
  for (const tool of tools) {
    const running = tool.status === "running";
    const figure = running
      ? el(
          "span",
          { class: "figure", dataset: { elapsedFrom: String(tool.started || Date.now()) } },
          fmtElapsed(Date.now() - (tool.started || Date.now())),
        )
      : el("span", {
          class: "muted mono",
          text: tool.ended && tool.started ? fmtElapsed(tool.ended - tool.started) : "",
        });
    section.appendChild(
      el(
        "button",
        {
          type: "button",
          class: "activity-row",
          title: tool.name,
          // The ACTIVITY row and the chat block are the same object: expanding
          // one expands the other, and brings it into view.
          onclick: () => {
            toggle(state.expandedTools, tool.id);
            scheduleRender();
            requestAnimationFrame(() => {
              const target = [...dom.chat.querySelectorAll(".tool")].find((node) =>
                node.textContent.includes(tool.name),
              );
              if (target) target.scrollIntoView({ block: "nearest" });
            });
          },
        },
        el("span", { class: running ? "figure" : "dim", text: running ? "⚙" : "·" }),
        el("span", { class: "activity-name", text: tool.name }),
        figure,
      ),
    );
  }
  return section;
}

function renderSubagents() {
  const section = el("div", { class: "panel-section" }, label("Sub-agents"));
  const cards = [...state.subagents.values()];
  if (!cards.length) {
    section.appendChild(empty("No sub-agents"));
    return section;
  }
  for (const card of cards) {
    const running = card.status === "started";
    const expanded = state.expandedSubagents.has(card.id) || running;
    const glyph =
      card.status === "failed" ? "✗" : card.status === "paused" ? "◐" : running ? "●" : "○";
    const glyphClass = card.status === "failed" ? "fail" : running ? "ok" : "dim";
    const node = el(
      "div",
      { class: "subagent" },
      el(
        "button",
        {
          type: "button",
          class: "subagent-head",
          onclick: () => {
            toggle(state.expandedSubagents, card.id);
            scheduleRender();
          },
        },
        el("span", { class: glyphClass, text: glyph }),
        el("span", { class: "subagent-name", text: card.agent || card.id }),
        running
          ? el(
              "span",
              { class: "figure", dataset: { elapsedFrom: String(card.started) } },
              fmtElapsed(Date.now() - card.started),
            )
          : el("span", { class: "dim", text: card.status }),
      ),
    );
    if (expanded && (card.brief || card.result_tail)) {
      const body = el("div", { class: "subagent-body" });
      if (card.brief) body.append(label("Brief"), el("div", { text: card.brief }));
      if (card.result_tail) {
        body.append(label("Output tail"), el("div", { class: "subagent-tail", text: card.result_tail }));
      }
      node.appendChild(body);
    }
    section.appendChild(node);
  }
  return section;
}

function renderContext() {
  memo(
    "context",
    [
      state.plan,
      [...state.tools.values()].map((tool) => [
        tool.id,
        tool.name,
        tool.status,
        tool.started,
        tool.ended,
        tool.output !== undefined,
      ]),
      [...state.subagents.values()],
      [...state.expandedTools],
      [...state.expandedSteps],
      [...state.expandedSubagents],
      state.fullOutputs.size,
    ],
    () => {
      clear(dom.context);
      dom.context.append(renderPlan(), renderActivity(), renderSubagents());
    },
  );
}

let renderedDrawerSignature = null;

function renderDrawer() {
  dom.drawer.hidden = !state.drawerOpen;
  if (!state.drawerOpen) {
    renderedDrawerSignature = null;
    return;
  }
  const signature = JSON.stringify([state.approvals, [...state.decisions], state.canWrite]);
  if (signature === renderedDrawerSignature) return;
  renderedDrawerSignature = signature;

  clear(dom.drawer);
  dom.drawer.appendChild(
    el(
      "div",
      { class: "drawer-head" },
      label("Pending approvals"),
      el("button", { type: "button", class: "btn btn-quiet", text: "✕", "aria-label": "Close approvals", onclick: closeDrawer }),
    ),
  );
  if (!state.approvals.length) {
    dom.drawer.appendChild(empty("No pending approvals"));
    return;
  }
  for (const approval of state.approvals) {
    const key = `${approval.execution_id}|${approval.task_call_id}`;
    const note = state.decisions.get(key);
    const card = el(
      "div",
      { class: "approval" },
      el("div", { class: "approval-task", text: approval.task_name }),
      approval.target_value ? el("div", { class: "approval-target", text: `→ ${approval.target_value}` }) : null,
      el("div", {
        class: "approval-meta",
        text: `${approval.execution_id.slice(0, 8)} · ${fmtAge(approval.requested_at) || "just now"}`,
      }),
    );
    card.appendChild(note ? el("div", { class: "prompt-note", text: note }) : approvalActions(approval));
    dom.drawer.appendChild(card);
  }
}

let modalShell = null;

function renderModal() {
  dom.modal.hidden = !state.modalOpen;
  if (!state.modalOpen) {
    modalShell = null;
    return;
  }
  if (!modalShell) buildModal();
  renderAgentList();
}

function buildModal() {
  clear(dom.modal);
  const filter = el("input", { class: "field", placeholder: "🔍 filter agents…", "aria-label": "Filter agents" });
  filter.addEventListener("input", () => {
    state.agentFilter = filter.value;
    renderAgentList();
  });
  filter.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveAgentSelection(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      startSession();
    }
  });

  const list = el("div", { class: "agent-list" });
  const nameInput = el("input", { class: "field", "aria-label": "Session name (optional)" });
  nameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      startSession();
    }
  });

  const startBtn = guard(
    el("button", { type: "button", class: "btn btn-solid", text: "Start session", onclick: startSession }),
  );

  modalShell = { filter, list, nameInput, startBtn };
  dom.modal.appendChild(
    el(
      "div",
      { class: "modal" },
      label("New session"),
      filter,
      list,
      label("Name (optional)"),
      nameInput,
      el("div", { class: "modal-note", text: "unnamed sessions auto-title from your first message" }),
      el(
        "div",
        { class: "modal-actions" },
        el("button", { type: "button", class: "btn btn-quiet", text: "Cancel", onclick: closeModal }),
        startBtn,
      ),
    ),
  );
  requestAnimationFrame(() => filter.focus());
}

function filteredAgents() {
  const needle = state.agentFilter.trim().toLowerCase();
  if (!needle) return state.agents;
  return state.agents.filter((agent) =>
    `${agent.name} ${agent.model || ""} ${agent.description || ""}`.toLowerCase().includes(needle),
  );
}

function moveAgentSelection(step) {
  const agents = filteredAgents();
  if (!agents.length) return;
  const current = agents.findIndex((agent) => agent.name === state.agentSelected);
  const next = Math.min(agents.length - 1, Math.max(0, (current === -1 ? 0 : current) + step));
  state.agentSelected = agents[next].name;
  renderAgentList();
}

function renderAgentList() {
  if (!modalShell) return;
  const agents = filteredAgents();
  if (!agents.some((agent) => agent.name === state.agentSelected)) {
    state.agentSelected = agents.length ? agents[0].name : null;
  }
  memo("agent-list", [agents, state.agentSelected, state.canWrite], () =>
    buildAgentList(agents),
  );
}

function buildAgentList(agents) {
  clear(modalShell.list);
  if (!agents.length) {
    modalShell.list.appendChild(empty("No agents match"));
  }
  for (const agent of agents) {
    modalShell.list.appendChild(
      el(
        "button",
        {
          type: "button",
          class: `agent-row${agent.name === state.agentSelected ? " selected" : ""}`,
          onclick: () => {
            state.agentSelected = agent.name;
            renderAgentList();
          },
        },
        el("b", { text: agent.name }),
        " ",
        el("span", { class: "agent-model", text: `· ${agent.model || "?"}` }),
        agent.description ? el("div", { class: "agent-desc", text: agent.description }) : null,
      ),
    );
  }
  modalShell.nameInput.placeholder = `${state.agentSelected || "agent"} · ${fmtDate(new Date().toISOString())}`;
  modalShell.startBtn.disabled = !state.agentSelected || !state.canWrite;
}

function toggle(set, key) {
  if (set.has(key)) set.delete(key);
  else set.add(key);
}

/* ── actions ───────────────────────────────────────────────────────── */

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (err) {
    /* storage denied — the choice simply does not survive a reload */
  }
  scheduleRender();
}

function openModal() {
  state.modalOpen = true;
  state.agentFilter = "";
  modalShell = null;
  // The modal is rebuilt from scratch on each open, so the memoized agent
  // list must be too — otherwise its signature still matches the previous
  // open and the freshly built (empty) list never gets filled.
  invalidate("agent-list");
  scheduleRender();
  refreshAgents();
}

function closeModal() {
  state.modalOpen = false;
  scheduleRender();
}

function openDrawer() {
  state.drawerOpen = true;
  renderedDrawerSignature = null;
  scheduleRender();
  refreshApprovals();
}

function closeDrawer() {
  state.drawerOpen = false;
  scheduleRender();
}

async function refreshSessions() {
  try {
    state.sessions = await api.sessions();
    clearNotice("sessions");
  } catch (err) {
    setNotice("sessions", `sessions: ${errorText(err)}`);
  }
  scheduleRender();
}

async function refreshApprovals() {
  try {
    state.approvals = await api.approvals();
    clearNotice("approvals");
  } catch (err) {
    setNotice("approvals", `approvals: ${errorText(err)}`);
  }
  scheduleRender();
}

async function refreshAgents() {
  try {
    state.agents = await api.agents();
    clearNotice("agents");
  } catch (err) {
    state.agents = [];
    setNotice("agents", `agents: ${errorText(err)}`);
  }
  renderAgentList();
  scheduleRender();
}

async function openSession(id) {
  if (state.activeId !== id) {
    // Stop pumping the session being left. The turn itself keeps running
    // server-side (other subscribers still need its log_delta) — this only
    // detaches *this* browser from a stream it is about to ignore, and the
    // turn's own finalizer clears `busy` when the abort lands.
    if (inflightTurn) inflightTurn.controller.abort();
    // A different session shares none of the previous one's overlay state.
    state.activeId = id;
    state.blocks = [];
    state.tools = new Map();
    state.plan = null;
    state.subagents = new Map();
    state.stream = "";
    state.reasoning = null;
    state.localTitle = null;
    state.stopConfirm = false;
    state.chatVersion += 1;
  }
  scheduleRender();
  try {
    applyDetail(await api.detail(id));
  } catch (err) {
    state.blocks = [{ kind: "error", text: `Could not open session: ${errorText(err)}` }];
    state.chatVersion += 1;
  }
  scheduleRender();
}

async function refreshDetail() {
  if (!state.activeId || state.busy) return;
  try {
    applyDetail(await api.detail(state.activeId));
    scheduleRender();
  } catch (err) {
    /* transient; the next tick retries */
  }
}

async function stopSession() {
  const row = activeRow();
  if (!state.activeId || !state.canWrite || !isStoppable(row)) return;
  if (!state.stopConfirm) {
    state.stopConfirm = true;
    scheduleRender();
    return;
  }
  state.stopConfirm = false;
  beginAction();
  const target = state.activeId;
  try {
    await api.stop(target);
  } catch (err) {
    setNotice("stop", `stop: ${errorText(err)}`);
  }
  await refreshSessions();
  if (state.activeId === target) await refreshDetail();
  scheduleRender();
}

async function commitRename(value) {
  const name = String(value || "").trim();
  state.renaming = false;
  if (!name || !state.activeId) {
    scheduleRender();
    return;
  }
  beginAction();
  try {
    await api.rename(state.activeId, name);
    await refreshSessions();
  } catch (err) {
    setNotice("rename", `rename: ${errorText(err)}`);
  }
  scheduleRender();
}

async function startSession() {
  if (!state.agentSelected || !state.canWrite) return;
  const name = modalShell ? modalShell.nameInput.value.trim() : "";
  if (modalShell) modalShell.startBtn.disabled = true;
  beginAction();
  try {
    const created = await api.create(state.agentSelected, name);
    closeModal();
    await refreshSessions();
    if (created && created.execution_id) await openSession(created.execution_id);
  } catch (err) {
    setNotice("new session", `new session: ${errorText(err)}`);
    if (modalShell) modalShell.startBtn.disabled = false;
    scheduleRender();
  }
}

async function decideApproval(executionId, callId, decision) {
  const key = `${executionId}|${callId}`;
  try {
    const result = await api.decide(executionId, callId, decision);
    state.decisions.set(
      key,
      result && result.result === "already_decided"
        ? "already decided elsewhere"
        : decision.approve
          ? "Approved."
          : "Rejected.",
    );
  } catch (err) {
    state.decisions.set(key, `Failed: ${errorText(err)}`);
  }
  renderedDrawerSignature = null;
  state.chatVersion += 1;
  await refreshApprovals();
  await refreshSessions();
  if (executionId === state.activeId) await refreshDetail();
  scheduleRender();
}

async function respondToElicitation(block, action) {
  const data = block.data || {};
  // The block's own session, not whichever one is open: an operator can
  // answer a prompt, switch sessions, and land the reply either way.
  const sessionId = block.sessionId || state.activeId;
  if (!sessionId) return;
  block.answered = action;
  beginAction();
  state.chatVersion += 1;
  scheduleRender();
  try {
    // Same payload shape the legacy single-agent page used, resumed against
    // this session's own execution rather than the process-level one.
    await api.elicitation(sessionId, {
      elicitation_response: {
        elicitation_id: data.elicitation_id || "",
        action,
      },
    });
    await refreshDetail();
  } catch (err) {
    block.answered = false;
    state.chatVersion += 1;
    setNotice("elicitation", `elicitation: ${errorText(err)}`);
  }
  scheduleRender();
}

async function consumeStream(response, onFrame) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const raw of lines) {
      const line = raw.trim();
      if (!line.startsWith("data:")) continue; // ": ping" keep-alives, blank separators
      try {
        onFrame(JSON.parse(line.slice(5)));
      } catch (err) {
        /* a frame we cannot parse is dropped; log_delta still reconciles */
      }
    }
  }
}

// The turn this browser is currently streaming, if any: {id, controller}.
// One at a time — the composer is disabled while `state.busy` — and it is
// tracked outside `state` because only `openSession` (to abort it) and the
// turn's own finalizer (to retire it) ever touch it.
let inflightTurn = null;

async function sendMessage(text) {
  if (!state.activeId || !text.trim() || state.busy || !state.canWrite) return;
  // Every later step names the session this turn was sent to. `state.activeId`
  // can move under a running turn (the operator clicks another rail row), and
  // a reply appended to whatever is open by then lands in the wrong session.
  const turnId = state.activeId;
  const message = text;
  const controller = new AbortController();
  pushBlock({ kind: "user", text: message, time: new Date().toISOString() });
  state.busy = true;
  state.stream = "";
  state.reasoning = null;
  inflightTurn = { id: turnId, controller };
  scheduleRender();

  try {
    const response = await fetch(`/console/sessions/${encodeURIComponent(turnId)}/send`, {
      method: "POST",
      headers: WRITE_HEADERS,
      body: JSON.stringify({ text: message }),
      signal: controller.signal,
    });
    if (!response.ok) {
      let body = null;
      try {
        body = await response.json();
      } catch (err) {
        body = null;
      }
      noteFailure(response.status, body, true);
      if (state.activeId === turnId) {
        pushBlock({ kind: "error", text: errorText(new HttpError(response.status, body)) });
      }
    } else {
      await consumeStream(response, handleEvent);
    }
  } catch (err) {
    // An abort is this console's own doing (the operator switched sessions),
    // not a failure worth reporting in the transcript it switched to.
    if (state.activeId === turnId && !controller.signal.aborted) {
      pushBlock({ kind: "error", text: `Connection error: ${errorText(err)}` });
    }
  }

  // Only retire the turn still on record: an aborted turn's finalizer can run
  // after the operator already started a new one, and must not steal its
  // `busy` (which would re-enable the composer mid-turn).
  if (inflightTurn && inflightTurn.controller === controller) {
    inflightTurn = null;
    state.busy = false;
    state.stream = "";
  }
  scheduleRender();
  refreshSessions();
  // The composer was disabled for the turn, which drops focus; give it back
  // so the next message needs no click.
  if (state.canWrite && !state.busy) requestAnimationFrame(() => dom.input.focus());
}

/* ── wiring ────────────────────────────────────────────────────────── */

dom.themeBtn.addEventListener("click", toggleTheme);
dom.newSessionBtn.addEventListener("click", openModal);
dom.approvalsBtn.addEventListener("click", () => (state.drawerOpen ? closeDrawer() : openDrawer()));

dom.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    const text = dom.input.value;
    dom.input.value = "";
    dom.input.style.height = "auto";
    sendMessage(text);
  }
});

dom.input.addEventListener("input", () => {
  dom.input.style.height = "auto";
  dom.input.style.height = `${Math.min(dom.input.scrollHeight, 140)}px`;
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.modalOpen) closeModal();
  else if (state.drawerOpen) closeDrawer();
  else if (state.stopConfirm) {
    // Escape disarms a stop the operator thought better of.
    state.stopConfirm = false;
    scheduleRender();
  }
});

dom.modal.addEventListener("click", (event) => {
  if (event.target === dom.modal) closeModal();
});

// Live figures tick on their own so a running timer never forces a re-render.
setInterval(() => {
  for (const node of document.querySelectorAll("[data-elapsed-from]")) {
    node.textContent = fmtElapsed(Date.now() - Number(node.dataset.elapsedFrom));
  }
}, 1000);

setInterval(() => {
  refreshSessions();
  refreshApprovals();
  const row = activeRow();
  // Poll the open session only while something can still change in it.
  if (row && RUNNING_STATES.has(String(row.state || "").toUpperCase())) refreshDetail();
}, POLL_MS);

async function boot() {
  try {
    const consoleState = await api.state();
    state.agent = consoleState.agent;
    state.serverUrl = consoleState.server_url || "";
    state.canWrite = consoleState.can_write !== false;
    // The server's write probe is the only thing that ever gets a denial for
    // a read-only token -- every control here is disabled, so the page can
    // never provoke a 403 of its own to learn the permission's name.
    if (consoleState.missing_permission) state.missingPermission = consoleState.missing_permission;
    if (consoleState.session) state.activeId = consoleState.session;
  } catch (err) {
    setNotice("console", `console: ${errorText(err)}`);
  }
  await Promise.all([refreshSessions(), refreshApprovals()]);
  const target = state.activeId || (state.sessions.length ? state.sessions[0].execution_id : null);
  if (target) await openSession(target);
  scheduleRender();
}

boot();

export { handleEvent, render, state };
