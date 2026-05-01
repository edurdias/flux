# Agent Harness

The agent harness turns LLM agents into first-class Flux resources. An agent is a YAML-declarable entity stored in the database, serves as a chat interface over the terminal, a web UI, or a headless HTTP/SSE API, and reuses Flux's existing workflow engine, RBAC, streaming, and pause/resume mechanics under the hood.

If you are familiar with the Python-level `agent()` task (see [AI Agents](ai-agents.md)), the harness is the layer above it: it packages an agent definition, a chat loop, session management, and a user-facing process into something an operator can create and run without writing code.

## Overview

A Flux agent is a row in the `agents` table plus a process (`flux agent start`) that drives a chat session against the server. The chat loop itself runs as a normal Flux workflow (`agents/agent_chat`) on a worker. The agent process only handles the UX.

Capabilities:

- **Declarative agents** — define an agent in YAML, store it in Flux, reuse it anywhere.
- **Three serving modes** — `terminal` for interactive CLI use, `web` for a local single-user chat page, `api` for headless integration.
- **Tool integration** — bundled system tools (shell, files, search, directory), individual groups, cherry-picked names, or custom Python `@task` tools from a file.
- **MCP integration** — connect to any MCP server; URL-based elicitation (auth) is handled across all three serving modes.
- **Elicitation** — MCP `elicitation/create` requests pause the workflow; the process shows the URL and resumes on completion.
- **Working memory** — turn-by-turn conversation memory, always on.
- **Planning** — optional structured planning with approval; see [Agent Plans](agent-plans.md).
- **Delegation** — agents can call other agents as sub-agents; see [Sub-agents](sub-agents.md).
- **Skills** — reusable prompt capabilities loaded from a directory; see [Agent Skills](agent-skills.md).
- **Long-term memory** — optional provider-backed memory across sessions; see [AI Memory](ai-memory.md).
- **Sessions are executions** — a session ID is a Flux execution ID; any authorized user can resume any session.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│                  flux agent start                   │
│                                                     │
│  ┌─────────┐   ┌─────────┐   ┌─────────────────┐    │
│  │Terminal │   │ Web UI  │   │   API (SSE)     │    │
│  │         │   │ (chat   │   │                 │    │
│  │         │   │  page)  │   │  POST /chat     │    │
│  └────┬────┘   └────┬────┘   └────────┬────────┘    │
│       │             │                  │            │
│       └─────────────┼──────────────────┘            │
│                     │                               │
│             ┌───────▼────────┐                      │
│             │ Agent Process  │                      │
│             │                │                      │
│             │ • AgentSession │                      │
│             │ • Event parser │                      │
│             │ • Elicitation  │                      │
│             └───────┬────────┘                      │
│                     │                               │
└─────────────────────┼───────────────────────────────┘
                      │  Flux HTTP API
                      │  run / resume / SSE stream
                      │
             ┌────────▼────────┐
             │   Flux Server   │
             │                 │
             │ • Auth / RBAC   │
             │ • Agent defs    │
             │ • Config store  │
             │ • Executions    │
             └────────┬────────┘
                      │ dispatch
                      │
             ┌────────▼────────┐
             │     Worker      │
             │                 │
             │ • agent_chat    │
             │   workflow      │
             │ • agent() loop  │
             │ • Tools / MCP   │
             │ • Memory        │
             └─────────────────┘
