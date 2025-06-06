# Workflows

Workflows are the orchestration layer in Flux that combines multiple tasks to achieve complex business logic. They are Python functions decorated with `@workflow` that define the execution flow, coordination between tasks, and overall workflow behavior. Workflows provide state management, error handling, and sophisticated execution patterns like parallel processing, conditional execution, and pause/resume functionality.

## Workflow Definition

### Basic Workflow Creation

The simplest way to define a workflow is using the `@workflow` decorator:

```python
from flux import workflow, ExecutionContext, task

@task
async def process_data(data: str) -> str:
    """Process input data."""
    return data.upper()

@task
async def save_result(data: str) -> bool:
    """Save processed data."""
    print(f"Saved: {data}")
    return True

@workflow
async def data_pipeline(ctx: ExecutionContext[str]) -> dict:
    """A simple data processing pipeline."""
    # Access input data from execution context
    raw_data = ctx.input

    # Execute tasks in sequence
    processed_data = await process_data(raw_data)
    saved = await save_result(processed_data)

    return {"data": processed_data, "saved": saved}
```

### Workflow Requirements

Workflows must follow these guidelines:

- **Function signature**: Must be async functions that take `ExecutionContext` as the first parameter
- **Decorator**: Must use `@workflow` or `@workflow.with_options()` decorator
- **Type hints**: The `ExecutionContext` can be typed with the expected input type (e.g., `ExecutionContext[str]`)
- **Return values**: Can return any Python object
- **Task coordination**: Use `await` to call tasks and other workflows

```python
from typing import Dict, List, Optional

@workflow
async def user_processing_workflow(ctx: ExecutionContext[Dict[str, str]]) -> Optional[Dict[str, any]]:
    """Process user data with proper type hints."""
    user_data = ctx.input

    # Validate input
    if not user_data or "email" not in user_data:
        return None

    # Process user
    validated = await validate_user(user_data)
    enriched = await enrich_user_data(validated)
    saved = await save_user(enriched)

    return {"user": enriched, "saved": saved}
```

## Workflow Decoration

### Using @workflow.with_options

For advanced configuration, use `@workflow.with_options()` to specify workflow behavior:

```python
from flux.domain.resource_request import ResourceRequest
from flux.output_storage import OutputStorage

@workflow.with_options(
    name="advanced_data_pipeline",      # Custom workflow identifier
    secret_requests=["DATABASE_URL", "API_KEY"],  # Required secrets
    output_storage=custom_storage,      # Custom output storage
    requests=ResourceRequest(           # Resource requirements
        min_memory="512MB",
        min_cpu="1.0",
        packages=["pandas", "numpy"]
    )
)
async def advanced_workflow(ctx: ExecutionContext[str]) -> dict:
    """A workflow with comprehensive configuration."""
    # Access secrets through context
    secrets = ctx.secrets
    db_url = secrets["DATABASE_URL"]
    api_key = secrets["API_KEY"]

    # Workflow implementation with resource-intensive operations
    result = await process_large_dataset(ctx.input, db_url, api_key)
    return result
```

### Decorator Parameters

#### name (str, optional)
Custom workflow name used for identification in logs, events, and the catalog:

```python
@workflow.with_options(name="user_onboarding_v2")
async def onboard_user(ctx: ExecutionContext[dict]):
    """Onboard a new user with a specific workflow name."""
    return await process_user_onboarding(ctx.input)
```

#### secret_requests (list[str], default: [])
List of secret names that the workflow requires:

```python
@workflow.with_options(secret_requests=["EMAIL_API_KEY", "DATABASE_PASSWORD"])
async def secure_workflow(ctx: ExecutionContext[dict]):
    """Workflow that requires secure credentials."""
    # Secrets are automatically injected into the execution context
    email_key = ctx.secrets["EMAIL_API_KEY"]
    db_password = ctx.secrets["DATABASE_PASSWORD"]

    result = await send_secure_notification(ctx.input, email_key)
    await log_to_secure_database(result, db_password)
    return result
```

