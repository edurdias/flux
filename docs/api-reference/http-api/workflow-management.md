# Workflow Management API

The Flux HTTP API provides comprehensive endpoints for managing workflows and their execution, including uploading workflow files, listing available workflows, retrieving workflow details, executing workflows, and monitoring execution status. All endpoints are accessible via REST API calls and return JSON responses.

## Base URL

All API endpoints are relative to your Flux server's base URL:
```
http://localhost:8000
```

## Authentication

Flux uses Bearer token authentication for workers and admin operations. Include your token in the Authorization header:
```
Authorization: Bearer <your-token>
```

Note: Some endpoints require specific authentication depending on the operation:

- Worker operations require worker session tokens
- Admin operations may require additional permissions
- Public endpoints (like listing workflows) may not require authentication

## Upload Workflow

Upload a new workflow file to the Flux server for execution.

### Endpoint
```
POST /workflows
```

### Request Body
This endpoint expects a file upload, not JSON. Use multipart/form-data to upload a Python workflow file.

### Response
```json
{
  "status": "success",
  "workflows": [
    {
      "name": "my_workflow",
      "version": 1
    }
  ]
}
```

### Example
```bash
curl -X POST http://localhost:8000/workflows \
  -F "file=@my_workflow.py"
```

Example workflow file (`my_workflow.py`):
```python
from flux import task, workflow

@task
async def hello():
    return 'Hello, World!'

@workflow
async def my_workflow(ctx):
    result = await hello()
    return result
```

## List Workflows

Retrieve a list of all uploaded workflows.

### Endpoint
```
GET /workflows
```

### Response
```json
[
  {
    "name": "data_pipeline",
    "version": 1
  },
  {
    "name": "user_onboarding",
    "version": 2
  }
]
```

### Example
```bash
curl -X GET http://localhost:8000/workflows
```

## Get Workflow Details

Retrieve detailed information about a specific workflow.

### Endpoint
```
GET /workflows/{workflow_name}
```

### Parameters
- `workflow_name` (string): Name of the workflow to inspect

### Response
```json
{
  "id": "workflow-uuid-here",
  "name": "data_pipeline",
  "version": 1,
  "imports": ["flux", "pandas", "requests"],
  "source": "ZnJvbSBmbHV4IGltcG9ydC4uLg==",
  "requests": {
    "cpu": "2.0",
    "memory": "4GB",
    "packages": ["pandas", "requests"]
  }
}
```

### Example
```bash
curl -X GET http://localhost:8000/workflows/data_pipeline
```

## Execute Workflow

Execute a workflow with input data in different modes.

### Endpoint
```
POST /workflows/{workflow_name}/run/{mode}
```

### Parameters
- `workflow_name` (string): Name of the workflow to execute
- `mode` (string): Execution mode - `async`, `sync`, or `stream`

### Query Parameters
- `detailed` (boolean): Return detailed execution information (default: false)

### Request Body
```json
{
  "input_data": "any valid JSON data",
  "parameters": {
    "key": "value"
  }
}
```

### Response

#### Async Mode
```json
{
  "execution_id": "exec-uuid-here",
  "workflow_name": "data_pipeline",
  "state": "scheduled",
  "created_at": "2024-01-15T10:30:00Z"
}
```

#### Sync Mode
```json
{
  "execution_id": "exec-uuid-here",
  "workflow_name": "data_pipeline",
  "state": "completed",
  "output": {
    "result": "workflow output data"
  },
  "created_at": "2024-01-15T10:30:00Z",
  "completed_at": "2024-01-15T10:31:15Z"
}
```

#### Stream Mode
Returns Server-Sent Events (text/event-stream) with real-time execution updates.

### Examples
```bash
# Execute asynchronously
curl -X POST http://localhost:8000/workflows/data_pipeline/run/async \
  -H "Content-Type: application/json" \
  -d '{"input": "test data"}'

# Execute synchronously (waits for completion)
curl -X POST http://localhost:8000/workflows/data_pipeline/run/sync \
  -H "Content-Type: application/json" \
  -d '{"input": "test data"}'

# Execute with streaming updates
curl -X POST http://localhost:8000/workflows/data_pipeline/run/stream \
  -H "Content-Type: application/json" \
  -d '{"input": "test data"}'
```

## Get Execution Status

Check the status of a running or completed workflow execution.

### Endpoint
```
GET /workflows/{workflow_name}/status/{execution_id}
```

### Parameters
- `workflow_name` (string): Name of the workflow
- `execution_id` (string): ID of the execution to check

### Query Parameters
- `detailed` (boolean): Return detailed execution information (default: false)

### Response
```json
{
  "execution_id": "exec-uuid-here",
  "workflow_name": "data_pipeline",
  "state": "running",
  "progress": {
    "current_task": "validate_data",
    "completed_tasks": 2,
    "total_tasks": 5
  },
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:45Z"
}
```

### Example
```bash
curl -X GET http://localhost:8000/workflows/data_pipeline/status/exec-uuid-here
```

## Cancel Execution

Cancel a running workflow execution.

### Endpoint
```
GET /workflows/{workflow_name}/cancel/{execution_id}
```

### Parameters
- `workflow_name` (string): Name of the workflow
- `execution_id` (string): ID of the execution to cancel

### Query Parameters
- `mode` (string): Cancellation mode - `async` or `sync` (default: async)
- `detailed` (boolean): Return detailed execution information (default: false)

### Response
```json
{
  "execution_id": "exec-uuid-here",
  "workflow_name": "data_pipeline",
  "state": "cancelled",
  "message": "Execution cancelled successfully",
  "cancelled_at": "2024-01-15T10:32:00Z"
}
```

### Example
```bash
# Cancel asynchronously
curl -X GET http://localhost:8000/workflows/data_pipeline/cancel/exec-uuid-here

# Cancel synchronously (wait for cancellation to complete)
curl -X GET http://localhost:8000/workflows/data_pipeline/cancel/exec-uuid-here?mode=sync
```

## Error Handling

All endpoints return appropriate HTTP status codes and error messages:

### Status Codes
- `200` - Success
- `400` - Bad Request (invalid input, invalid execution mode, etc.)
- `404` - Not Found (workflow or execution doesn't exist)
- `500` - Internal Server Error

### Error Response Format
```json
{
  "detail": "Workflow 'nonexistent_workflow' not found"
}
```

### Common Errors

#### Workflow Not Found
```json
{
  "detail": "Workflow 'missing_workflow' not found"
}
```

#### Invalid Execution Mode
```json
{
  "detail": "Invalid mode. Use 'sync', 'async', or 'stream'."
}
```

#### Execution Not Found
```json
{
  "detail": "Execution context not found."
}
```

#### Cannot Cancel Finished Execution
```json
{
  "detail": "Cannot cancel a finished execution."
}
```

## Rate Limiting

The API implements rate limiting to prevent abuse:
- **Workflow uploads**: Limited by server configuration
- **Execution requests**: Limited by server capacity
- **Status checks**: Generally unrestricted for monitoring

Rate limit information may be included in response headers when limits are applied.

## Next Steps

- Learn about [Execution Control](execution-control.md) for advanced execution patterns
- Explore [Status and Monitoring](status-monitoring.md) for tracking workflow execution in detail
- Check out [Administration](administration.md) for server management and secrets
