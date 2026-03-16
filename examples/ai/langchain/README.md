# LangChain + Flux Examples

This directory contains examples of integrating LangChain and LangGraph with Flux for building durable, production-ready AI workflows.

## Why Flux + LangChain?

LangChain provides a rich ecosystem for LLM interaction — prompt templates, chains, memory abstractions, document loaders, and vector stores. Flux adds the operational layer that production AI systems need:

- **Durability** — workflow state is persisted; crashes don't lose progress
- **Pause/resume** — suspend mid-workflow and resume with new input (e.g., human-in-the-loop)
- **Retries** — automatic retry with configurable backoff for flaky LLM or tool calls
- **Scheduling** — run workflows on a cron schedule without extra infrastructure
- **Worker distribution** — fan out across multiple workers for parallel execution
- **Secrets management** — store API keys securely with `flux secrets set`; access them in workflows without hardcoding
- **Observability** — full execution history, tracing, and OpenTelemetry integration out of the box

## Examples

| Example | LangChain + Flux | Pure Flux Equivalent | Pattern |
|---------|-----------------|----------------------|---------|
| Conversational Agent | [conversational_agent.py](conversational_agent.py) | [../conversational_agent_ollama.py](../conversational_agent_ollama.py) | ChatOllama + message history vs direct Ollama SDK |
| RAG Pipeline | [rag_pipeline.py](rag_pipeline.py) | [../rag_agent_ollama.py](../rag_agent_ollama.py) | LCEL + Chroma vs FAISS + raw embeddings |
| Multi-Agent Code Review | [multi_agent_code_review.py](multi_agent_code_review.py) | [../multi_agent_code_review_ollama.py](../multi_agent_code_review_ollama.py) | LangGraph StateGraph vs Flux Graph |

## Setup

### Ollama

All examples use Ollama for local LLM inference. No API keys required.

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull required models
ollama pull llama3
ollama pull llama3.2
ollama pull nomic-embed-text

# Start Ollama service
ollama serve
```

### Python Dependencies

Install only what each example needs, or install everything at once.

**Conversational Agent:**
```bash
pip install langchain-core langchain-ollama
```

**RAG Pipeline:**
```bash
pip install langchain-core langchain-ollama langchain-chroma langchain-community langchain-text-splitters
```

> Note: `langchain-chroma` pulls in `chromadb` which has heavy transitive dependencies (including C++ build tools on some platforms). If you only need the conversational or multi-agent examples, skip this install.

**Multi-Agent Code Review:**
```bash
pip install langchain-core langchain-ollama langgraph
```

**Install all at once:**
```bash
pip install langchain-core langchain-ollama langchain-chroma langchain-community langchain-text-splitters langgraph
```

## Running the Examples

### Conversational Agent

```bash
# Run directly
python examples/ai/langchain/conversational_agent.py

# Or via Flux
flux workflow run conversational_agent_langchain '{"message": "Why is the sky blue?"}'

# Resume the conversation
flux workflow resume conversational_agent_langchain <execution_id> '{"message": "Why does the sky turn red at sunset?"}'
```

### RAG Pipeline

```bash
# Run directly
python examples/ai/langchain/rag_pipeline.py

# Or via Flux — index documents first
flux workflow run rag_index_langchain '{
    "docs_path": "./examples/ai/docs",
    "collection_name": "flux_docs"
}'

# Then query
flux workflow run rag_query_langchain '{
    "collection_name": "flux_docs",
    "query": "What are Flux workflows?"
}'
```

### Multi-Agent Code Review

```bash
# Run directly
python examples/ai/langchain/multi_agent_code_review.py

# Or via Flux
flux workflow run multi_agent_code_review_langgraph '{
    "code": "def process(data):\n    return [x*2 for x in data]"
}'

# With a specific model
flux workflow run multi_agent_code_review_langgraph '{
    "code": "def process(data):\n    return [x*2 for x in data]",
    "model": "llama3.2"
}'
```
