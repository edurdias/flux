# Agent Harness Examples

Ready-to-use agent definitions and supporting code for the Flux agent harness.

## Prerequisites

A running Flux server and worker:

```bash
flux start server
flux start worker my-worker
```

## Examples

### Minimal assistant

A bare-bones agent — model + system prompt, no tools.

```bash
flux agent create assistant --file examples/agents/assistant.yaml
flux agent start assistant --mode terminal
```

### Full-featured coder

System tools, MCP integration, planning, and reasoning.

```bash
flux secrets set GITHUB_TOKEN "ghp_..."
flux agent create coder --file examples/agents/coder.yaml
flux agent start coder --mode terminal
```

### Local Ollama agent

Runs entirely offline using a local Ollama model. No API keys required.

```bash
ollama pull qwen3:8b
flux agent create local-agent --file examples/agents/ollama_local.yaml
flux agent start local-agent --mode terminal
```

### Research agent with memory and skills

Persistent long-term memory across sessions, skill-based workflows.

```bash
flux agent create researcher --file examples/agents/researcher.yaml
flux agent start researcher --mode terminal
```

### Agent delegation

A lead agent that delegates to specialist sub-agents.

```bash
flux agent create assistant --file examples/agents/assistant.yaml
flux agent create researcher --file examples/agents/researcher.yaml
flux agent create lead --file examples/agents/delegation.yaml
flux agent start lead --mode terminal
```

### Custom tools

Define `@task` functions in a Python file and reference them in the agent definition.

```bash
flux agent create my-agent \
  --model anthropic/claude-sonnet-4-20250514 \
  --system-prompt "You are a support agent. Use the available tools." \
  --tools-file examples/agents/custom_tools.py
flux agent start my-agent --mode terminal
```

See [`custom_tools.py`](custom_tools.py) for the tool definitions.

### Custom workflow

Override the built-in chat loop with a custom workflow that adds a welcome message and turn limit.

```bash
flux agent create my-agent \
  --model anthropic/claude-sonnet-4-20250514 \
  --system-prompt "You are a helpful assistant." \
  --workflow-file examples/agents/custom_workflow.py
flux agent start my-agent --mode terminal
```

See [`custom_workflow.py`](custom_workflow.py) for the workflow code.

### API mode client

Start an agent in headless API mode and interact programmatically.

```bash
flux agent start assistant --mode api --port 9100
python examples/agents/api_client.py
```

See [`api_client.py`](api_client.py) for the client code.

## Serving Modes

| Mode | Command | Use case |
|------|---------|----------|
| Terminal | `flux agent start <name> --mode terminal` | Interactive CLI chat |
| Web | `flux agent start <name> --mode web --port 9100` | Browser-based chat UI |
| API | `flux agent start <name> --mode api --port 9100` | Headless SSE integration |

## Session Management

```bash
flux agent session list                    # all sessions
flux agent session list coder              # sessions for a specific agent
flux agent session show <session-id>       # session details
flux agent session resume <session-id>     # resume in terminal mode
flux agent stop <session-id>               # cancel a session
```

## File Reference

| File | Description |
|------|-------------|
| `assistant.yaml` | Minimal agent definition |
| `coder.yaml` | Full-featured coding agent with tools and MCP |
| `researcher.yaml` | Research agent with long-term memory and skills |
| `ollama_local.yaml` | Offline agent using local Ollama model |
| `delegation.yaml` | Lead agent with sub-agent delegation |
| `custom_tools.py` | Example `@task` functions used as agent tools |
| `custom_workflow.py` | Custom agent workflow with welcome message and turn limit |
| `api_client.py` | Python client for API mode interaction |
