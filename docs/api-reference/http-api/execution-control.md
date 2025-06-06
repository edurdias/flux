# Execution Control API

The Execution Control API provides endpoints for running workflows in different modes: synchronous execution (blocking until completion), asynchronous execution (non-blocking with status polling), and streaming execution (real-time event streaming).

## Base URL

All execution endpoints are relative to:
```
http://localhost:8080/api/v1/execute
```

## Synchronous Execution

Execute a workflow and wait for completion before returning results.

### Endpoint
```
POST /execute/sync/{workflow_name}
```

### Request Body
```json
{
  "input": "workflow_input_data",
  "options": {
    "timeout": 300,
    "detailed": true
  }
}
```

### Parameters
- `workflow_name` (string): Name of the workflow to execute
- `input` (any): Input data for the workflow
- `timeout` (integer, optional): Maximum execution time in seconds (default: 300)
- `detailed` (boolean, optional): Include detailed execution information (default: false)

### Response (Success)
```json
{
  "status": "completed",
  "execution_id": "exec_123456789",
  "output": "workflow_result_data",
  "execution_time": 45.2,
  "started_at": "2024-01-15T10:30:00Z",
  "completed_at": "2024-01-15T10:30:45Z",
  "details": {
    "tasks_executed": 5,
    "tasks_succeeded": 5,
    "tasks_failed": 0,
    "retry_count": 0
  }
}
```

### Response (Timeout)
```json
{
  "status": "timeout",
  "execution_id": "exec_123456789",
  "message": "Execution exceeded timeout of 300 seconds",
  "partial_output": "intermediate_result",
  "execution_time": 300.0
}
```

### Example
```bash
curl -X POST http://localhost:8080/api/v1/execute/sync/data_pipeline \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic <credentials>" \
  -d '{
    "input": {"user_id": 12345, "action": "process"},
    "options": {"timeout": 600, "detailed": true}
  }'
```

## Asynchronous Execution

Start workflow execution and return immediately with an execution ID for status polling.

### Endpoint
```
POST /execute/async/{workflow_name}
```

### Request Body
```json
{
  "input": "workflow_input_data",
  "options": {
    "priority": "normal",
    "tags": ["batch-job", "customer-data"]
  }
}
```

### Parameters
- `workflow_name` (string): Name of the workflow to execute
- `input` (any): Input data for the workflow
- `priority` (string, optional): Execution priority - "low", "normal", "high" (default: "normal")
- `tags` (array, optional): Labels for organizing and filtering executions

### Response
```json
{
  "status": "started",
  "execution_id": "exec_987654321",
  "message": "Workflow execution started",
  "started_at": "2024-01-15T10:30:00Z",
  "estimated_duration": 120,
  "status_url": "/api/v1/status/exec_987654321"
}
```

### Example
```bash
curl -X POST http://localhost:8080/api/v1/execute/async/user_onboarding \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic <credentials>" \
  -d '{
    "input": {"email": "user@example.com", "plan": "premium"},
    "options": {"priority": "high", "tags": ["onboarding", "premium"]}
  }'
```

## Streaming Execution

Execute a workflow with real-time event streaming for live monitoring.

### Endpoint
```
POST /execute/stream/{workflow_name}
```

### Request Body
```json
{
  "input": "workflow_input_data",
  "stream_options": {
    "include_logs": true,
    "include_state_changes": true,
    "buffer_size": 1000
  }
}
```

### Parameters
- `workflow_name` (string): Name of the workflow to execute
- `input` (any): Input data for the workflow
- `include_logs` (boolean, optional): Stream log messages (default: true)
- `include_state_changes` (boolean, optional): Stream state change events (default: true)
- `buffer_size` (integer, optional): Event buffer size (default: 1000)

### Response (Server-Sent Events)
The response is a Server-Sent Events (SSE) stream:

```
Content-Type: text/event-stream

event: execution_started
data: {"execution_id": "exec_555444333", "started_at": "2024-01-15T10:30:00Z"}

event: task_started
data: {"task_name": "validate_input", "started_at": "2024-01-15T10:30:01Z"}

event: task_completed
data: {"task_name": "validate_input", "completed_at": "2024-01-15T10:30:02Z", "output": "validation_passed"}

event: log
data: {"level": "INFO", "message": "Processing user data", "timestamp": "2024-01-15T10:30:03Z"}

event: execution_completed
data: {"execution_id": "exec_555444333", "status": "completed", "output": "final_result", "completed_at": "2024-01-15T10:30:15Z"}
```