```

The agent process never runs the model itself. It is a stateless client: it starts or resumes an execution of the `agents/agent_chat` workflow on the server, streams the resulting SSE events, renders them for the user, prompts for the next message, and resumes. The worker does the model calls, tool execution, and memory work.

## Concepts

### Agent definition

A row in the `agents` table, created via `flux agent create`. Holds everything needed to instantiate the chat loop: model, system prompt, tools, MCP servers, memory, planning flags, and so on. See the [YAML specification](#agent-yaml-specification) for the full field list.

Agents are global (not namespaced). The name is the primary key. Permissions use the format `agent:<name>:<action>`.

### Configuration (`configs` table)

Plaintext key-value entries stored in the database. Analogous to secrets but without encryption — suitable for non-sensitive runtime values that change independently of code.

```bash
flux config set logging.level info
flux config get logging.level
flux config list
flux config remove logging.level
```

Agent definitions are themselves stored as configs under the key `agent:<name>`. The built-in `agent_chat` workflow reads its definition from this key at runtime via the generic `get_config` task.

### Session

A session is a running execution of the `agent_chat` workflow. The session ID and the Flux execution ID are the same value. There is no separate session store.

- Start a new session: `flux agent start <name>`
- Resume a session: `flux agent start <name> --session <id>` or `flux agent session resume <id>`
- Sessions are not owned by a user; any principal with `agent:<name>:start` and `workflow:agents:agent_chat:run` can resume.

### Event stream

Sessions stream SSE events. The agent process parses raw Flux SSE frames into `AgentEvent` kinds consumed by any UI:

| Kind | Emitted when | Payload |
|------|--------------|---------|
| `session_id` | Workflow starts | `{"id": "<exec-id>"}` |
| `token` | LLM streams tokens (stream mode only, outside tool loop in v1) | `{"text": "..."}` |
| `tool_start` | Before a tool call | `{"name": "...", "args": {...}}` |
| `tool_done` | After a tool call | `{"name": "...", "status": "success" \| "error"}` |
| `chat_response` | Workflow pauses waiting for user input | `{"content": "...", "turn": N}` |
| `elicitation` | MCP server requests out-of-band authorization | `{"elicitation_id", "url", "message", "server_name", "mode"}` |
| `session_end` | Workflow pauses with final session output | `{"reason": "max_turns"\|"user_exit"\|"error", "turns": N}` |

Unknown raw frames are silently dropped — the parser is forward-compatible.

## Quick Start

Prereq: a running Flux server and worker. If auth is enabled, sign in first:

```bash
flux auth login
```

Define a minimal agent in YAML:

```yaml
# assistant.yaml
name: assistant
model: anthropic/claude-sonnet-4-20250514
system_prompt: |
  You are a helpful coding assistant. Keep answers concise.
description: General-purpose coding helper
```

Create it:

```bash
flux agent create assistant --file assistant.yaml
```

Start a terminal session:

```bash
flux agent start assistant --mode terminal
```

You see a prompt. Type a message, press Enter. When you are done, press `Ctrl+D` or type `/quit`. The session ID is printed on exit so you can resume later:

```bash
flux agent session resume <session-id>
```

## CLI Reference

### `flux agent create`

Create a new agent definition. Options can come from flags, a YAML `--file`, or a combination (flags win).

```bash
flux agent create <name> \
  [--file agent.yaml] \
  [--model provider/name] \
  [--system-prompt TEXT | --system-prompt-file PATH] \
  [--description TEXT] \
  [--tools NAME]... \
  [--tools-file PATH] \
  [--workflow-file PATH] \
  [--mcp-server URL]... \
  [--skills-dir PATH] \
  [--planning | --no-planning] \
  [--max-tool-calls N] \
  [--max-tokens N] \
  [--reasoning-effort low|medium|high] \
  [--format simple|json]
```

`--tools` is a repeatable flag that takes a built-in tool group name (`system_tools`, `shell`, `files`, `search`, `directory`). Individual tool names from the resolver are also accepted. `--mcp-server` is repeatable and records only the URL; richer MCP configuration (auth, secret reference, name) must go through `--file`.

### `flux agent list`

```bash
flux agent list [--format simple|json]
```

### `flux agent show`

```bash
flux agent show <name> [--format simple|json|yaml]
```

Default format is `yaml`.

### `flux agent update`

```bash
flux agent update <name> \
  [--file agent.yaml] \
  [--model ...] \
  [--system-prompt ... | --system-prompt-file ...] \
  [--description ...] \
  [--planning | --no-planning] \
  [--max-tool-calls N] \
  [--reasoning-effort low|medium|high] \
  [--format simple|json]
```

Fields not supplied are preserved. When both `--file` and flags are given, flags override.

### `flux agent delete`

```bash
flux agent delete <name> [--format simple|json]
```

### `flux agent start`

```bash
flux agent start <name> \
  [--mode terminal|web|api] \
  [--session SESSION_ID] \
  [--port PORT] \
  [--server URL]
