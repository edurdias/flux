# AI-Enabled Agents with Flux

This directory contains examples of building AI-powered agents and workflows using Flux. These examples demonstrate how to leverage Flux's stateful execution, pause/resume capabilities, and distributed architecture to create robust AI applications.

## Examples Overview

### Conversational AI Agents

Stateful conversational agents that maintain context across multiple turns using Flux's pause/resume functionality.

| Example | Description | Use Case |
|---------|-------------|----------|
| **conversational_agent.py** | Base example with mock LLM | Learning and development |
| **conversational_agent_ollama.py** | Local LLM with Ollama (llama3) | Privacy-sensitive, offline, no API costs |
| **conversational_agent_openai.py** | OpenAI GPT-4o | Production-ready, powerful models |
| **conversational_agent_anthropic.py** | Anthropic Claude Sonnet 4.5 | Extended context, complex reasoning |

### RAG (Retrieval Augmented Generation)

AI agents enhanced with document retrieval for answering questions based on specific knowledge bases.

| Example | Description | Use Case |
|---------|-------------|----------|
| **rag_agent_ollama.py** | Fully local RAG with Ollama + FAISS | Documentation Q&A, knowledge bases, privacy-sensitive applications |

## Quick Start

### 1. Ollama (Local Development)

Perfect for getting started without API keys:

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama3

# Start Ollama service
ollama serve

# Register the workflow
flux workflow register examples/ai/conversational_agent_ollama.py

# Start a conversation
flux workflow run conversational_agent_ollama '{"message": "Why is the sky blue?"}'

# Resume the conversation (use execution_id from previous response)
flux workflow resume conversational_agent_ollama <execution_id> '{"message": "Why does the sky turn red and orange during sunset?"}'
```

### 2. OpenAI (Production)

For production applications with OpenAI:

```bash
# Set your API key
flux secrets set OPENAI_API_KEY "sk-..."

# Register the workflow
flux workflow register examples/ai/conversational_agent_openai.py

# Start a conversation with GPT-4o (default)
flux workflow run conversational_agent_openai '{"message": "Why is the sky blue?"}'

# Use GPT-4o-mini for faster, cheaper responses
flux workflow run conversational_agent_openai '{"message": "Why is the sky blue?", "model": "gpt-4o-mini"}'
```

### 3. Anthropic Claude

For extended context and advanced reasoning:

```bash
# Set your API key
flux secrets set ANTHROPIC_API_KEY "sk-ant-..."

# Register the workflow
flux workflow register examples/ai/conversational_agent_anthropic.py

# Start a conversation with Claude Sonnet 4.5 (default)
flux workflow run conversational_agent_anthropic '{"message": "Why is the sky blue?"}'

# Use Claude 3.7 Sonnet for older version
flux workflow run conversational_agent_anthropic '{"message": "Why is the sky blue?", "model": "claude-3-7-sonnet-20250219"}'
```

### 4. RAG Agent (Document Q&A)

Build a fully local RAG system that answers questions based on your documentation.

**Two-workflow pattern:** Index documents once with `rag_index_documents`, then query many times with `rag_query_documents`.

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull required models
ollama pull llama3
ollama pull nomic-embed-text

# Start Ollama service
ollama serve

# Register the workflows
flux workflow register examples/ai/rag_agent_ollama.py

# Step 1: Index documents once
flux workflow run rag_index_documents '{
  "docs_path": "./examples/ai/docs",
  "index_name": "my_docs"
}'

# Step 2: Query multiple times (reuses index - fast!)
flux workflow run rag_query_documents '{
  "index_name": "my_docs",
  "query": "What are Flux workflows?"
}'

flux workflow run rag_query_documents '{
  "index_name": "my_docs",
  "query": "How does task caching work?"
}'

# Or run the example directly
python examples/ai/rag_agent_ollama.py
```

## How It Works

### Stateful Conversations

Flux's execution context maintains conversation state across multiple turns:

```python
@workflow
async def conversational_agent(ctx: ExecutionContext):
    # Initialize state on first turn
    if not hasattr(ctx, '_conversation_state'):
        ctx._conversation_state = ConversationState()

    conversation = ctx._conversation_state

    while conversation.turn_count < max_turns:
        # Process user message
        user_message = ctx.input.get("message")
        conversation.add_user_message(user_message)

        # Generate response with LLM
        response = await call_llm(conversation)
        conversation.add_assistant_message(response)

        # Pause and wait for next input
        next_input = await pause("waiting_for_user_input", output=response)
        ctx.input = next_input
```

### Key Features

**Pause & Resume**
- Conversations can be paused indefinitely
- State is persisted automatically
- Resume with execution ID at any time

**Error Handling**
- Automatic retries on API failures
- Exponential backoff for rate limits
- Graceful error messages

**Secret Management**
- API keys stored securely
- Never exposed in logs or state
- Easy rotation and updates

**Distributed Execution**
- Run on multiple workers
- Scale conversations horizontally
- Load balancing built-in

### RAG Architecture

The RAG implementation uses a **two-workflow pattern** for production efficiency:

**Workflow 1: Index Documents (rag_index_documents)**
```python
@workflow
async def rag_index_documents(ctx: ExecutionContext):
    # 1. Load documents from directory
    documents = await load_markdown_documents(docs_path)

    # 2. Split into chunks with overlap
    chunks = await chunk_documents(documents, chunk_size=500, overlap=50)

    # 3. Generate embeddings via Ollama
    embeddings = await generate_embeddings(
        texts=[c["content"] for c in chunks],
        model="nomic-embed-text"
    )

    # 4. Build FAISS vector index
    index = await build_faiss_index(embeddings)

    # 5. Save to disk (~/.flux/rag_indexes/)
    save_index_to_disk(index, chunks, metadata)

    return {"status": "indexed", "num_chunks": len(chunks)}
```

**Workflow 2: Query Documents (rag_query_documents)**
```python
@workflow
async def rag_query_documents(ctx: ExecutionContext):
    # 1. Load index from disk
    index, chunks, metadata = load_index_from_disk(index_name)

    # 2. Generate query embedding
    query_embedding = await generate_embeddings([query])

    # 3. Retrieve top-k similar chunks
    relevant_chunks = await retrieve_relevant_chunks(
        query, index, chunks, top_k=3
    )

    # 4. Build context from chunks
    context = format_context(relevant_chunks)

    # 5. Generate answer with LLM
    answer = await generate_rag_response(query, context, model="llama3")

    return {"answer": answer, "sources": relevant_chunks}
```

**Key RAG Features:**
- **Two-Workflow Pattern**: Index once, query many times (production-ready)
- **Persistent Indexes**: Stored in `~/.flux/rag_indexes/` for reuse
- **Semantic Search**: FAISS L2 distance for finding relevant content
- **Source Attribution**: Each answer includes source documents
- **Chunk Overlap**: Prevents context loss at chunk boundaries
- **Fully Local**: All operations run on your machine (no external APIs)

## Configuration Options

All conversational agents support these configuration options:

```json
{
  "message": "Required: The user's message",
  "system_prompt": "Optional: System instructions for the AI",
  "model": "Optional: Specific model to use",
  "temperature": 0.7,
  "max_tokens": 500,
  "max_turns": 10
}
```

### Provider-Specific Options

**Ollama:**
```json
{
  "model": "llama3",  // or mistral, codellama, qwen2.5, etc.
  "ollama_url": "http://localhost:11434"
}
```

**OpenAI:**
```json
{
  "model": "gpt-4o",  // or gpt-4o-mini, gpt-4-turbo
  "temperature": 0.7,
  "max_tokens": 500
}
```

**Anthropic:**
```json
{
  "model": "claude-sonnet-4-5-20250929",  // or claude-3-7-sonnet-20250219
  "temperature": 1.0,
  "max_tokens": 1024
}
```