#### output_storage (OutputStorage, optional)
Custom storage implementation for workflow results:

```python
from flux.output_storage import OutputStorage

class S3OutputStorage(OutputStorage):
    def store(self, workflow_id: str, output: any) -> str:
        # Store output in S3 and return reference
        s3_key = f"workflow-outputs/{workflow_id}.json"
        upload_to_s3(s3_key, output)
        return s3_key

    def retrieve(self, reference: str) -> any:
        # Retrieve output from S3
        return download_from_s3(reference)

@workflow.with_options(output_storage=S3OutputStorage())
async def large_output_workflow(ctx: ExecutionContext[str]):
    """Workflow that generates large results stored in S3."""
    large_dataset = await generate_large_dataset(ctx.input)
    return large_dataset
```

#### requests (ResourceRequest, optional)
Specify minimum resource requirements for workflow execution:

```python
from flux.domain.resource_request import ResourceRequest

@workflow.with_options(
    requests=ResourceRequest(
        min_memory="2GB",
        min_cpu="2.0",
        packages=["tensorflow", "pandas", "scikit-learn"]
    )
)
async def ml_workflow(ctx: ExecutionContext[dict]):
    """Machine learning workflow with specific resource requirements."""
    import tensorflow as tf
    import pandas as pd

    # Resource-intensive ML operations
    model = await train_model(ctx.input)
    predictions = await generate_predictions(model)
    return predictions
```

## Workflow Patterns

### Sequential Execution

The default pattern where tasks execute one after another:

```python
@workflow
async def sequential_pipeline(ctx: ExecutionContext[str]) -> dict:
    """Execute tasks in sequence."""
    # Each task waits for the previous one to complete
    step1 = await fetch_data(ctx.input)
    step2 = await validate_data(step1)
    step3 = await process_data(step2)
    step4 = await save_results(step3)

    return {"result": step4, "stages_completed": 4}
```

### Parallel Execution

Execute multiple tasks concurrently using the `parallel()` built-in task:

```python
from flux.tasks import parallel

@workflow
async def parallel_processing(ctx: ExecutionContext[list]) -> dict:
    """Process multiple items in parallel."""
    items = ctx.input

    # Process multiple items simultaneously
    results = await parallel(
        process_item(items[0]),
        process_item(items[1]),
        process_item(items[2]),
        validate_item(items[0]),
        validate_item(items[1])
    )

    return {"processed": results[:3], "validated": results[3:]}

@workflow
async def parallel_data_gathering(ctx: ExecutionContext[dict]) -> dict:
    """Gather data from multiple sources in parallel."""
    user_id = ctx.input["user_id"]

    # Fetch different types of data simultaneously
    user_data, preferences, history, recommendations = await parallel(
        fetch_user_profile(user_id),
        fetch_user_preferences(user_id),
        fetch_user_history(user_id),
        generate_recommendations(user_id)
    )

    return {
        "profile": user_data,
        "preferences": preferences,
        "history": history,
        "recommendations": recommendations
    }
```

### Pipeline Processing

Chain tasks where each receives the previous task's output:

```python
from flux.tasks import pipeline

@workflow
async def data_transformation_pipeline(ctx: ExecutionContext[str]) -> str:
    """Transform data through a series of processing steps."""
    # Each function receives the output of the previous one
    result = await pipeline(
        extract_data,
        clean_data,
        normalize_data,
        enrich_data,
        format_output,
        input=ctx.input
    )

    return result
```

### Conditional Workflows

Execute different paths based on runtime conditions:

```python
@workflow
async def conditional_processing(ctx: ExecutionContext[dict]) -> dict:
    """Execute different logic based on input conditions."""
    data = ctx.input

    # Determine processing path based on data characteristics
    if data.get("priority") == "high":
        result = await high_priority_processing(data)
    elif data.get("type") == "bulk":
        result = await bulk_processing(data)
    else:
        result = await standard_processing(data)

    # Common post-processing
    audit_result = await log_processing_result(result)

    return {"result": result, "audit": audit_result}
```