### Example (JavaScript)
```javascript
const eventSource = new EventSource(
  'http://localhost:8080/api/v1/execute/stream/data_pipeline',
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Basic <credentials>'
    },
    body: JSON.stringify({
      input: {user_id: 12345},
      stream_options: {include_logs: true}
    })
  }
);

eventSource.addEventListener('execution_started', (event) => {
  const data = JSON.parse(event.data);
  console.log('Execution started:', data.execution_id);
});

eventSource.addEventListener('task_completed', (event) => {
  const data = JSON.parse(event.data);
  console.log('Task completed:', data.task_name);
});

eventSource.addEventListener('execution_completed', (event) => {
  const data = JSON.parse(event.data);
  console.log('Execution completed:', data.output);
  eventSource.close();
});
```

### Example (Python)
```python
import requests
import json

response = requests.post(
    'http://localhost:8080/api/v1/execute/stream/data_pipeline',
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Basic <credentials>'
    },
    json={
        'input': {'user_id': 12345},
        'stream_options': {'include_logs': True}
    },
    stream=True
)

for line in response.iter_lines():
    if line:
        line_str = line.decode('utf-8')
        if line_str.startswith('data: '):
            data = json.loads(line_str[6:])
            print(f"Event: {data}")
```

## Batch Execution

Execute multiple workflows in a single request.

### Endpoint
```
POST /execute/batch
```

### Request Body
```json
{
  "executions": [
    {
      "workflow_name": "data_pipeline",
      "input": {"user_id": 1},
      "options": {"priority": "high"}
    },
    {
      "workflow_name": "user_onboarding",
      "input": {"email": "user1@example.com"},
      "options": {"priority": "normal"}
    }
  ],
  "batch_options": {
    "max_concurrent": 5,
    "fail_fast": false
  }
}
```

### Response
```json
{
  "batch_id": "batch_789012345",
  "status": "started",
  "executions": [
    {
      "execution_id": "exec_111222333",
      "workflow_name": "data_pipeline",
      "status": "started"
    },
    {
      "execution_id": "exec_444555666",
      "workflow_name": "user_onboarding",
      "status": "started"
    }
  ],
  "status_url": "/api/v1/batch/batch_789012345/status"
}
```

## Execution Cancellation

Cancel a running workflow execution.

### Endpoint
```
POST /execute/{execution_id}/cancel
```

### Request Body
```json
{
  "reason": "User requested cancellation",
  "mode": "graceful"
}
```

### Parameters
- `execution_id` (string): ID of the execution to cancel
- `reason` (string, optional): Reason for cancellation
- `mode` (string, optional): Cancellation mode - "graceful" or "immediate" (default: "graceful")

### Response
```json
{
  "status": "cancelling",
  "execution_id": "exec_123456789",
  "message": "Cancellation request submitted",
  "mode": "graceful",
  "estimated_completion": "2024-01-15T10:35:00Z"
}
```

### Example
```bash
curl -X POST http://localhost:8080/api/v1/execute/exec_123456789/cancel \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic <credentials>" \
  -d '{
    "reason": "Resource constraints",
    "mode": "graceful"
  }'
```

## Execution Pause and Resume

Pause and resume workflow execution at defined pause points.

### Pause Execution
```
POST /execute/{execution_id}/pause
```

### Resume Execution
```
POST /execute/{execution_id}/resume
```

### Request Body (Resume)
```json
{
  "input_override": "new_input_data",
  "options": {
    "skip_to_task": "specific_task_name"
  }
}
```

### Response
```json
{
  "status": "resumed",
  "execution_id": "exec_123456789",
  "message": "Execution resumed successfully",
  "resumed_at": "2024-01-15T10:35:00Z"
}
```

## Error Handling

Execution endpoints return detailed error information:

### Workflow Not Found
```json
{
  "status": "error",
  "error_code": "WORKFLOW_NOT_FOUND",
  "message": "Workflow 'unknown_workflow' not found",
  "available_workflows": ["data_pipeline", "user_onboarding"]
}
```

### Invalid Input
```json
{
  "status": "error",
  "error_code": "INVALID_INPUT",
  "message": "Workflow input validation failed",
  "validation_errors": [
    {
      "field": "user_id",
      "error": "Required field missing"
    }
  ]
}
```

### Execution Failed
```json
{
  "status": "failed",
  "execution_id": "exec_123456789",
  "error": "Task 'process_data' failed after 3 retries",
  "failed_task": "process_data",
  "error_details": {
    "exception_type": "ValueError",
    "exception_message": "Invalid data format",
    "stack_trace": "..."
  }
}
```

## Rate Limiting

Execution endpoints have specific rate limits:
- **Synchronous execution**: 10 requests per minute
- **Asynchronous execution**: 100 requests per minute
- **Streaming execution**: 5 concurrent streams per user
- **Batch execution**: 5 requests per hour

## Next Steps

- Learn about [Status and Monitoring](status-monitoring.md) to track execution progress
- Explore [Workflow Management](workflow-management.md) to manage your workflows
- Check out [Administration](administration.md) for advanced server configuration