**RAG Agent:**
```json
// Indexing workflow
{
  "docs_path": "./path/to/docs",           // Required: path to docs
  "index_name": "my_docs",                 // Required: unique index name
  "chunk_size": 500,                       // Optional: chunk size
  "overlap": 50,                           // Optional: chunk overlap
  "embedding_model": "nomic-embed-text",   // Optional: embedding model
  "ollama_url": "http://localhost:11434"   // Optional: Ollama URL
}

// Query workflow
{
  "index_name": "my_docs",                 // Required: index to query
  "query": "Your question here",           // Required: question
  "llm_model": "llama3",                   // Optional: LLM model
  "top_k": 3,                              // Optional: chunks to retrieve
  "ollama_url": "http://localhost:11434"   // Optional: Ollama URL
}
```

## Running via HTTP API

You can also run these workflows via the Flux HTTP API:

```bash
# Start the Flux server
flux start server

# Execute workflow via API
curl -X POST 'http://localhost:8000/workflows/conversational_agent_openai/run/async' \
  -H 'Content-Type: application/json' \
  -d '{"message": "Why is the sky blue?"}'

# Resume conversation
curl -X POST 'http://localhost:8000/workflows/conversational_agent_openai/resume/<execution_id>/async' \
  -H 'Content-Type: application/json' \
  -d '{"message": "Why does the sky turn red and orange during sunset?"}'

# Check status
curl 'http://localhost:8000/workflows/conversational_agent_openai/status/<execution_id>?detailed=true'
```

## Use Cases

### Customer Support Bot
```python
initial_input = {
    "message": "I need help with my order #12345",
    "system_prompt": "You are a helpful customer support agent. Be empathetic and professional.",
    "max_turns": 20
}
```

### Code Assistant
```python
initial_input = {
    "message": "How do I implement a binary search in Python?",
    "system_prompt": "You are an expert programming tutor. Provide clear explanations with code examples.",
    "model": "gpt-4o"
}
```

### Research Assistant
```python
initial_input = {
    "message": "Summarize recent advances in quantum computing",
    "system_prompt": "You are a research assistant. Provide detailed, academic-quality summaries.",
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 4000
}
```

### Interview Bot
```python
initial_input = {
    "message": "I'm ready to start the interview",
    "system_prompt": "You are conducting a technical interview. Ask progressively harder questions.",
    "max_turns": 15
}
```

### RAG Documentation Assistant
```python
# Ask questions about your documentation
rag_input = {
    "docs_path": "./docs",
    "query": "How do I implement error handling in my workflows?",
    "chunk_size": 500,
    "overlap": 50,
    "llm_model": "llama3",
    "top_k": 3
}
```

**RAG Use Cases:**
- Internal documentation Q&A
- Customer support knowledge bases
- Technical documentation chatbots
- Product manual assistants
- Legal document search
- Research paper analysis

**Why RAG?**
- Grounds LLM responses in factual documents
- Reduces hallucinations with source attribution
- Works with private/proprietary data
- Updates easily (re-index documents)
- Fully local with Ollama (no external APIs)

## Monitoring & Debugging

### View Workflow Status

```bash
# Get detailed execution information
flux workflow status conversational_agent_openai <execution_id> --detailed

# View conversation history
flux workflow status conversational_agent_openai <execution_id> --detailed | jq '.output.conversation_history'
```

### Token Usage Tracking

The OpenAI and Anthropic examples track detailed token usage:

```json
{
  "total_input_tokens": 800,
  "total_output_tokens": 450,
  "total_tokens": 1250
}
```

### Logs

Enable debug logging to see detailed execution:

```bash
export FLUX_LOG_LEVEL=DEBUG
flux workflow run conversational_agent_openai '{"message": "Hello"}'
```

## Best Practices

### 1. Set Appropriate Limits