### Dynamic Workflows

Modify workflow behavior based on runtime data:

```python
@workflow
async def dynamic_approval_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Dynamic approval process based on request amount."""
    request = ctx.input
    amount = request.get("amount", 0)

    # Basic validation
    validated = await validate_request(request)

    # Dynamic approval chain based on amount
    if amount < 1000:
        # Auto-approve small amounts
        approved = await auto_approve(validated)
    elif amount < 10000:
        # Single manager approval
        approved = await manager_approval(validated)
    else:
        # Multi-level approval for large amounts
        manager_approved = await manager_approval(validated)
        executive_approved = await executive_approval(manager_approved)
        approved = executive_approved

    # Final processing
    result = await process_approved_request(approved)
    return result
```

### Task Mapping

Apply a task across multiple inputs in parallel:

```python
@task
async def process_user(user_data: dict) -> dict:
    """Process a single user."""
    validated = await validate_user_data(user_data)
    enriched = await enrich_user_profile(validated)
    return enriched

@workflow
async def batch_user_processing(ctx: ExecutionContext[list]) -> dict:
    """Process multiple users in parallel using task mapping."""
    users = ctx.input

    # Process all users in parallel
    processed_users = await process_user.map(users)

    # Aggregate results
    total_processed = len(processed_users)
    successful = sum(1 for user in processed_users if user.get("status") == "success")

    return {
        "total": total_processed,
        "successful": successful,
        "failed": total_processed - successful,
        "users": processed_users
    }
```

## Advanced Workflow Features

### Subworkflows

Compose complex workflows from simpler, reusable components:

```python
from flux.tasks import call

@workflow
async def user_validation_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Validate user data."""
    user = ctx.input
    email_valid = await validate_email(user["email"])
    phone_valid = await validate_phone(user["phone"])
    return {"valid": email_valid and phone_valid, "user": user}

@workflow
async def user_enrichment_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Enrich user data."""
    user = ctx.input
    location = await get_location_data(user["address"])
    preferences = await infer_preferences(user)
    return {**user, "location": location, "preferences": preferences}

@workflow
async def complete_user_onboarding(ctx: ExecutionContext[dict]) -> dict:
    """Complete user onboarding using subworkflows."""
    user_data = ctx.input

    # Call validation subworkflow
    validation_result = await call(user_validation_workflow, user_data)

    if not validation_result["valid"]:
        return {"status": "failed", "reason": "validation_failed"}

    # Call enrichment subworkflow
    enriched_user = await call(user_enrichment_workflow, validation_result["user"])

    # Final steps
    saved = await save_user_to_database(enriched_user)
    welcome_sent = await send_welcome_email(enriched_user["email"])

    return {
        "status": "completed",
        "user_id": saved["id"],
        "email_sent": welcome_sent
    }
```

### Workflow with Pause Points

Add manual intervention points in workflows:

```python
from flux.tasks import pause

@workflow
async def approval_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow with manual approval step."""
    request = ctx.input

    # Automated processing
    validated = await validate_request(request)
    risk_assessment = await assess_risk(validated)

    # Pause for manual review if high risk
    if risk_assessment["risk_level"] == "high":
        await pause("manual_review_required")

    # Continue after manual approval
    processed = await process_approved_request(validated)
    notification_sent = await send_approval_notification(processed)

    return {
        "processed": processed,
        "notification_sent": notification_sent,
        "risk_level": risk_assessment["risk_level"]
    }

# First execution - runs until pause point
ctx = approval_workflow.run(high_risk_request)
print(ctx.is_paused)  # True

# Resume execution after manual review
ctx = approval_workflow.run(execution_id=ctx.execution_id)
print(ctx.has_finished)  # True
```

### Graph-based Workflows

Create complex task dependencies using directed acyclic graphs:

