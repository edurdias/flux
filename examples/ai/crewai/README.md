# CrewAI + Flux Examples

These examples demonstrate Flux orchestrating CrewAI workloads. CrewAI's role-based multi-agent teams handle LLM collaboration patterns while Flux provides production infrastructure: durability, retries, scheduling, and worker distribution.

## Why Flux + CrewAI?

CrewAI makes it easy to define role-based agent teams that collaborate on tasks. Flux adds what CrewAI lacks for production use:

- **Durability** — workflow state is persisted; crashes don't lose progress
- **Retries** — automatic retry with configurable backoff when LLM calls fail
- **Scheduling** — run crews on a cron schedule without extra infrastructure
- **Worker Distribution** — distribute heavy LLM workloads across machines
- **Secrets Management** — built-in AES-256 encrypted secrets
- **Observability** — full execution tracing via workflow events

## Examples

| Example | CrewAI + Flux | Pure Flux Equivalent | Pattern |
|---------|---------------|----------------------|---------|
| Multi-Agent Code Review | [`multi_agent_code_review.py`](multi_agent_code_review.py) | [`../multi_agent_code_review_ollama.py`](../multi_agent_code_review_ollama.py) | CrewAI Crew (sequential) vs Flux Graph |
| Blog Post Writer | [`blog_post_writer.py`](blog_post_writer.py) | *(no equivalent)* | CrewAI sequential pipeline: Researcher → Writer → Editor |

## Setup

### 1. Install Ollama

```bash
# Install from https://ollama.ai, then:
ollama pull llama3
ollama pull llama3.2
ollama serve
```

### 2. Install Dependencies

```bash
pip install crewai litellm
```

> **Note:** `litellm` is required for CrewAI's Ollama integration. CrewAI uses LiteLLM under the hood to connect to local LLM providers.

### 3. Run Examples

```bash
# Multi-Agent Code Review
python examples/ai/crewai/multi_agent_code_review.py

# Blog Post Writer
python examples/ai/crewai/blog_post_writer.py
```

Or via the Flux CLI:

```bash
# Start Flux
flux start server &
flux start worker worker-1 &

# Run workflows
flux workflow run multi_agent_code_review_crewai '{"code": "def foo(): pass"}'
flux workflow run blog_post_writer_crewai '{"topic": "The Future of AI Agents"}'
```
