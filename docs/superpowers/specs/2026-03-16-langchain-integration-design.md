# Flux + LangChain/LangGraph Integration Design

## Problem Statement

The AI workflow landscape is fragmented. Agent frameworks (LangChain, LangGraph, CrewAI) handle LLM-specific patterns well but lack infrastructure concerns: scheduling, durability, resource management, secrets, distributed execution. Workflow engines (Prefect, Temporal) handle infrastructure but don't understand AI workloads. No single tool bridges both.

Flux is uniquely positioned to be the orchestration layer above agent frameworks — not competing with them, but making them reliable, observable, and production-ready.

## Goals

- Position Flux as the workflow engine for AI workloads by demonstrating seamless orchestration of LangChain/LangGraph alongside pure Flux alternatives.
- Show that Flux's existing primitives (tasks, workflows, Graph, parallel, pause/resume, resource-aware workers) already cover most AI orchestration patterns.
- Provide a progressive integration path: examples → dedicated tasks → deep integration.

## Non-Goals

- Replacing LangChain/LangGraph — Flux orchestrates them, not competes with them.
- Building a custom agent framework inside Flux.
- Cross-framework state translation (deferred until real usage patterns emerge).
- Streaming token delivery architecture (separate design concern).
- Cost/token tracking (can layer on existing OpenTelemetry infrastructure later).

## Target User

General-purpose, but most beneficial for teams orchestrating AI agent frameworks (LangChain, LangGraph, CrewAI) who need reliability, durability, scheduling, and resource management without the complexity of Temporal or Kubernetes-native tools.

## Competitive Landscape

| Feature | Prefect | Temporal | LangGraph | Flux |
|---------|---------|----------|-----------|------|
| Durable execution | Partial | Yes | Yes (1.0) | Yes |
| GPU-aware scheduling | No | No | No | Yes |
| Human-in-the-loop | Partial | Yes (signals) | Yes (interrupt) | Yes (pause/resume) |
| Streaming execution | No | No | Partial | Yes (SSE) |
| Built-in secrets | No | No | No | Yes (AES-256) |
| MCP integration | No | No | No | Yes |
| Zero-dep deployment | No | No | No | Yes (SQLite) |
| Agent framework orchestration | No | Partial (OpenAI SDK) | N/A (is one) | Target |

### LangGraph Production Gaps That Flux Fills

- No distributed worker execution
- No built-in scheduling (cron/interval/once)
- No secrets management
- No multi-tenant isolation primitives
- Limited scaling — no native multi-worker distribution
- LangGraph Platform costs $39+/user/month for deployment

## Design

### Integration Philosophy

Flux's task extensibility model is the right abstraction for framework integration. Custom tasks wrap each framework's execution model, following the same patterns as built-in tasks like `Graph` and `parallel`. This means:

- No changes to Flux's core engine
- No changes required on the framework side
- Framework integrations are installable task packages
- Users bring their existing chains/graphs unchanged

### Three-Phase Approach

#### Phase 1: Examples (This Work)

Side-by-side examples showing the same AI patterns implemented with LangChain+Flux and purely in Flux. Demonstrates value of both approaches.

#### Phase 2: Opaque Dedicated Tasks (Future)

Extract proven patterns into a `flux-langchain` package with reusable tasks:

- `langchain_invoke(runnable, input, config)` — wraps any LangChain `Runnable.ainvoke()` as a Flux task with retries, timeouts, and secrets injection.
- `langgraph_invoke(graph, input, thread_id, config)` — wraps a compiled LangGraph `CompiledStateGraph`. LangGraph manages its own internal checkpointing; Flux manages the outer workflow lifecycle.

#### Phase 3: Deep Integration (Future)

Deferred until real usage informs the design. Potential directions:

- Custom `BaseCheckpointSaver` backed by Flux's event store
- LangGraph `interrupt()` mapped to Flux `pause()`
- Flux `BaseCallbackHandler` feeding LangChain lifecycle events into OpenTelemetry

### Phase 1: Example Set

Three examples, each with a LangChain+Flux variant and a pure Flux variant:

| # | Example | LangChain+Flux | Pure Flux |
|---|---------|----------------|-----------|
| 1 | Conversational Agent | `ChatOllama` + conversation memory | Existing `conversational_agent_ollama.py` |
| 2 | RAG Pipeline | `OllamaEmbeddings` + `Chroma` + LCEL | Existing `rag_agent_ollama.py` |
| 3 | Multi-Agent Code Review | LangGraph `StateGraph` + parallel nodes | Existing `multi_agent_code_review_ollama.py` (updated to use Flux `Graph`) |

#### File Structure

```
examples/ai/langchain/
├── conversational_agent.py          # LangChain + Flux
├── rag_pipeline.py                  # LangChain + Flux
├── multi_agent_code_review.py       # LangGraph + Flux
└── README.md                        # Explains pairs, how to run, dependencies
```

Existing pure Flux examples in `examples/ai/` serve as the comparison variants. The multi-agent code review example will be updated to use Flux's `Graph` task instead of `parallel()` for better feature parity with LangGraph's `StateGraph`.

#### Example Conventions

- All examples use Ollama as the LLM provider (consistent with existing examples, no paid API keys required).
- Each file is self-contained — no shared utilities across examples.
- Each example includes a docstring header explaining: what it does, the LangChain pattern used, and where to find the pure Flux equivalent.
- LangChain dependencies are optional — not added to Flux's core `pyproject.toml`. Each example lists its `pip install` requirements in the header comment.

#### Example 1: Conversational Agent

