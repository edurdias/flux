# System Tools

System tools give AI agents the ability to execute shell commands and interact with the file
system. They are provided as a standalone module — import and pass them via `tools=`.

## Quick Start

```python
from flux import workflow, ExecutionContext
from flux.tasks.ai import agent, system_tools

@workflow
async def autonomous_agent(ctx: ExecutionContext):
    tools = system_tools(workspace="/path/to/project")

    assistant = await agent(
        "You are an autonomous coding assistant. Use your tools to explore the codebase, "
        "make changes, and run tests.",
        model="anthropic/claude-sonnet-4-20250514",
        tools=tools,
    )

    return await assistant("Refactor the auth module to use async/await")
```

## Configuration

```python
tools = system_tools(
    workspace="/path/to/project",   # Required. Root for file tools, cwd for shell.
    timeout=30,                     # Shell timeout in seconds (default: 30).
    blocklist=None,                 # Shell blocklist patterns (None = defaults).
    max_output_chars=100_000,       # Truncate responses to LLM (default: 100K).
)
```

### Parameters

- **workspace** (required): Absolute path used as the root directory. File tools are sandboxed
  to this directory. Shell commands use it as their working directory.
- **timeout**: Applied to the shell tool via `@task.with_options(timeout=...)`. Default: 30s.
- **blocklist**: List of regex patterns. Shell commands matching any pattern are rejected.
  Pass `None` for sensible defaults, or `[]` to disable.
- **max_output_chars**: Maximum characters in tool responses sent to the LLM. Full output is
  preserved in Flux task events. Default: 100,000.

## Tools

### Shell

| Tool | Description |
|------|-------------|
| `shell` | Execute a shell command in the workspace directory |

The shell tool runs commands with `cwd=workspace`. It captures stdout, stderr, and exit code.
Non-zero exit codes are **not** errors — the tool returns `status: "ok"` so the agent can
interpret the failure.

Pass `stream=True` to emit stdout chunks as `progress()` events for real-time output.

### File Operations

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents (full or line range) |
| `write_file` | Create or overwrite a file |
| `edit_file` | Search-and-replace edit |
| `file_info` | File metadata (size, modified, permissions) |

All file tools are sandboxed to the workspace directory. Paths are relative to workspace;
any attempt to escape (e.g., `../`) returns an error.

### Search

| Tool | Description |
|------|-------------|
| `find_files` | Find files by glob pattern |
| `grep` | Search file contents by regex |

### Directory

| Tool | Description |
|------|-------------|
| `list_directory` | List directory contents with metadata |
| `directory_tree` | Recursive tree view |

## Security Model

**File tools** are sandboxed to the workspace directory. Paths are resolved and checked —
symlink escapes, `../` traversals, and absolute paths outside workspace are all rejected.

**Shell** is not path-sandboxed. It can access the full system, which is necessary for
package installs, system commands, and external services. A regex blocklist prevents
obviously destructive commands. Override with `blocklist=[]` for full trust.

For stronger isolation, run your Flux workflow inside a Docker container.

## Composing With Other Tools

System tools are a plain `list[task]` — combine them with your own tools:

```python
@task
async def query_database(sql: str) -> str:
    """Run a SQL query against the database."""
    # ...

tools = system_tools(workspace="/path/to/project")
assistant = await agent(
    "You are an assistant with access to the codebase and a database.",
    model="openai/gpt-4o",
    tools=tools + [query_database],
)
```

To select specific tools:

```python
tools = system_tools(workspace="/path/to/project")
shell_only = [t for t in tools if t.func.__name__ == "shell"]
file_tools = [t for t in tools if t.func.__name__ in ("read_file", "write_file", "edit_file")]
```