```python
from flux.tasks import Graph

@workflow
async def graph_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow with complex task dependencies."""
    data = ctx.input

    # Define workflow as a graph
    workflow_graph = (
        Graph("data_processing_flow")
        .add_node("validate", validate_input)
        .add_node("process_a", process_type_a)
        .add_node("process_b", process_type_b)
        .add_node("merge", merge_results)
        .add_node("finalize", finalize_output)
        .add_node("handle_error", handle_validation_error)

        # Define conditional edges
        .add_edge("validate", "process_a",
                 condition=lambda r: r.get("type") == "A")
        .add_edge("validate", "process_b",
                 condition=lambda r: r.get("type") == "B")
        .add_edge("validate", "handle_error",
                 condition=lambda r: not r.get("valid"))
        .add_edge("process_a", "merge")
        .add_edge("process_b", "merge")
        .add_edge("merge", "finalize")

        .start_with("validate")
        .end_with("finalize")
        .end_with("handle_error")
    )

    return await workflow_graph(data)
```

## Workflow State Management

### Execution Context

The execution context provides access to workflow state and metadata:

```python
@workflow
async def stateful_workflow(ctx: ExecutionContext[dict]) -> dict:
    """Workflow that uses execution context for state management."""
    # Access input data
    input_data = ctx.input

    # Check execution state
    if ctx.is_resumed:
        print("Workflow is being resumed")

    # Access workflow metadata
    print(f"Workflow ID: {ctx.workflow_id}")
    print(f"Execution ID: {ctx.execution_id}")

    # Process data
    result = await process_with_context(input_data, ctx)

    return result
```

### Error Handling in Workflows

Workflows can handle errors at the workflow level:

```python
@workflow
async def error_resilient_workflow(ctx: ExecutionContext[str]) -> dict:
    """Workflow with comprehensive error handling."""
    try:
        # Main workflow logic
        step1 = await risky_operation(ctx.input)
        step2 = await another_risky_operation(step1)
        return {"status": "success", "result": step2}

    except ValidationError as e:
        # Handle specific error types
        fallback_result = await validation_fallback(ctx.input)
        return {"status": "fallback", "result": fallback_result}

    except Exception as e:
        # Handle unexpected errors
        error_logged = await log_error(str(e))
        return {"status": "error", "message": str(e), "logged": error_logged}
```

## Running Workflows

### Local Execution

Execute workflows directly in Python:

```python
# Simple execution
result = my_workflow.run("input_data")
print(result.output)

# Resume paused workflow
result = my_workflow.run(execution_id="existing_execution_id")

# Check execution status
if result.has_succeeded:
    print(f"Success: {result.output}")
elif result.has_failed:
    print(f"Failed: {result.output}")
elif result.is_paused:
    print("Workflow is paused, waiting for manual intervention")
```

### Distributed Execution

Register and run workflows using the Flux server:

```bash
# Start server and worker
flux start server &
flux start worker &

# Register workflow
flux workflow register my_workflows.py

# Execute workflow
flux workflow run my_workflow '"input_data"' --mode sync
```

### HTTP API Execution

Execute workflows via HTTP requests:

```bash
# Synchronous execution
curl -X POST 'http://localhost:8000/workflows/my_workflow/run/sync' \
     -H 'Content-Type: application/json' \
     -d '"input_data"'

# Asynchronous execution
curl -X POST 'http://localhost:8000/workflows/my_workflow/run/async' \
     -H 'Content-Type: application/json' \
     -d '"input_data"'
```

## Best Practices

### Workflow Design Guidelines

1. **Single Responsibility**: Each workflow should have a clear, single purpose
2. **Composability**: Design workflows to be reusable and composable
3. **Error Resilience**: Plan for failure scenarios and implement appropriate handling
4. **State Management**: Use execution context effectively for state tracking
5. **Documentation**: Include clear docstrings explaining the workflow's purpose and behavior

### Configuration Recommendations