**LangChain + Flux** (`examples/ai/langchain/conversational_agent.py`):

- Uses LangChain's `ChatOllama` for LLM calls and `ChatMessageHistory` for conversation memory
- LangChain manages message formatting and memory windowing
- Flux manages the outer workflow: pause/resume for human-in-the-loop turns, retries, scheduling, durability
- Each conversation turn is a Flux `@task` that invokes the LangChain chain

**Pure Flux** (existing `examples/ai/conversational_agent_ollama.py`):

- Uses Ollama `AsyncClient` directly for chat API calls
- Manual message history management (list of dicts)
- Same pause/resume pattern for multi-turn

Both examples are comparable in scope: multi-turn conversational agents with pause/resume. The LangChain variant shows how its abstractions reduce message management boilerplate, while the pure Flux variant shows the same is achievable with direct SDK calls.

**Demonstrates:** LangChain simplifies LLM interaction and memory management. Flux provides durable multi-turn conversations that survive crashes, scheduling, worker distribution, and secrets management — capabilities LangChain does not offer.

#### Example 2: RAG Pipeline

**LangChain + Flux** (`examples/ai/langchain/rag_pipeline.py`):

- Uses LangChain's `OllamaEmbeddings`, `Chroma` vector store, LCEL chain (`retriever | prompt | llm | parser`)
- Two Flux workflows mirroring the existing pattern:
  - `rag_index_documents` — document loading, chunking, embedding, indexing via LangChain
  - `rag_query_documents` — retrieval + generation via LCEL chain
- Each stage (load, chunk, embed, index, retrieve, generate) is a Flux `@task`

**Pure Flux** (existing `examples/ai/rag_agent_ollama.py`):

- Uses `AsyncClient` directly for embeddings and generation
- FAISS for vector similarity search, numpy for embedding operations
- Already uses the two-workflow pattern (index + query with pause/resume)
- Manual document loading, chunking, and embedding pipeline

Both examples share the same two-workflow structure. The LangChain variant shows how its ecosystem (document loaders, text splitters, `OllamaEmbeddings`, Chroma vector store, LCEL chain composition) reduces the boilerplate of building each RAG stage. The pure Flux variant shows the same pipeline built with direct SDK calls and FAISS.

**Demonstrates:** LangChain's retriever/vector store ecosystem provides a richer set of components (document loaders for many formats, multiple vector store backends, LCEL composability). Flux provides the workflow infrastructure both variants rely on: durable indexing pipelines, retry on failed embedding batches, and schedulable query workflows.

#### Example 3: Multi-Agent Code Review

**LangGraph + Flux** (`examples/ai/langchain/multi_agent_code_review.py`):

- Uses LangGraph's `StateGraph` with specialist reviewer nodes (security, performance, style, testing)
- Conditional edges route based on review results
- LangGraph manages graph execution and state reducers
- Flux wraps the entire graph as a `@task`, providing: worker distribution, scheduling, retries, durability

**Pure Flux** (existing `examples/ai/multi_agent_code_review_ollama.py`, updated to use `Graph`):

- Uses Flux's `Graph` task with nodes for each specialist reviewer
- Conditional edges (e.g., skip testing review based on input)
- Aggregation node collects all reviewer outputs
- `START` → reviewers → aggregation → `END`

**Demonstrates:** LangGraph's `StateGraph` vs Flux's `Graph` — two approaches to the same multi-agent pattern. Flux's built-in Graph is simpler for this use case. LangGraph adds value for complex shared mutable state between agents; Flux adds value as the outer durability/scheduling layer.

### Resource Management

Flux's existing `ResourceRequest` with CPU/memory/disk/GPU matching is sufficient for Phase 1. Workers declare their resources, workflows declare requirements, Flux matches them. This already exceeds what Prefect and Temporal offer.

Future phases may add:
- Concurrency controls (rate limiting per workflow/task)
- Model-aware scheduling (route to workers with models loaded in GPU memory)

## Development

### Branch

All work will be done on a feature branch `feature/langchain-integration` created from `main`.

### Dependencies

LangChain dependencies are example-only, not core:
- `langchain-core`
- `langchain-ollama`
- `langchain-chroma` (RAG example)
- `langgraph` (multi-agent example)

Note: `langchain-chroma` pulls in transitive dependencies (`onnxruntime`, `tokenizers`, etc.). The README should document this as a heavier install for the RAG example.

### Phase 2 Note: Serialization

The Phase 2 task signatures (`langchain_invoke`, `langgraph_invoke`) assume `Runnable` and `CompiledStateGraph` objects can be passed around. These objects contain closures, API clients, and other non-serializable state. Serialization strategy is an open design question for Phase 2 — the signatures shown are conceptual, not final.

### Acceptance Criteria (Phase 1)

- Each LangChain example runs successfully against a local Ollama instance.
- Each example follows existing conventions: docstring header with prerequisites and usage, `__main__` block for standalone execution, error handling with helpful messages.
- The existing `multi_agent_code_review_ollama.py` is updated in-place to use Flux's `Graph` task instead of `parallel()`.
- The `examples/ai/langchain/README.md` covers: setup instructions, dependency installation, and explains each example pair with links to the pure Flux equivalent.
- No tests are expected — consistent with existing AI examples which are inherently not testable in CI (require a running Ollama instance).

### Open Questions

1. **Streaming architecture** — How should Flux handle LLM token streaming without exploding the event history? Deferred to separate design.
2. **Cross-framework state** — How should state flow between tasks wrapping different frameworks in the same workflow? Deferred until Phase 2 usage patterns emerge.
