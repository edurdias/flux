# Status and Monitoring API

The Status and Monitoring API provides comprehensive endpoints for tracking workflow execution, retrieving detailed run information, and streaming real-time events. These endpoints are essential for monitoring workflow health and debugging execution issues.

## Base URL

All status endpoints are relative to:
```
http://localhost:8080/api/v1/status
```

## Get Execution Status

Retrieve the current status of a workflow execution.

### Endpoint
```
GET /status/{execution_id}
```

### Parameters
- `execution_id` (string): Unique identifier for the execution
- `detailed` (boolean, optional): Include detailed task information (default: false)

### Response (Running)
```json
{
  "execution_id": "exec_123456789",
  "status": "running",
  "workflow_name": "data_pipeline",
  "started_at": "2024-01-15T10:30:00Z",
  "current_task": "process_data",
  "progress": {
    "total_tasks": 5,
    "completed_tasks": 2,
    "failed_tasks": 0,
    "percentage": 40
  },
  "estimated_completion": "2024-01-15T10:32:00Z",
  "input": {"user_id": 12345},
  "tags": ["batch-job", "customer-data"]
}
```

### Response (Completed)
```json
{
  "execution_id": "exec_123456789",
  "status": "completed",
  "workflow_name": "data_pipeline",
  "started_at": "2024-01-15T10:30:00Z",
  "completed_at": "2024-01-15T10:31:45Z",
  "execution_time": 105.2,
  "output": {"processed_records": 1500, "status": "success"},
  "progress": {
    "total_tasks": 5,
    "completed_tasks": 5,
    "failed_tasks": 0,
    "percentage": 100
  },
  "resource_usage": {
    "cpu_time": 45.3,
    "memory_peak": "512MB",
    "disk_io": "15.2MB"
  }
}
```

### Response (Failed)
```json
{
  "execution_id": "exec_123456789",
  "status": "failed",
  "workflow_name": "data_pipeline",
  "started_at": "2024-01-15T10:30:00Z",
  "failed_at": "2024-01-15T10:30:30Z",
  "execution_time": 30.5,
  "error": {
    "task_name": "validate_data",
    "error_type": "ValidationError",
    "error_message": "Invalid data format in field 'email'",
    "retry_count": 3,
    "stack_trace": "Traceback (most recent call last)..."
  },
  "progress": {
    "total_tasks": 5,
    "completed_tasks": 1,
    "failed_tasks": 1,
    "percentage": 20
  }
}
```

### Example
```bash
curl -X GET http://localhost:8080/api/v1/status/exec_123456789?detailed=true \
  -H "Authorization: Basic <credentials>"
```

## Get Detailed Execution Information

Retrieve comprehensive execution details including task-by-task breakdown.

### Endpoint
```
GET /status/{execution_id}/detailed
```

### Response
```json
{
  "execution_id": "exec_123456789",
  "status": "completed",
  "workflow_name": "data_pipeline",
  "started_at": "2024-01-15T10:30:00Z",
  "completed_at": "2024-01-15T10:31:45Z",
  "execution_time": 105.2,
  "input": {"user_id": 12345},
  "output": {"processed_records": 1500},
  "tasks": [
    {
      "task_name": "validate_data",
      "status": "completed",
      "started_at": "2024-01-15T10:30:00Z",
      "completed_at": "2024-01-15T10:30:15Z",
      "execution_time": 15.2,
      "input": {"user_id": 12345},
      "output": {"valid": true},
      "retry_count": 0,
      "worker_id": "worker_001"
    },
    {
      "task_name": "transform_data",
      "status": "completed",
      "started_at": "2024-01-15T10:30:15Z",
      "completed_at": "2024-01-15T10:30:45Z",
      "execution_time": 30.1,
      "input": {"user_id": 12345, "valid": true},
      "output": {"transformed_data": "..."},
      "retry_count": 0,
      "worker_id": "worker_002"
    }
  ],
  "events": [
    {
      "timestamp": "2024-01-15T10:30:00Z",
      "type": "execution_started",
      "details": {"workflow_name": "data_pipeline"}
    },
    {
      "timestamp": "2024-01-15T10:30:00Z",
      "type": "task_started",
      "details": {"task_name": "validate_data"}
    }
  ],
  "resource_usage": {
    "total_cpu_time": 45.3,
    "peak_memory": "512MB",
    "total_disk_io": "15.2MB",
    "network_calls": 3
  }
}
```

