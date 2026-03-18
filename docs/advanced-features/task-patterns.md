# Task Patterns

## Parallel Execution

Parallel execution allows multiple tasks to run concurrently, improving performance for independent operations.

### Using the Parallel Task
```python
from flux import task, workflow, ExecutionContext
from flux.tasks import parallel

@task
async def say_hi(name: str):
    return f"Hi, {name}"

@task
async def say_hello(name: str):
    return f"Hello, {name}"

@task
async def say_hola(name: str):
    return f"Hola, {name}"

@workflow
async def parallel_workflow(ctx: ExecutionContext[str]):
    results = await parallel(
        say_hi(ctx.input),
        say_hello(ctx.input),
        say_hola(ctx.input)
    )
    return results
```

Key features:
- Executes tasks concurrently using ThreadPoolExecutor
- Automatically manages thread pool based on CPU cores
- Returns results in order of task definition
- Handles failures in individual tasks

## Pipeline Processing

Pipelines chain multiple tasks together, passing results from one task to the next.

### Using the Pipeline Task
```python
from flux import task, workflow, ExecutionContext
from flux.tasks import pipeline

@task
async def multiply_by_two(x):
    return x * 2

@task
async def add_three(x):
    return x + 3

@task
async def square(x):
    return x * x

@workflow
async def pipeline_workflow(ctx: ExecutionContext[int]):
    result = await pipeline(
        multiply_by_two,
        add_three,
        square,
        input=ctx.input
    )
    return result
```

Key features:
- Sequential task execution
- Automatic result passing between tasks
- Clear data transformation flow
- Error propagation through the pipeline

## Task Mapping

Task mapping applies a single task to multiple inputs in parallel.

### Basic Task Mapping
```python
from flux import task, workflow, ExecutionContext

@task
async def process_item(item: str):
    return item.upper()

@workflow
async def mapping_workflow(ctx: ExecutionContext[list[str]]):
    # Process multiple items in parallel
    results = await process_item.map(ctx.input)
    return results
```

### Complex Mapping
```python
@task
async def count(to: int):
    return [i for i in range(0, to + 1)]

@workflow
async def task_map_workflow(ctx: ExecutionContext[int]):
    # Generate sequences in parallel
    results = await count.map(list(range(0, ctx.input)))
    return len(results)
```

Key features:
- Parallel processing of multiple inputs
- Automatic thread pool management
- Result aggregation
- Error handling for individual mappings

## Graph

Graphs allow complex task dependencies and conditional execution paths.

### Basic Graph
```python
from flux import task, workflow, ExecutionContext
from flux.tasks import Graph

@task
async def get_name(input: str) -> str:
    return input

@task
async def say_hello(name: str) -> str:
    return f"Hello, {name}"

@workflow
async def graph_workflow(ctx: ExecutionContext[str]):
    hello = (
        Graph("hello_world")
        .add_node("get_name", get_name)
        .add_node("say_hello", say_hello)
        .add_edge("get_name", "say_hello")
        .start_with("get_name")
        .end_with("say_hello")
    )
    return await hello(ctx.input)
```

### Conditional Graph Execution
```python
@workflow
async def conditional_graph_workflow(ctx: ExecutionContext):
    workflow = (
        Graph("conditional_flow")
        .add_node("validate", validate_data)
        .add_node("process", process_data)
        .add_node("error", handle_error)
        .add_edge("validate", "process",
                 condition=lambda result: result.get("valid"))
        .add_edge("validate", "error",
                 condition=lambda result: not result.get("valid"))
        .start_with("validate")
        .end_with("process")
        .end_with("error")
    )
    return await workflow(ctx.input)
```

Key features:
- Define complex task dependencies
- Conditional execution paths
- Automatic validation of graph structure
- Clear visualization of workflow logic
- Flexible error handling paths

## AI Agents

The `agent()` factory creates Flux tasks that call LLMs. Each agent is a regular `@task` — it composes with `parallel()`, `Graph`, `pause()`, and all other Flux primitives.

### Basic Agent

```python
from flux import workflow, ExecutionContext
from flux.tasks.ai import agent

researcher = agent(
    "You are a research analyst. Find key findings and trends.",
    model="ollama/llama3",
    name="researcher",
)

@workflow
async def research_workflow(ctx: ExecutionContext[dict]):
    return await researcher(ctx.input["topic"])
```

### Agent Pipeline with Context

Agents can pass output between stages using the `context` parameter:

```python
researcher = agent("You are a researcher.", model="ollama/llama3", name="researcher")
writer = agent("You are a writer.", model="ollama/llama3", name="writer")
editor = agent("You are an editor.", model="ollama/llama3", name="editor")

@workflow
async def blog_pipeline(ctx: ExecutionContext[dict]):
    topic = ctx.input["topic"]
    research = await researcher(f"Research: {topic}")
    draft = await writer(f"Write about: {topic}", context=research)
    return await editor(f"Edit:", context=draft)
```

Each agent appears as a named task in the execution event history:

```
TASK_STARTED    researcher     {"instruction": "Research: AI Agents"}
TASK_COMPLETED  researcher     "Key findings: ..."
TASK_STARTED    writer         {"instruction": "Write about: AI Agents"}
TASK_COMPLETED  writer         "Blog post draft: ..."
TASK_STARTED    editor         {"instruction": "Edit:"}
TASK_COMPLETED  editor         "Final polished post: ..."
```

### Provider Support

The model string selects the LLM provider:

```python
# Local (Ollama)
agent("...", model="ollama/llama3")
agent("...", model="ollama/llama3.2")

# OpenAI (requires OPENAI_API_KEY env var)
agent("...", model="openai/gpt-4o")

# Anthropic (requires ANTHROPIC_API_KEY env var)
agent("...", model="anthropic/claude-sonnet-4-20250514")
```

### Tool Use

Existing `@task` functions can be used as agent tools. The agent inspects the function signature and docstring to build tool schemas automatically:

```python
from flux import task
from flux.tasks.ai import agent

@task.with_options(timeout=30)
async def search_web(query: str) -> str:
    """Search the web and return relevant results."""
    ...

@task.with_options(timeout=10)
async def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    ...

assistant = agent(
    "You are a helpful assistant. Use tools when needed.",
    model="ollama/llama3",
    tools=[search_web, get_weather],
)
```

Tool calls appear as child tasks in the execution trace:

```
TASK_STARTED    assistant
TASK_STARTED    search_web       {"query": "AI agents 2026"}
TASK_COMPLETED  search_web       "Results: ..."
TASK_COMPLETED  assistant        "Based on my research: ..."
```

### Structured Output

Use `response_format` with a Pydantic model for typed responses:

```python
from pydantic import BaseModel

class ResearchFindings(BaseModel):
    topic: str
    key_points: list[str]
    sources: list[str]

researcher = agent(
    "You are a researcher. Return structured findings.",
    model="ollama/llama3",
    response_format=ResearchFindings,
)

# Returns a ResearchFindings instance, not a string
result = await researcher("Research AI agents")
print(result.key_points)
```

### Stateful Conversations

With `stateful=True`, the agent accumulates message history across invocations within a workflow execution:

```python
chatbot = agent(
    "You are a helpful assistant.",
    model="ollama/llama3",
    stateful=True,
)

@workflow
async def conversation(ctx: ExecutionContext[dict]):
    r1 = await chatbot("What is Python?")        # standalone message
    r2 = await chatbot("What about asyncio?")     # knows about the Python context
    return r2
```

**Caveats:**
- Stateful history is in-memory. If the workflow crashes, history is lost on resume. For crash-durable conversations, manage history at the workflow level using `pause()`/`resume()`.
- Stateful agents must not be called concurrently (e.g., via `parallel()`). Concurrent invocations share the same message list and will corrupt the history. Use separate `agent()` instances for concurrent use.

### Task Options

Agents return regular Flux tasks. Use `with_options()` to customize retries, timeouts, and other task behavior:

```python
researcher = agent(
    "You are a researcher.",
    model="ollama/llama3",
    name="researcher",
).with_options(
    retry_max_attempts=5,
    timeout=300,
)
```

### Agent Composition

Since agents are tasks, they compose with all Flux primitives:

```python
from flux.tasks import parallel, Graph

# Parallel agents
reviews = await parallel(
    security_reviewer("Review:\n" + code),
    performance_reviewer("Review:\n" + code),
)

# Graph-based agent pipeline
graph = (
    Graph("review")
    .add_node("security", security_reviewer)
    .add_node("performance", performance_reviewer)
    .add_node("aggregate", aggregate_results)
    .start_with("security")
    .start_with("performance")
    .add_edge("security", "aggregate")
    .add_edge("performance", "aggregate")
    .end_with("aggregate")
)

# Human-in-the-loop with agents
from flux.tasks import pause

research = await researcher("Research AI")
feedback = await pause("human_review")
final = await writer("Revise based on feedback", context=f"{research}\n\n{feedback}")
```

## Pattern Selection Guidelines

Choose the appropriate pattern based on your needs:

1. **Parallel Execution** when:
   - Tasks are independent
   - You want to improve performance
   - Order of execution doesn't matter

2. **Pipeline** when:
   - Tasks form a sequential chain
   - Each task depends on previous results
   - You need clear data transformation steps

3. **Task Mapping** when:
   - Same operation applies to multiple items
   - Items can be processed independently
   - You want to parallelize processing

4. **Graph** when:
   - You have complex task dependencies
   - You need conditional execution paths
   - Workflow has multiple possible paths

5. **AI Agent** when:
   - Your task needs to call an LLM
   - You want provider abstraction (switch between Ollama/OpenAI/Anthropic)
   - You need tool use, structured output, or conversation history
   - You want LLM calls as observable, retryable Flux tasks

## Performance Considerations

### Parallel Execution Performance

