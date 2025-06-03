# Key Features

Flux provides a comprehensive set of features designed to handle real-world workflow challenges. Each feature is built with production reliability and developer productivity in mind.

> 🚀 **Ready to try these features?** Start with our [Your First Workflow Tutorial](../tutorials/your-first-workflow.md) to see them in action.

## High-Performance Task Execution

### Parallel Execution
Execute multiple independent tasks concurrently to maximize throughput and reduce total execution time.

```python
from flux import workflow, task, parallel

@task
def process_file(filename: str) -> dict:
    # Process individual file
    return {"file": filename, "status": "processed"}

@workflow
def batch_processing(files: list[str]):
    # Process all files in parallel
    results = parallel([process_file(f) for f in files])
    return {"processed_count": len(results), "results": results}
```

**Benefits:**
- Automatic thread/process management
- Optimal resource utilization
- Built-in error isolation between parallel tasks
- Configurable concurrency limits

### Task Mapping
Apply operations across collections of data efficiently with built-in parallelization and error handling.

```python
from flux import workflow, task, map_task

@task
def analyze_customer(customer_id: str) -> dict:
    # Analyze individual customer data
    return {"id": customer_id, "score": calculate_score(customer_id)}

@workflow
def customer_analysis(customer_ids: list[str]):
    # Map analysis across all customers
    analyses = map_task(analyze_customer, customer_ids, batch_size=10)
    return {"total_customers": len(analyses), "analyses": analyses}
```

**Features:**
- Configurable batch sizes for memory efficiency
- Progress tracking for long-running operations
- Partial failure handling with detailed error reporting
- Memory-efficient streaming for large datasets

### Pipeline Processing
Chain tasks together in efficient processing pipelines with automatic data flow management.

```python
from flux import workflow, task, pipeline

@task
def extract_data(source: str) -> dict:
    return {"raw_data": fetch_from_source(source)}

@task
def transform_data(data: dict) -> dict:
    return {"transformed": apply_transformations(data["raw_data"])}

@task
def load_data(data: dict) -> dict:
    destination_id = save_to_destination(data["transformed"])
    return {"destination_id": destination_id, "status": "loaded"}

@workflow
def etl_pipeline(source: str):
    # Create processing pipeline
    result = pipeline([
        extract_data(source),
        transform_data,
        load_data
    ])
    return result
```

### Graph-based Workflows
Create complex task dependencies using directed acyclic graphs for sophisticated workflow orchestration.

```python
from flux import workflow, task, graph

@workflow
def complex_data_pipeline():
    # Define task dependencies as a graph
    return graph({
        "fetch_users": fetch_users(),
        "fetch_orders": fetch_orders(),
        "join_data": join_user_orders(
            depends_on=["fetch_users", "fetch_orders"]
        ),
        "calculate_metrics": calculate_metrics(
            depends_on=["join_data"]
        ),
        "generate_report": generate_report(
            depends_on=["calculate_metrics"]
        )
    })
```

## Fault-Tolerance

### Automatic Retries
Configure retry attempts with customizable backoff strategies for transient failures.

```python
from flux import task, RetryConfig, BackoffStrategy

@task(retry=RetryConfig(
    max_attempts=3,
    backoff=BackoffStrategy.EXPONENTIAL,
    initial_delay=1.0,
    max_delay=60.0,
    jitter=True
))
def api_call_with_retries(endpoint: str) -> dict:
    response = requests.get(endpoint)
    response.raise_for_status()  # Will retry on HTTP errors
    return response.json()
```

**Retry Strategies:**
- **Exponential Backoff**: Delays increase exponentially with jitter
- **Linear Backoff**: Fixed delay increments
- **Fixed Delay**: Constant delay between attempts
- **Custom**: User-defined backoff functions

### Fallback Mechanisms
Define fallback behavior for failed tasks to ensure workflow continuity.

```python
from flux import task, FallbackConfig

@task(fallback=FallbackConfig(
    handler=use_cached_data,
    conditions=["ConnectionError", "TimeoutError"]
))
def fetch_live_data(api_key: str) -> dict:
    # Try to fetch live data
    return call_external_api(api_key)

def use_cached_data(context, error) -> dict:
    # Fallback to cached data when live data unavailable
    return {"data": load_from_cache(), "source": "cache"}
```

### Error Recovery
Roll back failed operations with custom recovery logic to maintain data consistency.

```python
from flux import task, RollbackConfig

@task(rollback=RollbackConfig(handler=cleanup_partial_upload))
def upload_files(files: list[str]) -> dict:
    uploaded = []
    try:
        for file in files:
            result = upload_file(file)
            uploaded.append(result)
        return {"uploaded": uploaded}
    except Exception:
        # Rollback will be called automatically
        raise

def cleanup_partial_upload(context, uploaded_files):
    # Clean up any partially uploaded files
    for file_info in uploaded_files:
        delete_uploaded_file(file_info["id"])
```