## List Executions

Retrieve a list of workflow executions with filtering and pagination.

### Endpoint
```
GET /status/executions
```

### Query Parameters
- `workflow_name` (string, optional): Filter by workflow name
- `status` (string, optional): Filter by status - "running", "completed", "failed", "cancelled"
- `start_date` (string, optional): Filter by start date (ISO 8601)
- `end_date` (string, optional): Filter by end date (ISO 8601)
- `tags` (string, optional): Comma-separated list of tags to filter by
- `page` (integer, optional): Page number for pagination (default: 1)
- `per_page` (integer, optional): Results per page (default: 50, max: 100)
- `sort` (string, optional): Sort field - "started_at", "completed_at", "execution_time" (default: "started_at")
- `order` (string, optional): Sort order - "asc", "desc" (default: "desc")

### Response
```json
{
  "executions": [
    {
      "execution_id": "exec_123456789",
      "workflow_name": "data_pipeline",
      "status": "completed",
      "started_at": "2024-01-15T10:30:00Z",
      "completed_at": "2024-01-15T10:31:45Z",
      "execution_time": 105.2,
      "tags": ["batch-job", "customer-data"]
    },
    {
      "execution_id": "exec_987654321",
      "workflow_name": "user_onboarding",
      "status": "running",
      "started_at": "2024-01-15T10:25:00Z",
      "current_task": "send_welcome_email",
      "progress": 60
    }
  ],
  "pagination": {
    "current_page": 1,
    "per_page": 50,
    "total_pages": 3,
    "total_executions": 127,
    "has_next": true,
    "has_previous": false
  },
  "filters_applied": {
    "workflow_name": "data_pipeline",
    "status": "completed",
    "date_range": "last_24_hours"
  }
}
```

### Example
```bash
curl -X GET "http://localhost:8080/api/v1/status/executions?workflow_name=data_pipeline&status=completed&page=1&per_page=20" \
  -H "Authorization: Basic <credentials>"
```

## Event Streaming

Subscribe to real-time execution events using Server-Sent Events (SSE).

### Endpoint
```
GET /status/{execution_id}/events
```

### Query Parameters
- `execution_id` (string): Execution to monitor
- `include_logs` (boolean, optional): Include log events (default: true)
- `include_metrics` (boolean, optional): Include metrics events (default: false)
- `from_timestamp` (string, optional): Start streaming from specific timestamp

### Response (Server-Sent Events)
```
Content-Type: text/event-stream

event: task_started
data: {"timestamp": "2024-01-15T10:30:15Z", "task_name": "validate_data", "worker_id": "worker_001"}

event: log
data: {"timestamp": "2024-01-15T10:30:16Z", "level": "INFO", "message": "Validating user data", "task_name": "validate_data"}

event: task_completed
data: {"timestamp": "2024-01-15T10:30:30Z", "task_name": "validate_data", "output": {"valid": true}, "execution_time": 15.2}

event: metrics
data: {"timestamp": "2024-01-15T10:30:30Z", "cpu_usage": 45.2, "memory_usage": "256MB", "task_name": "validate_data"}
```

### Example (JavaScript)
```javascript
const eventSource = new EventSource(
  'http://localhost:8080/api/v1/status/exec_123456789/events?include_logs=true',
  {
    headers: {
      'Authorization': 'Basic <credentials>'
    }
  }
);

eventSource.addEventListener('task_started', (event) => {
  const data = JSON.parse(event.data);
  console.log(`Task started: ${data.task_name}`);
});

eventSource.addEventListener('log', (event) => {
  const data = JSON.parse(event.data);
  console.log(`[${data.level}] ${data.message}`);
});
```

## Execution Logs

Retrieve execution logs with filtering and pagination.

### Endpoint
```
GET /status/{execution_id}/logs
```

### Query Parameters
- `level` (string, optional): Filter by log level - "DEBUG", "INFO", "WARNING", "ERROR"
- `task_name` (string, optional): Filter by specific task
- `start_time` (string, optional): Start time for log retrieval (ISO 8601)
- `end_time` (string, optional): End time for log retrieval (ISO 8601)
- `page` (integer, optional): Page number (default: 1)
- `per_page` (integer, optional): Logs per page (default: 100, max: 1000)