#### Thread Pool Management
```python
from flux.tasks import parallel

@workflow
async def parallel_workflow(ctx: ExecutionContext):
    # Tasks are executed using ThreadPoolExecutor
    # Number of workers = CPU cores available
    results = await parallel(
        task1(),
        task2(),
        task3()
    )
```

Key considerations:
- Uses Python's asyncio for concurrent task execution
- Best for I/O-bound tasks (network requests, file operations)
- All tasks start simultaneously, consuming resources immediately

Optimization tips:
1. Group tasks appropriately:
```python
# Less efficient (too granular)
results = await parallel(
    task1(item1),
    task1(item2),
    task1(item3),
    task2(item1),
    task2(item2),
    task2(item3)
)

# More efficient (better grouping)
group1 = await parallel(
    task1(item1),
    task1(item2),
    task1(item3)
)
group2 = await parallel(
    task2(item1),
    task2(item2),
    task2(item3)
)
```

2. Consider resource constraints:
```python
# Resource-intensive tasks should be grouped appropriately
results = await parallel(
    lambda: heavy_task1(),  # Uses significant memory
    lambda: light_task(),   # Minimal resource usage
    lambda: heavy_task2()   # Uses significant memory
)
```

### Pipeline Performance

Pipeline execution is sequential, making performance dependent on the slowest task.

```python
@workflow
async def pipeline_workflow(ctx: ExecutionContext):
    result = await pipeline(
        fast_task,      # 0.1s
        slow_task,      # 2.0s
        medium_task,    # 0.5s
        input=ctx.input
    )
    # Total time ≈ 2.6s
```

Optimization tips:
1. Order tasks efficiently:
   - Put quick validation tasks first
   - Group data transformation tasks
   - Place heavy processing tasks last

2. Balance task granularity:
```python
# Less efficient (too granular)
result = await pipeline(
    validate_input,
    transform_data,
    process_part1,
    process_part2,
    process_part3,
    save_result,
    input=ctx.input
)

# More efficient (better grouping)
result = await pipeline(
    validate_and_transform,  # Combined validation and transformation
    process_all_parts,      # Combined processing
    save_result,
    input=ctx.input
)
```

### Task Mapping Performance

Task mapping parallelizes the same operation across multiple inputs.

```python
@task
async def process_item(item: str):
    return item.upper()

@workflow
async def mapping_workflow(ctx: ExecutionContext):
    # Be mindful of the input size
    results = await process_item.map(large_input_list)
```

Key considerations:
- Built on top of asyncio.gather for concurrent execution
- Memory usage scales with input size
- All results are collected in memory

Optimization tips:
1. Batch processing for large datasets:
```python
@workflow
async def optimized_mapping(ctx: ExecutionContext):
    # Process in smaller batches
    batch_size = 1000
    results = []
    for i in range(0, len(ctx.input), batch_size):
        batch = ctx.input[i:i + batch_size]
        batch_results = await process_item.map(batch)
        results.extend(batch_results)
```

2. Memory-efficient processing:
```python
@workflow
async def memory_efficient_mapping(ctx: ExecutionContext):
    # Process and store results incrementally
    results = []
    for batch in chunk_generator(ctx.input, size=1000):
        batch_results = await process_item.map(batch)
        # Process or store results before next batch
        await store_results(batch_results)
```

### Graph Performance

Graph execution performance depends on task dependencies and conditions.

```python
@workflow
async def graph_workflow(ctx: ExecutionContext):
    workflow = (
        Graph("optimized_flow")
        .add_node("validate", quick_validation)
        .add_node("process", heavy_processing)
        .add_node("error", handle_error)
        .add_edge("validate", "process",
                 condition=lambda r: r.get("valid"))
        .add_edge("validate", "error",
                 condition=lambda r: not r.get("valid"))
        .start_with("validate")
        .end_with("process")
        .end_with("error")
    )
```

Optimization tips:
1. Optimize graph structure:
   - Place validation and lightweight tasks early
   - Group related tasks to minimize edge complexity
   - Use conditions to skip unnecessary tasks

2. Balance between complexity and performance:
```python
# Less efficient (too many edges)
graph = (
    Graph("complex")
    .add_node("A", task_a)
    .add_node("B", task_b)
    .add_node("C", task_c)
    .add_edge("A", "B")
    .add_edge("A", "C")
    .add_edge("B", "C")
)

# More efficient (simplified structure)
graph = (
    Graph("optimized")
    .add_node("A", task_a)
    .add_node("BC", combined_task_bc)
    .add_edge("A", "BC")
)
```

### General Performance Tips

1. **Resource Management**
   - Monitor memory usage in parallel operations
   - Use appropriate batch sizes for large datasets
   - Consider I/O vs CPU-bound task characteristics

2. **Task Granularity**
   - Balance between too fine and too coarse
   - Group related operations when possible
   - Split very large tasks into manageable pieces

3. **Error Handling**
   - Implement early validation to fail fast
   - Use appropriate timeouts
   - Consider the cost of retries and fallbacks

4. **State Management**
   - Be mindful of data size in context
   - Implement cleanup for temporary data
   - Use appropriate storage strategies for large results