### Task Timeouts
Set execution time limits to prevent hanging tasks and ensure timely workflow completion.

```python
from flux import task, TimeoutConfig

@task(timeout=TimeoutConfig(
    duration=300.0,  # 5 minutes
    on_timeout=send_alert
))
def long_running_analysis(dataset: str) -> dict:
    # Analysis that should complete within 5 minutes
    return perform_complex_analysis(dataset)

def send_alert(context, task_info):
    # Send notification when task times out
    notify_administrators(f"Task {task_info.name} timed out")
```

## Durable Execution

### State Persistence
Maintain workflow state across executions, system restarts, and failures.

```python
from flux import workflow, task, checkpoint

@workflow
def long_running_workflow(dataset: str):
    # State automatically persisted at each step
    preprocessed = preprocess_data(dataset)

    # Create explicit checkpoint
    checkpoint("preprocessing_complete", {"data": preprocessed})

    analyzed = analyze_data(preprocessed)
    results = generate_report(analyzed)

    return results
```

**Persistence Features:**
- Automatic state snapshots after each task
- Custom checkpoint creation for critical milestones
- State compression for memory efficiency
- Configurable retention policies

### Resume Capability
Continue workflows from their last successful state after interruptions.

```python
from flux import workflow, resume_workflow

# Resume a workflow that was interrupted
execution_id = "workflow_12345"
result = resume_workflow(execution_id)
```

### Event Tracking
Monitor and log all workflow and task events for debugging and auditing.

```python
from flux import workflow, EventListener

class WorkflowAuditor(EventListener):
    def on_workflow_started(self, event):
        log.info(f"Workflow {event.workflow_name} started")

    def on_task_completed(self, event):
        log.info(f"Task {event.task_name} completed in {event.duration}s")

    def on_workflow_failed(self, event):
        alert.send(f"Workflow {event.workflow_name} failed: {event.error}")

@workflow(listeners=[WorkflowAuditor()])
def monitored_workflow():
    # All events automatically tracked
    return process_data()
```

## Workflow Controls

### Pause/Resume
Pause workflows at defined points and resume when ready, enabling human-in-the-loop processes.

```python
from flux import workflow, task, pause_point

@workflow
def approval_workflow(document: dict):
    # Process document
    processed = process_document(document)

    # Pause for human approval
    approval = pause_point(
        "document_review",
        data={"document": processed},
        timeout=86400  # 24 hours
    )

    if approval.get("approved"):
        return finalize_document(processed)
    else:
        return reject_document(processed, approval.get("reason"))
```

### State Inspection
Examine workflow state at any point during execution for debugging and monitoring.

```python
from flux import get_workflow_state, list_active_workflows

# Inspect running workflows
active_workflows = list_active_workflows()
for workflow_id in active_workflows:
    state = get_workflow_state(workflow_id)
    print(f"Workflow {workflow_id}: {state.current_task} ({state.progress}%)")
```

### Subworkflow Support
Compose complex workflows from simpler ones for better modularity and reusability.

```python
from flux import workflow, subworkflow

@workflow
def data_validation(data: dict) -> dict:
    # Validate data quality
    return validate_schema(data)

@workflow
def data_enrichment(data: dict) -> dict:
    # Add external data
    return enrich_with_external_data(data)

@workflow
def master_pipeline(input_data: dict):
    # Compose from subworkflows
    validated = subworkflow(data_validation, input_data)
    enriched = subworkflow(data_enrichment, validated)
    return process_final_data(enriched)
```

## Security

### Secret Management
Securely handle sensitive data during workflow execution with encrypted storage.

```python
from flux import task, get_secrets

@task
def secure_api_call(endpoint: str) -> dict:
    # Retrieve secrets securely
    secrets = get_secrets(["api_key", "api_secret"])

    headers = {
        "Authorization": f"Bearer {secrets['api_key']}",
        "X-API-Secret": secrets["api_secret"]
    }

    response = requests.get(endpoint, headers=headers)
    return response.json()
```

**Security Features:**
- AES-256 encryption for secrets at rest
- Secure secret injection into task execution context
- Automatic secret rotation support
- Audit logging for secret access

### Access Control
Manage who can execute and modify workflows with role-based permissions.

```python
from flux import workflow, require_permissions

@workflow
@require_permissions(["workflow.execute", "data.read"])
def sensitive_workflow(user_id: str):
    # Only users with proper permissions can execute
    return process_sensitive_data(user_id)
```

## API Integration

### HTTP API
Built-in FastAPI server provides RESTful endpoints for workflow management.