```python
{
  "max_turns": 10,        # Prevent infinite conversations
  "max_tokens": 500,      # Control response length
  "timeout": 60           # Prevent hung requests
}
```

### 2. Handle Errors Gracefully

All examples include retry logic and error handling:

```python
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    timeout=60
)
async def call_llm(...):
    # API call with automatic retries
```

### 3. Use System Prompts

Customize behavior with clear system prompts:

```python
{
  "system_prompt": (
    "You are a helpful assistant specializing in Python programming. "
    "Provide code examples when relevant. "
    "If you're unsure, say so rather than guessing."
  )
}
```

### 4. Monitor Costs

Track token usage to manage API costs:

```python
# Log token usage after each turn (OpenAI and Anthropic)
print(f"Input tokens: {result.output['total_input_tokens']}")
print(f"Output tokens: {result.output['total_output_tokens']}")
print(f"Total tokens: {result.output['total_tokens']}")

# Estimate cost based on your model's pricing
```

## Next Steps

### Building Multi-Agent Systems

Check out upcoming examples:
- `multi_agent_research.py` - Multiple agents collaborating on research
- `code_reviewer.py` - AI code review with multiple specialized agents
- `content_pipeline.py` - Content creation workflow with agents

### Integration Ideas

- **Slack/Discord Bot**: Deploy as a chat bot with webhook triggers
- **Customer Support**: Integrate with ticketing systems
- **Documentation**: Generate and maintain docs automatically
- **Testing**: AI-powered test generation and review

## Troubleshooting

### Ollama Connection Issues

```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# Restart Ollama
ollama serve
```

### API Key Issues

```bash
# Verify secret is set
flux secrets list

# Update API key
flux secrets set OPENAI_API_KEY "new-key"
```

### Workflow Not Found

```bash
# Re-register the workflow
flux workflow register examples/ai/conversational_agent_openai.py

# Verify registration
flux workflow list
```

### Pause/Resume Issues

```bash
# Check execution status
flux workflow status <workflow_name> <execution_id> --detailed

# Verify the workflow is actually paused
# Look for "state": "PAUSED" in the output
```

### RAG-Specific Issues

**Embedding Model Not Found:**
```bash
# Pull the embedding model
ollama pull nomic-embed-text

# Verify it's available
ollama list | grep nomic-embed-text
```

**No Documents Found:**
```bash
# Check directory exists and contains .md files
ls -la examples/ai/docs/*.md

# Verify path is correct (relative to where you run the command)
pwd
```

**FAISS Import Error:**
```bash
# Install FAISS dependency
poetry install

# Or directly with pip
pip install faiss-cpu
```

**Memory Issues with Large Document Sets:**
- Reduce `chunk_size` (e.g., from 500 to 300)
- Process fewer documents at once
- Use smaller embedding model
- Increase system memory allocation

**Poor Retrieval Quality:**
- Increase `top_k` to retrieve more chunks (e.g., 5 or 7)
- Adjust `chunk_size` and `overlap` for better context
- Try different embedding models (mxbai-embed-large)
- Ensure documents are well-formatted markdown

**Query Returns "No relevant information":**
- Check if documents were indexed successfully
- Verify query is related to document content
- Increase `top_k` to cast a wider net
- Review chunk content with detailed status

## Contributing

Have ideas for new AI agent examples? Contributions are welcome!

- Add new LLM providers (Mistral, Cohere, etc.)
- Create specialized agents (code review, data analysis, etc.)
- Build multi-agent workflows
- Improve error handling and retry logic

## Resources

- [Flux Documentation](https://edurdias.github.io/flux/)
- [OpenAI API Docs](https://platform.openai.com/docs)
- [Anthropic API Docs](https://docs.anthropic.com/)
- [Ollama Documentation](https://ollama.ai/docs)
- [FAISS Documentation](https://github.com/facebookresearch/faiss)
- [RAG Pattern Overview](https://www.promptingguide.ai/techniques/rag)
