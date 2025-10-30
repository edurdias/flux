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

### Function Calling / Tool Use

AI agents that can autonomously call external tools and APIs to answer questions requiring real-time data.

| Example | Description | Use Case |
|---------|-------------|----------|
| **function_calling_agent_ollama.py** | Local LLM with function calling (llama3.2, mistral) + Open-Meteo API | Weather queries, multi-city comparisons, forecasts, zero API keys |

### MCP Integration

AI assistants that use Model Context Protocol (MCP) for dynamic tool discovery.

| Example | Description | Use Case |
|---------|-------------|----------|
| **mcp_workflow_assistant_ollama.py** | Workflow assistant with MCP tool discovery | Natural language workflow management |

### Multi-Agent Systems

Multiple specialized agents executing in parallel.

| Example | Description | Use Case |
|---------|-------------|----------|
| **multi_agent_code_review_ollama.py** | 4 review agents (security, performance, style, testing) | Code review, vulnerability detection |

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

### 5. Function Calling Agent (Weather Assistant)

Build a fully local AI assistant that uses tools to answer questions requiring real-time data.

**Function calling pattern:** LLM decides when to call tools, executes them, and uses results to answer.

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model that supports tool calling
ollama pull llama3.2  # or mistral
ollama pull llama3.2  # llama3.2 has better tool calling support

# Start Ollama service
ollama serve

# Register the workflow
flux workflow register examples/ai/function_calling_agent_ollama.py

# Start a conversation with weather queries
flux workflow run function_calling_agent_ollama '{
  "message": "What is the weather like in San Francisco?"
}'

# Resume with multi-city comparison (use execution_id from previous response)
flux workflow resume function_calling_agent_ollama <execution_id> '{
  "message": "How does that compare to New York?"
}'

# Ask for a forecast
flux workflow resume function_calling_agent_ollama <execution_id> '{
  "message": "Give me a 5-day forecast for London"
}'

# Or run the example directly
python examples/ai/function_calling_agent_ollama.py
```

**Available Tools:**
- `get_current_weather`: Get current weather conditions for any city
- `get_weather_forecast`: Get N-day weather forecast (default: 7 days)
- `compare_weather`: Compare weather between two cities

**Example Conversation:**
```
User: "What's the weather in Paris?"
Assistant: *calls get_current_weather("Paris")*
Assistant: "The current weather in Paris is 15°C (59°F) with partly cloudy skies..."

User: "How does that compare to London?"
Assistant: *calls compare_weather("Paris", "London")*
Assistant: "Paris is currently 3°F warmer than London (59°F vs 56°F)..."
```

### 6. MCP Workflow Assistant

AI assistant that uses MCP protocol to discover and invoke Flux workflow operations.

```bash
# Prerequisites: Flux server, worker, and MCP server running
poetry run flux start server  # Terminal 1
poetry run flux start worker worker-1  # Terminal 2
poetry run flux start mcp  # Terminal 3

# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model that supports tool calling
ollama pull llama3.2

# Start Ollama service
ollama serve

# Register the workflow
flux workflow register examples/ai/mcp_workflow_assistant_ollama.py

# Start a conversation about workflows
flux workflow run mcp_workflow_assistant_ollama '{
  "message": "What workflows are available?"
}'

# Continue the conversation (use execution_id from previous response)
flux workflow resume mcp_workflow_assistant_ollama <execution_id> '{
  "message": "Run the hello_world workflow with name Alice"
}'

# Ask for workflow suggestions
flux workflow run mcp_workflow_assistant_ollama '{
  "message": "I want to process some data"
}'

# Check execution status
flux workflow resume mcp_workflow_assistant_ollama <execution_id> '{
  "message": "Show me the status of execution abc-123"
}'

# Or run the example directly
python examples/ai/mcp_workflow_assistant_ollama.py
```

**Tools discovered via MCP:**
- `list_workflows`, `get_workflow_details`
- `execute_workflow_async`, `execute_workflow_sync`
- `resume_workflow_async`, `resume_workflow_sync`
- `get_execution_status`, `cancel_execution`
- `upload_workflow`

**Example:**
```
User: "What workflows are available?"
Assistant: *calls list_workflows via MCP*
Assistant: "Here are the available workflows: hello_world, data_pipeline, email_sender..."