1. **Resource Requirements**: Specify resource requirements for predictable execution
2. **Secret Management**: Always use secret management for sensitive data
3. **Output Storage**: Use custom storage for large workflow outputs
4. **Naming**: Use descriptive names for workflow identification
5. **Type Hints**: Use type hints for better development experience and validation

### Performance Considerations

1. **Parallel Execution**: Use parallel execution for independent operations
2. **Task Granularity**: Balance between too fine-grained and too coarse-grained tasks
3. **Resource Usage**: Consider memory and CPU usage for large-scale operations
4. **I/O Operations**: Use async/await properly for I/O-bound operations
5. **State Persistence**: Minimize state size for better performance

## Example: Complete Workflow Configuration

Here's a comprehensive example showing various workflow configuration options:

```python
from flux import workflow, ExecutionContext, task
from flux.tasks import parallel, pause, call
from flux.domain.resource_request import ResourceRequest
from flux.output_storage import OutputStorage
from typing import Dict, List, Any

# Custom output storage
class DatabaseStorage(OutputStorage):
    def store(self, workflow_id: str, output: any) -> str:
        record_id = save_to_database(workflow_id, output)
        return record_id

    def retrieve(self, reference: str) -> any:
        return load_from_database(reference)

@workflow.with_options(
    name="enterprise_data_pipeline_v2",
    secret_requests=["DATABASE_URL", "API_KEY", "EMAIL_CREDENTIALS"],
    output_storage=DatabaseStorage(),
    requests=ResourceRequest(
        min_memory="4GB",
        min_cpu="2.0",
        packages=["pandas", "requests", "sqlalchemy"]
    )
)
async def enterprise_data_pipeline(ctx: ExecutionContext[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Enterprise-grade data processing pipeline with comprehensive configuration.

    Args:
        ctx: Execution context containing configuration and input data

    Returns:
        Dictionary containing processing results and metadata
    """
    config = ctx.input
    batch_id = config["batch_id"]
    data_sources = config["data_sources"]

    print(f"Starting enterprise pipeline for batch {batch_id}")

    # Parallel data extraction from multiple sources
    raw_datasets = await parallel(
        extract_data(source, ctx.secrets["DATABASE_URL"])
        for source in data_sources
    )

    # Sequential processing with validation checkpoints
    validated_data = await validate_data_quality(raw_datasets)

    # Pause for manual data quality review if issues found
    if validated_data["quality_score"] < 0.8:
        await pause("data_quality_review")

    # Continue with data transformation
    transformed_data = await transform_enterprise_data(validated_data)

    # Parallel enrichment and analysis
    enriched_data, analysis_results = await parallel(
        enrich_with_external_apis(transformed_data, ctx.secrets["API_KEY"]),
        perform_statistical_analysis(transformed_data)
    )

    # Final steps
    saved_results = await save_to_data_warehouse(
        enriched_data,
        ctx.secrets["DATABASE_URL"]
    )

    notifications_sent = await send_completion_notifications(
        batch_id,
        analysis_results,
        ctx.secrets["EMAIL_CREDENTIALS"]
    )

    return {
        "batch_id": batch_id,
        "status": "completed",
        "records_processed": len(enriched_data),
        "quality_score": validated_data["quality_score"],
        "analysis": analysis_results,
        "saved": saved_results,
        "notifications_sent": notifications_sent,
        "execution_time": ctx.duration
    }

# Usage examples
if __name__ == "__main__":
    # Local execution
    config = {
        "batch_id": "BATCH_2024_001",
        "data_sources": ["customers", "orders", "products"]
    }

    result = enterprise_data_pipeline.run(config)

    if result.has_succeeded:
        print(f"Pipeline completed: {result.output}")
    elif result.is_paused:
        print("Pipeline paused for manual review")
        # Resume after review
        result = enterprise_data_pipeline.run(execution_id=result.execution_id)
    elif result.has_failed:
        print(f"Pipeline failed: {result.output}")
```

This example demonstrates a production-ready workflow with enterprise features including comprehensive error handling, security considerations, performance optimizations, pause points for manual intervention, and proper documentation.