```

- `--mode` defaults to `terminal`.
- `--session` resumes an existing session instead of starting a new one.
- `--port` applies only to `web` and `api`; defaults to `8080`.
- `--server` overrides the Flux server URL; defaults to `http://<server_host>:<server_port>` from config.
- The auth token is resolved from `$FLUX_AUTH_TOKEN` first, then from the OIDC credentials stored by `flux auth login`.

### `flux agent stop`

```bash
flux agent stop <session-id>
```

Cancels the underlying Flux execution via `POST /executions/<id>/cancel`.

### `flux agent session`

```bash
flux agent session list [<agent-name>] [--format simple|json]
flux agent session show <session-id> [--format simple|json]
flux agent session resume <session-id>
```

`resume` is a shortcut for `flux agent start <name> --mode terminal --session <id>` that does not require the agent name — it attaches to the execution directly.

!!! note
    `session list` and `session show` currently print a placeholder message pending full server-side integration. Use `flux execution list` or the `/executions` API to inspect sessions by their execution ID in the meantime.

### `flux config`

Key-value config used by agents and any task that declares `config_requests`.

```bash
flux config set <name> <value> [--format simple|json]
flux config get <name>          [--format simple|json]
flux config list                [--format simple|json]
flux config remove <name>       [--format simple|json]
```

Values are stored in plaintext. Do not put secrets here — use `flux secrets` for that.

## Agent YAML Specification

Every field maps 1:1 to a column in the `agents` table and to the `AgentDefinition` Pydantic model in `flux.agents.types`.

```yaml
name: coder                                         # required, primary key
model: anthropic/claude-sonnet-4-20250514           # required, provider/model
system_prompt: |                                    # required
  You are a coding assistant. Be concise.
description: A coding assistant with full system access

# Tools — see Tool Configuration below for details.
tools:
  - system_tools:
      workspace: /home/user/project
      timeout: 60
      max_output_chars: 200000
  - shell:
      workspace: /home/user/project
  - file: ./custom_tools.py      # Python file with @task functions
  - read_file                    # cherry-pick by name

tools_file: ./custom_tools.py    # alternative to the inline file: entry
workflow_file: ./custom_chat.py  # escape hatch: custom workflow, see below

# MCP servers. Only `url` is required; the rest is optional and
# depends on the MCP client's auth handling.
mcp_servers:
  - url: http://localhost:8080/mcp
    name: github
    auth: bearer
    secret: GITHUB_TOKEN         # resolved from Flux secrets at runtime

# Skills directory. Resolved at creation time; contents are stored inline.
skills_dir: ./skills

# Sub-agents this agent can delegate to. Must exist in the agents table.
agents:
  - researcher
  - reviewer

# Planning (structured multi-step)
planning: true
max_plan_steps: 20
approve_plan: false

# Tool loop limits
max_tool_calls: 20
max_concurrent_tools: 4

# LLM limits
max_tokens: 4096
stream: true

# Approval mode for tool execution (default | always | never — see Human-in-the-loop)
approval_mode: default

# Reasoning depth for models that support it
reasoning_effort: high

# Optional long-term memory
long_term_memory:
  provider: sqlite
  connection: "memory.db"
  scope: "user:default"
```

Working memory is always on; there is no flag to disable it.

### Field validation

- `model` must be in `provider/model_name` format (the `/` is required).
- `reasoning_effort` must be `low`, `medium`, `high`, or omitted.
- `tools` is a free-form list of strings, dicts, or `file:` references; validation happens in the tools resolver at runtime.

### File resolution at creation time

`skills_dir`, `tools_file`, and `workflow_file` are paths on the machine running `flux agent create`. Because the workflow runs on a worker — potentially a different machine — these files are read and stored inline in the database at creation (and again on `update` if paths are supplied). The worker never reads them from the local filesystem.

**Permission requirement.** When auth is enabled, shipping any of these inline payloads requires the `workflow:*:*:register` permission, not just `agent:*:create`. The contents are materialized on every worker that runs the agent, so the gate matches the one for registering workflow source code. Plain path-string `skills_dir` (referencing an existing directory on the worker host, not an inline bundle) does not trigger the elevated gate.

### Custom workflows

The built-in template is `agents/agent_chat`. Most agents do not need anything else. For advanced flows, supply `workflow_file` with a custom workflow that follows the same contract:

- Input on first run: `{"agent": "<name>"}`.
- Pause with `ChatResponseOutput`, `SessionEndOutput`, or an elicitation payload.
- Resume with `{"message": "..."}` for user turns or `{"elicitation_response": {...}}` for elicitation.

## Serving Modes

### Terminal

```bash
flux agent start coder --mode terminal
```

- Direct readline interaction, no HTTP server.
- Tokens stream inline; tool calls print a `Calling name(args)... Done.` line.
- In-chat commands:
  - `/help` — list commands.
  - `/session` — print the current session ID.
  - `/quit` — exit cleanly.
- `Ctrl+D` (EOF) exits. `Ctrl+C` cancels the current turn.
- On exit, the session ID is printed so you can resume later:

  ```
  Session: 7f3c2d1a-...-...
  ```

- Elicitation is handled inline: the URL is printed, and you are prompted `Open browser to authorize? [Y/n]`. Answering yes calls `webbrowser.open(url)`.

### Web

```bash
flux agent start coder --mode web --port 8080
```

- Binds to `0.0.0.0:<port>` and serves a single-page chat UI at `GET /`.
- The SSE `/chat` endpoint uses the operator's Flux token, set at process start time (either `$FLUX_AUTH_TOKEN` or refreshed via `flux auth login`). No per-request Bearer is required.
- Intended for a single operator. If you expose web mode publicly, put it behind a reverse proxy that enforces your own access control — Flux does not authenticate the browser side of web mode.
- Elicitation renders as a banner message with a link that opens the authorization URL in a new tab; the UI resumes automatically when the user responds.
- The UI follows the system light/dark preference via CSS `prefers-color-scheme`.

### API

```bash
flux agent start coder --mode api --port 8080
```

Headless SSE service. Every non-health endpoint requires a Bearer token that is passed through to the Flux server on a per-request basis — the operator token supplied at process start is not used.

| Method | Path | Body | Notes |
|--------|------|------|-------|
| `GET` | `/health` | — | Public. Returns `{"status": "ok"}`. |
| `POST` | `/chat` | `{"message": "..."}` | Starts a new session. SSE response. First frames include a `session_id` event. |
| `POST` | `/chat?session=<id>` | `{"message": "..."}` | Resumes an existing session. SSE response. |
| `POST` | `/elicitation/{elicitation_id}?session=<id>` | `{"elicitation_id": "...", "action": "accept"\|"decline"\|"cancel"}` | Resume the session in response to an elicitation. SSE response. |
| `GET` | `/session/{id}` | — | Proxy to `GET /executions/{id}` on the Flux server. |

SSE frames are JSON objects with a `type` field matching the event kinds above (except `chat_response` is serialized as `type: response` on the wire, for backward compatibility with the bundled web UI):

```json
{"type": "session_id", "id": "7f3c2d1a-..."}
{"type": "token", "text": "Hello"}
{"type": "tool_start", "name": "shell", "args": {"cmd": "ls"}}
{"type": "tool_done", "name": "shell", "status": "success"}
{"type": "response", "content": "Hi! What can I help with?", "turn": 1}
{"type": "elicitation", "elicitation_id": "el-1", "url": "https://auth...", "message": "Authorize", "server_name": "github", "mode": "url"}
{"type": "session_end", "reason": "user_exit", "turns": 5}
{"type": "error", "message": "..."}
```

Example curl (note `-N` to disable buffering):

```bash
curl -N -X POST http://localhost:8080/chat \
  -H "Authorization: Bearer $FLUX_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "list the files in /tmp"}'
```

Resume:

```bash
curl -N -X POST "http://localhost:8080/chat?session=$SESSION_ID" \
  -H "Authorization: Bearer $FLUX_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "now summarize what you found"}'
```

Respond to an elicitation:

```bash
curl -N -X POST "http://localhost:8080/elicitation/el-1?session=$SESSION_ID" \
  -H "Authorization: Bearer $FLUX_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"elicitation_id": "el-1", "action": "accept"}'
```

## Tool Configuration

Tool entries are resolved by `flux.agents.tools_resolver`. Four forms are supported.

### Bundled tool set

`system_tools` includes shell, files, search, and directory tools against a single workspace:

```yaml
tools:
  - system_tools:
      workspace: /home/user/project
      timeout: 60
      max_output_chars: 200000
      blocklist:
        - rm -rf
        - sudo
```

Omitted fields use the defaults: `workspace="."`, `timeout=30`, `max_output_chars=100000`, and the built-in `DEFAULT_BLOCKLIST` from `flux.tasks.ai.tools.system_tools`. See [System Tools](system-tools.md) for the full behavior.

### Individual groups

Select only the groups you need. Each takes the same config as `system_tools`:

```yaml
tools:
  - shell:
      workspace: /home/user/project
  - files:
      workspace: /home/user/project
  - search:
      workspace: /home/user/project
  - directory:
      workspace: /home/user/project
```

### Custom tools from a Python file

Point at a Python file whose top-level `@task` functions should be exposed as tools:

```yaml
tools:
  - file: ./custom_tools.py
```

Or, equivalently, the top-level `tools_file` field:

```yaml
tools_file: ./custom_tools.py
```

The file is read at `flux agent create` time and stored in the agent definition; the worker uses the stored copy.

### Cherry-pick by name

A plain string entry resolves via the tools resolver. In the current implementation the resolver accepts the bundled group names (`system_tools`, `shell`, `files`, `search`, `directory`); use `file:` for everything else.

```yaml
tools:
  - shell
  - files
```

## MCP Integration

Add one or more MCP servers in the agent YAML:

```yaml
mcp_servers:
  - url: http://localhost:8080/mcp
    name: flux
  - url: https://mcp.github.example.com
    name: github
    auth: bearer
    secret: GITHUB_TOKEN
```

At runtime the worker connects, discovers tools, and makes them available to the agent loop alongside local tools. See [MCP Client](mcp-client.md) for details on the underlying `mcp()` task, authentication modes, and tool discovery.

### Elicitation (URL mode)

Some MCP servers require user authorization before exposing a tool. The MCP client advertises URL-mode elicitation during the handshake. When a tool call triggers an `elicitation/create` response (or a `-32042` `URLElicitationRequiredError`), the worker pauses the workflow with a payload of:

```json
{
  "type": "elicitation",
  "mode": "url",
  "elicitation_id": "el-1",
  "url": "https://auth.example.com/...",
  "message": "Authorize Flux to access GitHub",
  "server_name": "github"
}
```

The agent process observes the pause, handles the UX for the current mode, and resumes with an `ElicitationResponse`:

- **Terminal**: prints `[github] Authorize Flux ...`, prompts `Open browser to authorize? [Y/n]`, and on yes calls `webbrowser.open(url)`. Resumes with `action: accept` or `decline`.
- **Web**: renders a clickable link (`Click to authorize`) that opens in a new tab. The operator completes the flow on the provider's site; the page stays open and the agent process resumes when the user submits.
- **API**: emits a `{"type": "elicitation", ...}` SSE event. The client is responsible for directing the end-user to the URL and then calling `POST /elicitation/{id}?session=<id>` with the chosen action.

Elicitation is handled in a generic way in the MCP client — any workflow that uses MCP tools can pause with this payload, not just agents.

## Authentication and Authorization

The agent harness reuses Flux's existing RBAC. No separate auth system.

### Token resolution

`flux agent start` resolves the operator token in this order:

1. `FLUX_AUTH_TOKEN` environment variable.
2. Bearer token derived from OIDC credentials saved by `flux auth login` (refreshed on each invocation).

If no token can be resolved, the process runs unauthenticated; this only works against servers with auth disabled.

### Permissions

The harness adds two new namespaces on top of the workflow/execution namespaces:

```
agent:*:create
agent:*:read
agent:<name>:read
agent:<name>:update
agent:<name>:delete
agent:<name>:start
agent:<name>:session:read

config:*:read
config:*:manage
```

Because `agent_chat` is a real workflow, the workflow namespace still applies. Most agent actions require **both** an `agent:*` permission and the matching `workflow:agents:agent_chat:*` permission.