User: "Run the hello_world workflow with name 'Alice'"
Assistant: *calls execute_workflow_async via MCP*
Assistant: "Started hello_world workflow with execution ID: abc-123"

User: "What's the status?"
Assistant: *calls get_execution_status via MCP*
Assistant: "The workflow completed successfully. Output: Hello, Alice!"
```

**Configuration:**
```json
{
  "message": "What workflows are available?",
  "system_prompt": "You are a helpful AI assistant...",
  "model": "llama3.2",
  "ollama_url": "http://localhost:11434",
  "mcp_url": "http://localhost:8080/mcp",
  "max_turns": 20
}
```

### 7. Multi-Agent Code Review

4 specialized agents run in parallel to analyze code for security, performance, style, and testing issues.

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama3.2

# Start Ollama service
ollama serve

# Register the workflow
flux workflow register examples/ai/multi_agent_code_review_ollama.py

# Review code from a string
flux workflow run multi_agent_code_review_ollama '{
  "code": "def login(username, password):\n    query = f\"SELECT * FROM users WHERE username='\''{username}'\''\"",
  "file_path": "auth.py",
  "context": "User authentication module"
}'

# Use a different model
flux workflow run multi_agent_code_review_ollama '{
  "code": "def foo(): pass",
  "model": "qwen2.5-coder"
}'

# Or run the example directly
python examples/ai/multi_agent_code_review_ollama.py
```

**Agents:**
1. Security: SQL injection, XSS, auth issues, hardcoded secrets
2. Performance: Algorithm efficiency, memory usage, I/O, caching
3. Style: Readability, naming conventions, documentation
4. Testing: Coverage gaps, edge cases, test structure

**Example Output:**
```json
{
  "summary": {
    "total_issues": 9,
    "critical": 1,
    "high": 2,
    "medium": 4,
    "low": 2,
    "agents_completed": 4,
    "agents_failed": 0,
    "execution_time_seconds": 5.67
  },
  "by_agent": {
    "security": {
      "issues_found": 3,
      "findings": [
        {
          "severity": "critical",
          "category": "SQL Injection",
          "description": "String concatenation in SQL query allows injection",
          "line": 15,
          "suggestion": "Use parameterized queries or ORM"
        }
      ]
    },
    "performance": { ... },
    "style": { ... },
    "testing": { ... }
  }
}
```

**Configuration:**
```json
{
  "code": "def foo(): pass",
  "file_path": "app.py",
  "context": "Optional additional context",
  "model": "llama3.2",
  "ollama_url": "http://localhost:11434"
}
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

### Function Calling Architecture

The function calling implementation uses LLM-driven tool selection and execution:

**Workflow: Function Calling Agent (function_calling_agent_ollama)**
```python
@workflow
async def function_calling_agent_ollama(ctx: ExecutionContext):
    # 1. User asks a question
    user_message = ctx.input.get("message")
    messages.append({"role": "user", "content": user_message})

    # 2. Call LLM with available tools
    response = await call_ollama_with_tools(
        messages=messages,
        tools=WEATHER_TOOLS,  # Tool definitions
        model="llama3.2"
    )

    # 3. Check if LLM wants to use tools
    if response["message"].get("tool_calls"):
        for tool_call in response["message"]["tool_calls"]:
            tool_name = tool_call["function"]["name"]
            tool_args = tool_call["function"]["arguments"]

            # 4. Execute the tool
            result = await execute_tool_call(tool_name, tool_args)

            # 5. Add tool result to conversation
            messages.append({"role": "tool", "content": result})

        # 6. Call LLM again with tool results
        response = await call_ollama_with_tools(messages, tools, model)

    # 7. Return final answer to user
    assistant_message = response["message"]["content"]
    messages.append({"role": "assistant", "content": assistant_message})

    # 8. Pause for next input
    await pause("waiting_for_user_input")
