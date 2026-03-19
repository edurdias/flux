# Agent Skills

Agent skills are reusable instruction bundles that agents can discover and activate on demand. Skills follow the [Agent Skills](https://agentskills.io) open standard — skills authored for Flux work with Claude Code, Cursor, GitHub Copilot, Gemini CLI, and 30+ other tools.

## Basic Usage

```python
from flux import workflow, ExecutionContext
from flux.tasks.ai import agent, SkillCatalog

catalog = SkillCatalog.from_directory("./skills")

assistant = agent(
    "You are a helpful assistant.",
    model="ollama/llama3.2",
    tools=[search_web],
    skills=catalog,
)

@workflow
async def my_workflow(ctx: ExecutionContext):
    return await assistant("Research quantum computing")
```

The agent sees skill descriptions in its system prompt, calls `use_skill` to load full instructions when relevant, then follows them using the available tools.

## Defining Skills

### SKILL.md Files

A skill is a directory containing a `SKILL.md` file with YAML frontmatter and markdown instructions:

```
skills/
├── researcher/
│   └── SKILL.md
├── summarizer/
│   └── SKILL.md
└── code-reviewer/
    ├── SKILL.md
    ├── references/
    │   └── owasp-checklist.md
    └── scripts/
        └── lint.py
```

The `SKILL.md` format:

```yaml
---
name: researcher
description: Deep research on a topic using web sources. Use when the task requires gathering information from multiple sources and synthesizing findings.
allowed-tools: search_web read_url
metadata:
  author: acme-corp
  version: "1.0"
---

Research the given topic thoroughly.

1. Use search_web to find relevant sources on the topic
2. Use read_url to extract content from the most promising results
3. Cross-reference findings across multiple sources
4. Synthesize findings into a comprehensive summary with citations
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Skill identifier. Max 64 chars, lowercase letters, numbers, and hyphens. Must match the parent directory name. |
| `description` | Yes | What the skill does and when to use it. Max 1024 chars. The LLM uses this to decide which skill to activate. |
| `allowed-tools` | No | Space-delimited list of tool names the skill expects to use. Validated against the agent's tools at construction time. |
| `license` | No | License name or reference to a bundled license file. |
| `compatibility` | No | Environment requirements (max 500 chars). |
| `metadata` | No | Arbitrary key-value mapping for additional metadata (author, version, etc.). |

### Python-Defined Skills

Skills can be constructed directly in Python without a `SKILL.md` file:

```python
from flux.tasks.ai import Skill

security_reviewer = Skill(
    name="security-reviewer",
    description="Reviews code for security vulnerabilities including injection, "
    "XSS, and OWASP Top 10 issues.",
    instructions=(
        "You are a security expert. Review the provided code.\n\n"
        "Check for:\n"
        "1. SQL injection vulnerabilities\n"
        "2. Cross-site scripting (XSS)\n"
        "3. Authentication and authorization flaws\n"
        "4. Sensitive data exposure\n"
        "5. Input validation issues\n\n"
        "For each issue, provide severity, description, and a fix."
    ),
    allowed_tools=["run_linter"],
)
```

Python skills are useful when:
- You want IDE autocompletion and type checking
- Skill instructions are generated dynamically
- You don't need cross-tool portability

### Name Validation

Skill names follow the Agent Skills standard:

- Lowercase letters, numbers, and hyphens only
- Must not start or end with a hyphen
- Must not contain consecutive hyphens (`--`)
- Maximum 64 characters
- Must match the parent directory name (for `SKILL.md` files; warns if mismatched)

## SkillCatalog

The `SkillCatalog` discovers, indexes, and provides access to skills.

### From a Directory

Scans immediate subdirectories for `SKILL.md` files:

```python
from flux.tasks.ai import SkillCatalog

catalog = SkillCatalog.from_directory("./skills")
# Finds: skills/researcher/SKILL.md, skills/summarizer/SKILL.md, etc.
```

Invalid skills (missing required fields, malformed YAML) are logged as warnings and skipped.

### From a List

Construct directly from `Skill` objects:

```python
from flux.tasks.ai import Skill, SkillCatalog

catalog = SkillCatalog([
    Skill(name="alpha", description="...", instructions="..."),
    Skill(name="beta", description="...", instructions="..."),
])
```

### Mixed Catalogs

Directory discovery and explicit registration can be combined:

```python
catalog = SkillCatalog.from_directory("./skills")

catalog.register(Skill(
    name="custom-reviewer",
    description="Custom review logic.",
    instructions="Review using these criteria...",
))
```

### Lookup

```python
skill = catalog.get("researcher")                       # by name, raises SkillNotFoundError if missing
skills = catalog.find(["researcher", "code-reviewer"])   # multiple by name
all_skills = catalog.list()                              # all registered skills
```

Name uniqueness is enforced — registering a skill with a duplicate name raises `SkillCatalogError`.

## Agent Integration

### Passing Skills to an Agent

Pass a `SkillCatalog` to `agent()` via the `skills` parameter:

```python
assistant = agent(
    "You are a helpful assistant.",
    model="openai/gpt-4o",
    tools=[search_web, read_url, lint_code],
    skills=catalog,
)
```

When `skills` is provided, `agent()` does three things:

1. **Appends skill descriptions** to the system prompt (~20 tokens per skill)
2. **Creates a `use_skill` tool** that the LLM can call to load a skill's full instructions
3. **Validates `allowed_tools`** — warns if a skill declares tools not present in the agent's tool list

No changes are needed in the provider builders (Ollama, OpenAI, Anthropic) — they receive an augmented system prompt and one additional tool.

### How Skill Selection Works

The LLM selects skills through a tool-calling mechanism:

```
1. System prompt includes:
   "Available skills:
    - researcher: Deep research on a topic using web sources.
    - summarizer: Summarizes long content into concise bullet points."

2. User instruction: "Research quantum computing"

3. LLM decides "researcher" is relevant, calls:
   use_skill(name="researcher")

4. Tool returns the skill's full instructions:
   "Research the given topic thoroughly.
    1. Use search_web to find relevant sources..."

5. LLM follows the instructions using available tools:
   search_web(query="quantum computing advances 2026")

6. LLM returns the final response
```

### Multi-Skill Stacking

The LLM can activate multiple skills in a single run. Previously loaded instructions remain in the message history:

```python
# Agent has both "researcher" and "summarizer" skills
result = await assistant("Research quantum computing and summarize the findings")

# LLM flow:
# 1. Calls use_skill("researcher") → gets research instructions
# 2. Calls search_web(...) → gets results
# 3. Calls use_skill("summarizer") → gets summarization instructions
# 4. Returns summarized research findings
```

### Allowed Tools Validation

Skills can declare which tools they expect via `allowed-tools` (SKILL.md) or `allowed_tools` (Python). At agent construction time, Flux validates these against the agent's actual tools and warns about any missing ones:

```python
# Skill declares: allowed-tools: search_web read_url
# Agent has: tools=[search_web]
# → Warning: "Skill 'researcher' declares allowed_tool 'read_url' which is not in the agent's tools list."
```

This is a warning, not an error — the skill may still work without all declared tools. Tool names are matched against `func.__name__` since that is what the tool executor uses for dispatch.

## Event Tracking

Skill activation appears in the Flux event log as regular task events:

```
TASK_STARTED    assistant         {"instruction": "Research quantum computing"}
TASK_STARTED    use_skill         {}
TASK_COMPLETED  use_skill         "Research the given topic thoroughly..."
TASK_STARTED    search_web        {"query": "quantum computing"}
TASK_COMPLETED  search_web        "Results: ..."
TASK_COMPLETED  assistant         "Quantum computing is..."
```

The `use_skill` call is a regular `@task` — it gets full observability (events, OpenTelemetry spans, retry tracking) for free.

## Error Handling

### Skill Not Found

If the LLM calls `use_skill` with an unknown name, `SkillNotFoundError` is raised. This extends Flux's `ExecutionError`, so it integrates with the agent's retry and error handling:

```python
assistant = agent(
    "You are a helpful assistant.",
    model="ollama/llama3.2",
    skills=catalog,
).with_options(retry_max_attempts=3)

# If the LLM hallucinates a skill name, the task fails and retries
```

### Validation Errors

Construction-time errors raise `SkillValidationError` (extends `ValueError`):

```python
# Missing required fields
Skill(name="", description="test", instructions="test")
# → SkillValidationError: Skill name must not be empty.

# Invalid SKILL.md format
Skill.from_file("bad-skill/SKILL.md")
# → SkillValidationError: Skill file must contain YAML frontmatter delimited by '---'.
```

### Catalog Errors

Duplicate names raise `SkillCatalogError` (extends `ValueError`):

```python
catalog = SkillCatalog([skill_a, skill_a])
# → SkillCatalogError: Skill 'researcher' is already registered.
```

## Examples

### Multi-Skill Research Agent

```python
from flux import task, workflow, ExecutionContext
from flux.tasks.ai import agent, SkillCatalog

@task
async def search_web(query: str) -> str:
    """Search the web and return relevant results."""
    ...

catalog = SkillCatalog.from_directory("./skills")

assistant = agent(
    "You are a research assistant.",
    model="ollama/llama3.2",
    tools=[search_web],
    skills=catalog,
).with_options(retry_max_attempts=3, timeout=120)

@workflow
async def research(ctx: ExecutionContext):
    return await assistant(f"Research: {ctx.input['topic']}")
```

### Code Review Agent with Python Skills

```python
from flux import task, workflow, ExecutionContext
from flux.tasks.ai import Skill, SkillCatalog, agent

security = Skill(
    name="security-reviewer",
    description="Reviews code for security vulnerabilities.",
    instructions="Check for SQL injection, XSS, auth flaws...",
)

performance = Skill(
    name="performance-reviewer",
    description="Reviews code for performance issues.",
    instructions="Check for O(n^2), memory leaks, N+1 queries...",
)

@task
async def run_linter(code: str) -> str:
    """Run static analysis on the provided code."""
    ...

reviewer = agent(
    "You are a code review assistant.",
    model="openai/gpt-4o",
    tools=[run_linter],
    skills=SkillCatalog([security, performance]),
).with_options(retry_max_attempts=3, timeout=120)

@workflow
async def review(ctx: ExecutionContext):
    return await reviewer(f"Review this code:\n{ctx.input['code']}")
```

### Skills with MCP Tools

Skills compose naturally with MCP-discovered tools:

```python
from flux.tasks.ai import agent, SkillCatalog
from flux.tasks.mcp import mcp

catalog = SkillCatalog.from_directory("./skills")

async with mcp("http://localhost:8080/mcp", name="server") as client:
    tools = await client.discover()

    assistant = agent(
        "You are a workflow assistant.",
        model="ollama/llama3.2",
        tools=list(tools),
        skills=catalog,
    )

    result = await assistant("List available workflows and summarize them")
```

## `Skill` Reference

```python
class Skill:
    def __init__(
        self,
        name: str,                              # required, validated
        description: str,                       # required
        instructions: str,                      # required
        allowed_tools: list[str] | None = None, # optional, defaults to []
        metadata: dict[str, str] | None = None, # optional, defaults to {}
    ): ...

    @classmethod
    def from_file(cls, path: str) -> Skill: ...
```

## `SkillCatalog` Reference

```python
class SkillCatalog:
    def __init__(self, skills: list[Skill] | None = None): ...

    @classmethod
    def from_directory(cls, path: str) -> SkillCatalog: ...

    def register(self, skill: Skill) -> None: ...
    def get(self, name: str) -> Skill: ...
    def find(self, names: list[str]) -> list[Skill]: ...
    def list(self) -> list[Skill]: ...
```