| Action | Required permissions |
|--------|----------------------|
| Create an agent definition | `agent:*:create` |
| List / show agents | `agent:*:read` or `agent:<name>:read` |
| Update / delete an agent | `agent:<name>:update` / `agent:<name>:delete` |
| Start or resume a session | `agent:<name>:start` + `workflow:agents:agent_chat:run` |
| Execute tasks inside the workflow | `workflow:agents:agent_chat:task:<task>:execute` |
| List sessions for an agent | `agent:<name>:session:read` + `execution:*:read` |
| Read config | `config:*:read` |
| Set / remove config | `config:*:manage` |

### Built-in roles

| Role | Agent / config permissions |
|------|----------------------------|
| `admin` | All (inherited via `*`). |
| `operator` | `agent:*:*`, `config:*:read`, `config:*:manage` (plus the standard workflow/execution/schedule perms). |
| `viewer` | `agent:*:read`, `config:*:read`. |

See [Authentication & Authorization](authentication.md) for the general RBAC model, role-management CLI, and permission wildcard rules.

## Deployment

The agent process is a thin client of the Flux server. It talks over HTTPS and SSE, so it can run anywhere the Flux API is reachable.

- **Terminal**: runs on the operator's machine. No network surface beyond its outbound HTTP calls.
- **Web**: binds to `0.0.0.0` by default but is designed as a single-operator chat UI. Put a reverse proxy (nginx, Caddy) in front if you need to expose it beyond localhost, and enforce your own authentication there. Web mode does **not** check a per-request Bearer token.
- **API**: multi-client by design. Each request must carry a Bearer token; tokens are passed through to the Flux server untrusted on the agent process side.

### Pinning agents to specific workers

The `agent_chat` workflow dispatches like any other Flux workflow. If your agent needs a specific environment (browser automation, a desktop sandbox, a GPU), combine the harness with [Worker Affinity](worker-affinity.md):

```bash
flux start worker --label role=harness --label env=sandbox --label browser=true
```

```python
# Custom workflow file referenced by workflow_file in the agent YAML
from flux import workflow, ExecutionContext

@workflow.with_options(namespace="agents", affinity={"role": "harness"})
async def agent_chat(ctx: ExecutionContext):
    ...  # same contract as the built-in template
```

Pair that with `workflow_file: ./custom_chat.py` in the agent definition.

### Scaling

Agent processes are stateless and can be horizontally scaled behind a load balancer when running in `api` mode. Sessions are addressed by execution ID, which is globally unique, so two processes can serve different sessions of the same agent concurrently without any shared state.

## Examples

Ready-to-use agent definitions and supporting code are in `examples/agents/`. Each YAML file is a complete agent definition you can create and run immediately.

| Example | What it shows |
|---------|---------------|
| `assistant.yaml` | Minimal agent — model + system prompt, no tools |
| `coder.yaml` | System tools, MCP integration, planning, reasoning |
| `ollama_local.yaml` | Fully offline agent using a local Ollama model |
| `researcher.yaml` | Long-term memory, skills, plan approval |
| `delegation.yaml` | Lead agent delegating to specialist sub-agents |
| `custom_tools.py` | `@task` functions used as agent tools via `--tools-file` |
| `custom_workflow.py` | Custom chat loop with welcome message and turn limit |
| `api_client.py` | Python client for headless API mode interaction |

### Custom tools

Define `@task` functions in a Python file and reference them at creation time:

```bash
flux agent create support-bot \
  --model anthropic/claude-sonnet-4-20250514 \
  --system-prompt "You are a support agent." \
  --tools-file examples/agents/custom_tools.py
```

Each `@task` function becomes a callable tool. The function name is the tool name and the docstring is the tool description sent to the LLM. At creation time, the file is read and stored inline in the agent definition — workers load it from the database, not the filesystem.

### Custom workflows

Override the built-in `agent_chat` template with a workflow file:

```bash
flux agent create my-agent \
  --model anthropic/claude-sonnet-4-20250514 \
  --system-prompt "You are a helpful assistant." \
  --workflow-file examples/agents/custom_workflow.py
```