```

**Tool Execution Flow:**
```python
@task
async def execute_tool_call(tool_name: str, tool_args: dict) -> str:
    """Route to appropriate tool and return JSON result."""
    if tool_name == "get_current_weather":
        # 1. Geocode location to lat/lon
        geo = await geocode_location(tool_args["location"])

        # 2. Call Open-Meteo API
        weather_data = await fetch_current_weather(geo["latitude"], geo["longitude"])

        # 3. Format and return result
        return json.dumps({
            "location": geo["name"],
            "temperature": weather_data["temperature_2m"],
            "conditions": decode_weather_code(weather_data["weather_code"]),
            "humidity": weather_data["relative_humidity_2m"],
            "wind_speed": weather_data["wind_speed_10m"]
        })
```

**Key Function Calling Features:**
- **LLM-Driven Tool Selection**: Model decides when and which tools to call
- **Multi-Tool Execution**: Can call multiple tools in sequence
- **Structured Arguments**: Type-safe tool parameters via JSON schema
- **Error Handling**: Retries and fallbacks for API calls
- **Fully Local**: Ollama + Open-Meteo (no API keys required)
- **Stateful Conversations**: Maintains context across tool calls
- **Tool Result Integration**: LLM sees tool outputs and uses them in responses

### MCP Architecture

**Workflow: mcp_workflow_assistant_ollama**
```python
@workflow
async def mcp_workflow_assistant_ollama(ctx: ExecutionContext):
    # 1. Connect to MCP server and discover tools dynamically
    mcp_tools = await discover_mcp_tools(mcp_url)

    # 2. Convert MCP tool schemas to Ollama format
    ollama_tools = await convert_mcp_tools_to_ollama(mcp_tools)

    # 3. Start conversation loop
    while turn < max_turns:
        # 4. Call LLM with dynamically discovered tools
        response = await call_ollama_with_mcp_tools(
            messages=messages,
            ollama_tools=ollama_tools,
            model="llama3.2"
        )

        # 5. Execute tool calls via MCP protocol
        if response["message"].get("tool_calls"):
            for tool_call in response["message"]["tool_calls"]:
                tool_name = tool_call["function"]["name"]
                tool_args = tool_call["function"]["arguments"]

                # Execute via MCP protocol
                result = await execute_mcp_tool(mcp_url, tool_name, tool_args)
                messages.append({"role": "tool", "content": result})

            # 6. Call LLM again with tool results
            response = await call_ollama_with_mcp_tools(messages, ollama_tools, model)

        # 7. Return response and pause for next input
        await pause("waiting_for_user_input")
```

**MCP Tool Discovery:**
```python
@task
async def discover_mcp_tools(mcp_url: str) -> list[dict[str, Any]]:
    """Discover tools dynamically from MCP server."""
    async with Client(mcp_url) as client:
        # Use MCP protocol to list available tools
        tools = await client.list_tools()

        # Convert to serializable format
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema,
            }
            for tool in tools
        ]
```

**MCP Tool Execution:**
```python
@task
async def execute_mcp_tool(mcp_url: str, tool_name: str, tool_args: dict) -> str:
    """Execute tool via MCP protocol."""
    async with Client(mcp_url) as client:
        # Call tool via MCP
        result = await client.call_tool(tool_name, tool_args)

        # Extract text content from MCP response
        if result and len(result) > 0:
            content = result[0]
            if hasattr(content, "text"):
                return content.text
            return json.dumps({"result": str(content)})
```

### Multi-Agent Architecture

**Workflow: multi_agent_code_review_ollama**
```python
@workflow
async def multi_agent_code_review_ollama(ctx: ExecutionContext):
    # 1. Load code from file or string
    code = load_code(ctx.input.get("code_path") or ctx.input.get("code"))

    # 2. Run specialized agents in parallel using flux.tasks.parallel()
    reviews = await parallel(
        security_review(code, model, ollama_url),
        performance_review(code, model, ollama_url),
        style_review(code, model, ollama_url),
        testing_review(code, model, ollama_url)
    )

    # 3. Aggregate results from all agents
    aggregated = await aggregate_reviews(reviews)

    # 4. Generate comprehensive summary report
    report = await generate_summary_report(aggregated, code)

    return report
