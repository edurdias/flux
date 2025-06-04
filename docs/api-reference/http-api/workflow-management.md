# Workflow Management API

The Flux HTTP API provides comprehensive endpoints for managing workflows, including uploading, listing, inspecting, and managing workflow metadata. All endpoints are accessible via REST API calls and return JSON responses.

## Base URL

All API endpoints are relative to your Flux server's base URL:
```
http://localhost:8080/api/v1
```

## Authentication

Currently, Flux uses basic authentication. Include your credentials in the Authorization header:
```
Authorization: Basic <base64-encoded-credentials>
```

## Upload Workflow

Upload a new workflow to the Flux server for execution.

### Endpoint
```
POST /workflows
```

### Request Body
```json
{
  "name": "my_workflow",
  "file_content": "from flux import task, workflow\n\n@task\nasync def hello():\n    return 'Hello'\n\n@workflow\nasync def my_workflow(ctx):\n    return await hello()"
}
```

### Response
```json
{
  "status": "success",
  "workflow_name": "my_workflow",
  "message": "Workflow uploaded successfully"
}
```

### Example
```bash
curl -X POST http://localhost:8080/api/v1/workflows \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic <credentials>" \
  -d '{
    "name": "data_pipeline",
    "file_content": "..."
  }'
```

## List Workflows

Retrieve a list of all uploaded workflows.

### Endpoint
```
GET /workflows
```

### Response
```json
{
  "workflows": [
    {
      "name": "data_pipeline",
      "uploaded_at": "2024-01-15T10:30:00Z",
      "file_size": 2048,
      "status": "active"
    },
    {
      "name": "user_onboarding",
      "uploaded_at": "2024-01-14T16:45:00Z",
      "file_size": 1536,
      "status": "active"
    }
  ]
}
```

### Example
```bash
curl -X GET http://localhost:8080/api/v1/workflows \
  -H "Authorization: Basic <credentials>"
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
  "name": "data_pipeline",
  "uploaded_at": "2024-01-15T10:30:00Z",
  "file_size": 2048,
  "status": "active",
  "description": "Pipeline for processing customer data",
  "tasks": [
    {
      "name": "validate_data",
      "type": "task",
      "dependencies": []
    },
    {
      "name": "transform_data",
      "type": "task",
      "dependencies": ["validate_data"]
    }
  ],
  "configuration": {
    "retries": 3,
    "timeout": 300
  }
}
```

### Example
```bash
curl -X GET http://localhost:8080/api/v1/workflows/data_pipeline \
  -H "Authorization: Basic <credentials>"
```

## Update Workflow

Update an existing workflow with new code or configuration.

### Endpoint
```
PUT /workflows/{workflow_name}
```

### Request Body
```json
{
  "file_content": "from flux import task, workflow\n\n@task\nasync def improved_hello():\n    return 'Hello, improved!'\n\n@workflow\nasync def my_workflow(ctx):\n    return await improved_hello()"
}
```

### Response
```json
{
  "status": "success",
  "workflow_name": "my_workflow",
  "message": "Workflow updated successfully",
  "version": 2
}
```

### Example
```bash
curl -X PUT http://localhost:8080/api/v1/workflows/data_pipeline \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic <credentials>" \
  -d '{
    "file_content": "..."
  }'
```

## Delete Workflow

Remove a workflow from the server.

### Endpoint
```
DELETE /workflows/{workflow_name}
```

### Response
```json
{
  "status": "success",
  "message": "Workflow deleted successfully"
}
```

### Example
```bash
curl -X DELETE http://localhost:8080/api/v1/workflows/old_pipeline \
  -H "Authorization: Basic <credentials>"
```

## Workflow Validation

Validate workflow syntax and dependencies before uploading.

### Endpoint
```
POST /workflows/validate
```

### Request Body
```json
{
  "file_content": "from flux import task, workflow\n\n@task\nasync def hello():\n    return 'Hello'\n\n@workflow\nasync def my_workflow(ctx):\n    return await hello()"
}
```

### Response
```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    "Task 'hello' has no type hints"
  ],
  "tasks_found": 1,
  "workflows_found": 1
}
```

### Error Response
```json
{
  "valid": false,
  "errors": [
    "SyntaxError: invalid syntax on line 5",
    "Undefined task 'missing_task' referenced in workflow"
  ],
  "warnings": [],
  "tasks_found": 0,
  "workflows_found": 0
}
```

## Error Handling

All endpoints return appropriate HTTP status codes and error messages:

### Status Codes
- `200` - Success
- `400` - Bad Request (invalid input)
- `401` - Unauthorized (authentication failed)
- `404` - Not Found (workflow doesn't exist)
- `409` - Conflict (workflow already exists)
- `500` - Internal Server Error

### Error Response Format
```json
{
  "status": "error",
  "error_code": "WORKFLOW_NOT_FOUND",
  "message": "Workflow 'nonexistent_workflow' not found",
  "details": {
    "workflow_name": "nonexistent_workflow",
    "available_workflows": ["data_pipeline", "user_onboarding"]
  }
}
```

## Rate Limiting

The API implements rate limiting to prevent abuse:
- **Workflow uploads**: 10 requests per minute
- **List operations**: 100 requests per minute
- **Detail operations**: 200 requests per minute

Rate limit headers are included in responses:
```
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 7
X-RateLimit-Reset: 1642248600
```

## Next Steps

- Learn about [Execution Control](execution-control.md) to run your uploaded workflows
- Explore [Status and Monitoring](status-monitoring.md) for tracking workflow execution
- Check out [Administration](administration.md) for server management features