Custom workflows must follow the [agent workflow contract](#agent-workflow-contract): accept `{"agent": "<name>"}` as input, pause with typed output, accept `{"message": "..."}` on resume.

### API mode integration

Start an agent in headless API mode and interact from any HTTP client:

```bash
flux agent start assistant --mode api --port 9100
```

Endpoints:

```
POST /chat                  Start a new session (SSE response)
POST /chat?session=<id>     Resume a session (SSE response)
POST /elicitation/<id>      Respond to an MCP elicitation
GET  /health                Health check
```

Every request except `/health` requires `Authorization: Bearer <token>`. See `examples/agents/api_client.py` for a Python client example.

## Example: A Coding Assistant

This walks through a realistic end-to-end flow: define an agent, start a session, have a conversation, resume it later.

### 1. Define the agent

```yaml
# coder.yaml
name: coder
model: anthropic/claude-sonnet-4-20250514
description: Coding assistant with workspace access and GitHub MCP.
system_prompt: |
  You are a senior engineer helping the user write and review code.
  Use the available tools to read, search, and edit files in the project.
  Always run the test suite after making changes. Be concise.

tools:
  - system_tools:
      workspace: /home/user/project
      timeout: 60
      max_output_chars: 200000

mcp_servers:
  - url: https://mcp.github.example.com
    name: github
    auth: bearer
    secret: GITHUB_TOKEN

planning: true
max_plan_steps: 15
max_tool_calls: 30
reasoning_effort: medium
```

### 2. Create it

```bash
flux secrets set GITHUB_TOKEN "ghp_..."
flux agent create coder --file coder.yaml
flux agent show coder
```

### 3. Start a terminal session

```bash
flux agent start coder --mode terminal
```

```
Flux Agent — coder
Session: 9e1b2f0c-4a2a-4e3d-b0f1-6a8a7a4c1b20
Type /help for commands, Ctrl+D to exit.

> find all TODO comments in the project and summarize them
Calling shell(cmd="rg -n TODO .")... Done.

Found 3 TODO comments:
1. src/parser.py:82 — "refactor this when we drop Py3.9"
...

> /quit

Session: 9e1b2f0c-4a2a-4e3d-b0f1-6a8a7a4c1b20
```

### 4. Resume later

```bash
flux agent session resume 9e1b2f0c-4a2a-4e3d-b0f1-6a8a7a4c1b20
```

```
> pick up where you left off and open a PR for the first TODO
...
```

### 5. Stop a runaway session

If something goes wrong mid-turn:

```bash
flux agent stop 9e1b2f0c-4a2a-4e3d-b0f1-6a8a7a4c1b20
```

This calls the Flux execution cancel endpoint. The next resume will not reattach — the session is terminated.

## Troubleshooting

**`Error starting agent: ... 401`** — the process could not authenticate to the Flux server. Run `flux auth login`, or set `FLUX_AUTH_TOKEN` to a valid Bearer token. Confirm with `flux auth status`.

**`Missing or invalid Authorization header`** — you hit the API mode `/chat` endpoint without a Bearer token. API mode does **not** fall back to the operator token; every request must carry its own `Authorization: Bearer ...` header.

**`Agent process started without a token`** — web mode was started without a way to resolve the operator token. Run `flux auth login` first, or launch with `FLUX_AUTH_TOKEN` set in the environment.

**MCP server prompts for authorization repeatedly** — elicitation state is not cached across sessions in v1. If you decline or cancel, the next tool call on that server will pause again. Accept once and keep the session alive for the remainder of the work.

**Session won't resume** — the underlying execution may have ended or errored. Inspect it directly:

```bash
curl -H "Authorization: Bearer $FLUX_AUTH_TOKEN" \
  http://localhost:8000/executions/<session-id>
```

If it is `COMPLETED`, `CANCELLED`, or `FAILED`, start a new session instead.

**Tools are never called** — check `approval_mode`: `always` requires a human to approve every tool call, and `never` disables tools entirely. Also confirm the caller has `workflow:agents:agent_chat:task:<task_name>:execute` for the tools the agent uses — missing task permissions silently skip or reject the call depending on server configuration.

**`Unknown tool group`** — the tools resolver only recognizes `system_tools`, `shell`, `files`, `search`, and `directory` out of the box. For anything else, expose it as a `@task` in a Python file and reference it via `file:` or `tools_file:`.

**Event stream ends with no `chat_response`** — the workflow paused on something the parser does not recognize and the loop exits. Check the execution's task events via `GET /executions/<id>` on the server. Unknown pause outputs are ignored on the parser side to stay forward-compatible; future Flux versions may add new pause types.