```

**Specialized Agent Implementation:**
```python
@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def security_review(code: str, model: str, ollama_url: str) -> dict:
    """Security-focused code analysis agent."""
    system_prompt = """You are a security expert. Analyze code for:
    - SQL injection vulnerabilities
    - XSS (cross-site scripting) risks
    - Authentication/authorization issues
    - Hardcoded secrets or credentials
    - Insecure cryptography
    - Path traversal vulnerabilities

    Return JSON array of findings with severity, category, description, line, suggestion."""

    # Call LLM with specialized prompt
    response = await call_ollama(code, system_prompt, model, ollama_url)

    # Parse LLM response with robust JSON handling
    findings = parse_llm_json_response(response["message"]["content"])

    return {
        "agent": "security",
        "status": "success",
        "findings": findings,
        "issues_found": len(findings)
    }
```

**Parallel Execution with Flux:**
```python
from flux.tasks import parallel

# Execute all agents concurrently
reviews = await parallel(
    agent1_task(args),
    agent2_task(args),
    agent3_task(args),
    agent4_task(args)
)
# Returns list of results once all tasks complete
```

**Result Aggregation:**
```python
@task
async def aggregate_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine results from all agents."""
    by_agent: dict[str, Any] = {}
    all_findings: list[dict[str, Any]] = []
    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    for review in reviews:
        agent_name = review["agent"]
        by_agent[agent_name] = review

        if review["status"] == "success":
            for finding in review.get("findings", []):
                all_findings.append({**finding, "agent": agent_name})
                severity = finding.get("severity", "low")
                counts[severity] = counts.get(severity, 0) + 1

    return {
        "by_agent": by_agent,
        "all_findings": all_findings,
        "severity_counts": counts,
        "total_issues": len(all_findings)
    }
```

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

**Function Calling Agent:**
```json
{
  "message": "What's the weather in Tokyo?",  // Required: user message
  "system_prompt": "You are a helpful weather assistant...",  // Optional: system instructions
  "model": "llama3.2",                     // Optional: llama3.2 (default), mistral
  "temperature": 0.7,                      // Optional: response randomness
  "max_turns": 10,                         // Optional: max conversation turns
  "ollama_url": "http://localhost:11434"   // Optional: Ollama URL
}
```

**Supported Models for Function Calling:**
- `llama3.2` (recommended) - Best tool calling support
- `mistral` - Good alternative with tool support
- Other Ollama models with tool/function calling capabilities

**Example Queries:**
- "What's the weather in Paris?"
- "Give me a 5-day forecast for London"
- "Compare the weather in San Francisco and New York"
- "Is it warmer in Miami or Los Angeles right now?"

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

### Weather Assistant (Function Calling)
```python
# Ask weather-related questions that require real-time data
weather_input = {
    "message": "What's the weather like in Seattle?",
    "system_prompt": "You are a helpful weather assistant. Use tools to get accurate weather data.",
    "model": "llama3.2",
    "max_turns": 15
}
```

**Function Calling Use Cases:**
- Real-time data queries (weather, stock prices, etc.)
- Multi-step information gathering
- Tool-augmented research assistants
- API integration workflows
- Data comparison and analysis
- Event-driven automations

**Why Function Calling?**
- Enables LLMs to access real-time information
- Structured tool execution with type safety
- Autonomous tool selection by the LLM
- Combines reasoning with external data
- Fully local with Ollama + free APIs (no OpenAI/Anthropic needed)
- Extensible to any API or tool

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

We now have working multi-agent examples! Check out:
- **multi_agent_code_review_ollama.py** - 4 specialized review agents (security, performance, style, testing) working in parallel
- **mcp_workflow_assistant_ollama.py** - MCP-integrated workflow management assistant

Build your own multi-agent systems:
```python
from flux import workflow, task, ExecutionContext
from flux.tasks import parallel

@task
async def research_agent(topic: str) -> dict:
    """Agent that researches a topic."""
    # Your research logic
    return {"findings": [...]}

@task
async def analysis_agent(data: dict) -> dict:
    """Agent that analyzes data."""
    # Your analysis logic
    return {"insights": [...]}

@workflow
async def multi_agent_research(ctx: ExecutionContext):
    # Run agents in parallel
    results = await parallel(
        research_agent("AI trends"),
        research_agent("ML frameworks"),
        analysis_agent(ctx.input.get("data"))
    )

    # Aggregate and return
    return aggregate_results(results)
```

### Integration Ideas

- Slack/Discord bots with webhook triggers
- Customer support ticketing systems
- Documentation generation
- Test generation and review

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

### Function Calling Issues

**Model Not Calling Tools:**
```bash
# Ensure you're using a model with tool support
ollama pull llama3.2  # llama3.2 has better function calling than llama3

# Verify model is running
ollama list | grep llama3.2
```

**Tool Execution Errors:**
- Check network connectivity for Open-Meteo API
- Verify location name is valid (try major cities first)
- Review error messages in workflow status output
- Check that httpx is installed: `poetry show httpx`

**LLM Not Understanding Tool Results:**
- Ensure tool results are valid JSON
- Check that tool result format matches expectations
- Try increasing temperature for more creative responses
- Verify model supports function calling (llama3.2, mistral)

**Weather Data Inaccurate or Missing:**
- Open-Meteo API requires valid geocoding
- Try using full city names with country (e.g., "Paris, France")
- Check API status at https://open-meteo.com/
- Review geocoding results in detailed status

### MCP Integration Issues

**MCP Server Not Running:**
```bash
# Start the Flux MCP server
poetry run flux start mcp

# Verify it's accessible
curl http://localhost:8080/mcp
```

**Tool Discovery Fails:**
```bash
# Check MCP server logs for errors
# Verify Flux server is running first
poetry run flux start server

# Ensure worker is running
poetry run flux start worker worker-1

# Test MCP connection manually
python -c "from fastmcp import Client; import asyncio; asyncio.run(Client('http://localhost:8080/mcp').list_tools())"
```

**Tools Not Available to LLM:**
- Ensure model supports tool calling (llama3.2, mistral)
- Verify MCP tools were discovered (check workflow output)
- Review tool schema conversion in detailed status
- Check that mcp_url is correct in configuration

**Workflow Execution via MCP Fails:**
- Verify workflow is registered: `flux workflow list`
- Check execution status: `flux workflow status <workflow> <execution_id>`
- Ensure worker is processing tasks
- Review MCP server logs for errors

### Multi-Agent System Issues

**Agents Failing or Timing Out:**
```bash
# Increase timeout for slow models
flux workflow run multi_agent_code_review_ollama '{
  "code_path": "./src/app.py",
  "timeout": 120
}'

# Check if model is downloaded
ollama list | grep qwen2.5-coder

# Pull model if missing
ollama pull qwen2.5-coder
```

**Inconsistent JSON Parsing:**
- The system includes robust JSON parsing with multiple fallback strategies
- If still failing, check workflow status for raw LLM output
- Try lowering temperature for more consistent output (0.2-0.3)
- Verify model supports structured output (qwen2.5-coder recommended)

**Not All Agents Running:**
- Check that all agents are enabled in configuration
- Review parallel execution results in detailed status
- Individual agent failures don't stop the workflow (graceful degradation)
- Look for specific agent errors in task history

**Poor Code Analysis Quality:**
- Use qwen2.5-coder model (specialized for code)
- Lower temperature for more focused analysis (0.2-0.3)
- Provide language hint for better context
- Ensure code sample is complete and well-formatted

**High Execution Time:**
- Parallel execution should take ~5-10 seconds for 4 agents
- If slower, check Ollama performance and model size
- Consider using smaller/faster models
- Disable unused agents to speed up execution

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
- [Ollama Function Calling](https://ollama.com/blog/tool-support)
- [FAISS Documentation](https://github.com/facebookresearch/faiss)
- [RAG Pattern Overview](https://www.promptingguide.ai/techniques/rag)
- [Open-Meteo API](https://open-meteo.com/en/docs)