```bash
# Start server
flux start server

# List workflows
curl http://localhost:8000/workflows

# Execute workflow
curl -X POST http://localhost:8000/workflows/my_workflow/run \
  -H "Content-Type: application/json" \
  -d '{"input": "data"}'

# Check status
curl http://localhost:8000/workflows/my_workflow/status/exec_123
```

### Programmatic Access
Python API for direct integration into applications.

```python
from flux import FluxClient

# Connect to Flux server
client = FluxClient("http://localhost:8000")

# Execute workflow programmatically
execution = client.run_workflow("data_pipeline", {"source": "database"})

# Monitor execution
while not execution.is_complete():
    status = execution.get_status()
    print(f"Progress: {status.progress}%")
    time.sleep(5)

result = execution.get_result()
```

## Development Features

### Type Safety
Full type hinting support for better development experience and IDE integration.

```python
from flux import workflow, task
from typing import List, Dict, Optional

@task
def process_records(records: List[Dict[str, str]]) -> Dict[str, int]:
    # Full type checking and IDE support
    return {"processed": len(records)}

@workflow
def typed_workflow(input_data: Dict[str, List[str]]) -> Optional[Dict[str, int]]:
    if not input_data.get("records"):
        return None

    return process_records(input_data["records"])
```

### Testing Support
Comprehensive testing utilities for workflows and tasks.

```python
import pytest
from flux.testing import WorkflowTester, mock_task

@pytest.fixture
def workflow_tester():
    return WorkflowTester()

def test_data_pipeline(workflow_tester):
    # Mock external dependencies
    with mock_task("fetch_data", return_value={"data": "test"}):
        result = workflow_tester.run(data_pipeline, source="test")
        assert result["status"] == "success"

def test_error_handling(workflow_tester):
    # Test failure scenarios
    with mock_task("fetch_data", side_effect=ConnectionError()):
        result = workflow_tester.run(data_pipeline, source="test")
        assert result["status"] == "failed"
        assert "ConnectionError" in result["error"]
```

### Debugging Tools
Rich debugging information and state inspection for troubleshooting.

```python
from flux import workflow, debug_mode

@workflow
def debug_workflow():
    with debug_mode():
        # Detailed execution logging
        step1 = process_step_1()
        breakpoint()  # Standard Python debugging
        step2 = process_step_2(step1)
        return step2
```

### Local Development
Easy local development and testing workflow with hot reload and live monitoring.

```bash
# Start development environment
flux start server --dev
flux start worker --dev

# Watch for workflow changes
flux workflow watch my_workflows.py

# Test workflow locally
flux workflow run my_workflow '{"test": true}' --mode sync
```

## Performance and Scaling

### Horizontal Scaling
Scale workers horizontally to handle increased workload without code changes.

```bash
# Scale workers across multiple machines
flux start worker worker-01 --server-url http://flux-server:8000
flux start worker worker-02 --server-url http://flux-server:8000
flux start worker worker-03 --server-url http://flux-server:8000
```

### Resource Management
Efficient resource utilization with configurable limits and monitoring.

```python
@task(resources={"cpu": 2, "memory": "4GB", "gpu": 1})
def resource_intensive_task(data: dict) -> dict:
    # Task with specific resource requirements
    return process_with_gpu(data)
```

### Metrics and Monitoring
Built-in metrics collection and monitoring integration.

```python
from flux import workflow, metrics

@workflow
def monitored_workflow():
    with metrics.timer("data_processing"):
        result = process_data()

    metrics.counter("workflows_completed").increment()
    metrics.gauge("data_size").set(len(result))

    return result
```

## Next Steps

Now that you understand Flux's capabilities, explore these resources to start building:

### Learn by Example
- **[Your First Workflow Tutorial](../tutorials/your-first-workflow.md)** - Hands-on introduction using these features
- **[Working with Tasks Tutorial](../tutorials/working-with-tasks.md)** - Master the task patterns shown above
- **[Parallel Processing Tutorial](../tutorials/parallel-processing.md)** - Deep dive into performance optimization

### Setup and Core Concepts
- **[Getting Started](../getting-started/installation.md)** - Set up your development environment
- **[Basic Concepts](../getting-started/basic_concepts.md)** - Understand workflows, tasks, and execution
- **[Core Concepts: Task System](../core-concepts/tasks.md)** - Comprehensive task architecture

### Advanced Usage
- **[Use Cases](use-cases.md)** - See how these features apply to real-world scenarios
- **[Advanced Features: Task Patterns](../advanced-features/task-patterns.md)** - Master advanced patterns and techniques
- **[Best Practices](../tutorials/best-practices.md)** - Production deployment guidelines

### Reference
- **[CLI Commands](../cli/index.md)** - Command-line interface for running workflows
- **[Troubleshooting](../tutorials/troubleshooting.md)** - Common issues and solutions