### Response
```json
{
  "logs": [
    {
      "timestamp": "2024-01-15T10:30:15Z",
      "level": "INFO",
      "message": "Starting data validation",
      "task_name": "validate_data",
      "worker_id": "worker_001",
      "execution_id": "exec_123456789"
    },
    {
      "timestamp": "2024-01-15T10:30:16Z",
      "level": "DEBUG",
      "message": "Checking email format for user 12345",
      "task_name": "validate_data",
      "worker_id": "worker_001",
      "execution_id": "exec_123456789"
    }
  ],
  "pagination": {
    "current_page": 1,
    "per_page": 100,
    "total_pages": 1,
    "total_logs": 45
  }
}
```

## Execution Metrics

Retrieve performance metrics for an execution.

### Endpoint
```
GET /status/{execution_id}/metrics
```

### Response
```json
{
  "execution_id": "exec_123456789",
  "metrics": {
    "execution_time": 105.2,
    "total_tasks": 5,
    "successful_tasks": 5,
    "failed_tasks": 0,
    "retried_tasks": 1,
    "parallel_tasks_peak": 3,
    "resource_usage": {
      "cpu_time_total": 45.3,
      "memory_peak": "512MB",
      "memory_average": "256MB",
      "disk_read": "5.2MB",
      "disk_write": "10.0MB",
      "network_requests": 8,
      "network_data": "2.1MB"
    },
    "task_breakdown": [
      {
        "task_name": "validate_data",
        "execution_time": 15.2,
        "cpu_time": 8.1,
        "memory_peak": "128MB",
        "retry_count": 0
      },
      {
        "task_name": "transform_data",
        "execution_time": 30.1,
        "cpu_time": 25.3,
        "memory_peak": "256MB",
        "retry_count": 1
      }
    ]
  },
  "timestamps": {
    "queued_at": "2024-01-15T10:29:58Z",
    "started_at": "2024-01-15T10:30:00Z",
    "first_task_at": "2024-01-15T10:30:00Z",
    "last_task_at": "2024-01-15T10:31:30Z",
    "completed_at": "2024-01-15T10:31:45Z"
  }
}
```

## Health Check

Check the overall health of the Flux monitoring system.

### Endpoint
```
GET /status/health
```

### Response
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:35:00Z",
  "version": "1.0.0",
  "components": {
    "database": {
      "status": "healthy",
      "response_time": 5.2
    },
    "worker_pool": {
      "status": "healthy",
      "active_workers": 5,
      "total_workers": 10
    },
    "event_streaming": {
      "status": "healthy",
      "active_connections": 23
    }
  },
  "system_metrics": {
    "active_executions": 15,
    "queued_executions": 3,
    "total_executions_today": 147,
    "average_execution_time": 85.6
  }
}
```

## Batch Status

Check the status of batch executions.

### Endpoint
```
GET /status/batch/{batch_id}
```

### Response
```json
{
  "batch_id": "batch_789012345",
  "status": "running",
  "started_at": "2024-01-15T10:30:00Z",
  "total_executions": 10,
  "completed_executions": 7,
  "failed_executions": 1,
  "running_executions": 2,
  "progress_percentage": 70,
  "executions": [
    {
      "execution_id": "exec_111222333",
      "workflow_name": "data_pipeline",
      "status": "completed",
      "execution_time": 45.2
    },
    {
      "execution_id": "exec_444555666",
      "workflow_name": "user_onboarding",
      "status": "running",
      "current_task": "send_email"
    }
  ]
}
```

## Error Responses

Status endpoints return appropriate error codes:

### Execution Not Found
```json
{
  "status": "error",
  "error_code": "EXECUTION_NOT_FOUND",
  "message": "Execution 'exec_unknown' not found",
  "suggestions": [
    "Check the execution ID for typos",
    "Verify the execution hasn't been cleaned up"
  ]
}
```

### Invalid Date Range
```json
{
  "status": "error",
  "error_code": "INVALID_DATE_RANGE",
  "message": "End date must be after start date",
  "provided_range": {
    "start_date": "2024-01-15T10:00:00Z",
    "end_date": "2024-01-14T10:00:00Z"
  }
}
```

## Rate Limiting

Status endpoints have the following rate limits:
- **Individual status checks**: 1000 requests per minute
- **List operations**: 100 requests per minute
- **Event streaming**: 10 concurrent streams per user
- **Log retrieval**: 50 requests per minute

## Next Steps

- Learn about [Execution Control](execution-control.md) to manage workflow execution
- Explore [Administration](administration.md) for server management features
- Check out [Workflow Management](workflow-management.md) for workflow operations
