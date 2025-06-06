# Administration API

The Administration API provides endpoints for managing server configuration, secrets, health monitoring, and system administration tasks. These endpoints are typically used by system administrators and DevOps teams.

## Base URL

All administration endpoints are relative to:
```
http://localhost:8080/api/v1/admin
```

## Authentication

Administration endpoints require elevated privileges. Use admin credentials:
```
Authorization: Basic <admin-credentials>
```

## Secrets Management

### List Secrets

Retrieve a list of all stored secrets (names only, not values).

#### Endpoint
```
GET /admin/secrets
```

#### Response
```json
{
  "secrets": [
    {
      "name": "database_password",
      "created_at": "2024-01-15T09:00:00Z",
      "updated_at": "2024-01-15T09:00:00Z",
      "description": "Database connection password"
    },
    {
      "name": "api_key_external_service",
      "created_at": "2024-01-14T14:30:00Z",
      "updated_at": "2024-01-15T08:45:00Z",
      "description": "API key for external data service"
    }
  ],
  "total_count": 2
}
```

### Create or Update Secret

Store a new secret or update an existing one.

#### Endpoint
```
PUT /admin/secrets/{secret_name}
```

#### Request Body
```json
{
  "value": "secret_value_here",
  "description": "Description of the secret",
  "metadata": {
    "environment": "production",
    "service": "database"
  }
}
```

#### Response
```json
{
  "status": "success",
  "message": "Secret 'database_password' updated successfully",
  "secret_name": "database_password",
  "created_at": "2024-01-15T10:30:00Z"
}
```

### Get Secret Value

Retrieve the value of a specific secret.

#### Endpoint
```
GET /admin/secrets/{secret_name}
```

#### Response
```json
{
  "name": "database_password",
  "value": "actual_secret_value",
  "description": "Database connection password",
  "created_at": "2024-01-15T09:00:00Z",
  "updated_at": "2024-01-15T09:00:00Z",
  "metadata": {
    "environment": "production",
    "service": "database"
  }
}
```

### Delete Secret

Remove a secret from storage.

#### Endpoint
```
DELETE /admin/secrets/{secret_name}
```

#### Response
```json
{
  "status": "success",
  "message": "Secret 'old_api_key' deleted successfully"
}
```

## Server Configuration

### Get Configuration

Retrieve current server configuration.

#### Endpoint
```
GET /admin/config
```

#### Response
```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080,
    "workers": 4,
    "max_connections": 1000
  },
  "database": {
    "url": "postgresql://localhost:5432/flux",
    "pool_size": 20,
    "max_overflow": 30
  },
  "execution": {
    "default_timeout": 300,
    "max_concurrent_executions": 100,
    "cleanup_retention_days": 30
  },
  "logging": {
    "level": "INFO",
    "format": "json",
    "file": "/var/log/flux/server.log"
  },
  "security": {
    "auth_enabled": true,
    "session_timeout": 3600,
    "rate_limiting_enabled": true
  }
}
```

### Update Configuration

Update server configuration (requires restart for some settings).

#### Endpoint
```
PATCH /admin/config
```

#### Request Body
```json
{
  "execution": {
    "default_timeout": 600,
    "max_concurrent_executions": 200
  },
  "logging": {
    "level": "DEBUG"
  }
}
```

#### Response
```json
{
  "status": "success",
  "message": "Configuration updated successfully",
  "changes_applied": [
    "execution.default_timeout: 300 -> 600",
    "execution.max_concurrent_executions: 100 -> 200",
    "logging.level: INFO -> DEBUG"
  ],
  "restart_required": false
}
```

## Worker Management

### List Workers

Retrieve information about all connected workers.

#### Endpoint
```
GET /admin/workers
```

#### Response
```json
{
  "workers": [
    {
      "worker_id": "worker_001",
      "status": "active",
      "connected_at": "2024-01-15T09:00:00Z",
      "last_heartbeat": "2024-01-15T10:35:00Z",
      "host": "worker-node-1.example.com",
      "capabilities": ["python", "data-processing"],
      "current_tasks": 3,
      "max_concurrent_tasks": 10,
      "total_tasks_completed": 1247,
      "resource_usage": {
        "cpu_percent": 45.2,
        "memory_percent": 67.8,
        "disk_usage": "15.2GB"
      }
    },
    {
      "worker_id": "worker_002",
      "status": "idle",
      "connected_at": "2024-01-15T09:15:00Z",
      "last_heartbeat": "2024-01-15T10:35:00Z",
      "host": "worker-node-2.example.com",
      "capabilities": ["python", "ml-processing"],
      "current_tasks": 0,
      "max_concurrent_tasks": 5,
      "total_tasks_completed": 892
    }
  ],
  "summary": {
    "total_workers": 2,
    "active_workers": 1,
    "idle_workers": 1,
    "offline_workers": 0,
    "total_capacity": 15,
    "used_capacity": 3
  }
}
```

### Get Worker Details

Retrieve detailed information about a specific worker.

#### Endpoint
```
GET /admin/workers/{worker_id}
```

#### Response
```json
{
  "worker_id": "worker_001",
  "status": "active",
  "connected_at": "2024-01-15T09:00:00Z",
  "last_heartbeat": "2024-01-15T10:35:00Z",
  "host": "worker-node-1.example.com",
  "port": 8081,
  "version": "1.0.0",
  "capabilities": ["python", "data-processing"],
  "configuration": {
    "max_concurrent_tasks": 10,
    "timeout_seconds": 3600,
    "temp_directory": "/tmp/flux-worker"
  },
  "current_tasks": [
    {
      "execution_id": "exec_123456789",
      "task_name": "process_data",
      "started_at": "2024-01-15T10:30:00Z",
      "estimated_completion": "2024-01-15T10:32:00Z"
    }
  ],
  "statistics": {
    "total_tasks_completed": 1247,
    "total_tasks_failed": 23,
    "average_task_duration": 45.6,
    "uptime_seconds": 5400
  },
  "resource_usage": {
    "cpu_percent": 45.2,
    "memory_used": "2.1GB",
    "memory_total": "8.0GB",
    "disk_used": "15.2GB",
    "disk_total": "100GB",
    "network_sent": "1.2GB",
    "network_received": "0.8GB"
  }
}
```

### Shutdown Worker

Gracefully shutdown a specific worker.

#### Endpoint
```
POST /admin/workers/{worker_id}/shutdown
```

#### Request Body
```json
{
  "mode": "graceful",
  "timeout": 300,
  "reason": "Maintenance window"
}
```

#### Response
```json
{
  "status": "shutting_down",
  "worker_id": "worker_001",
  "message": "Worker shutdown initiated",
  "mode": "graceful",
  "estimated_completion": "2024-01-15T10:40:00Z",
  "pending_tasks": 2
}
```

## Health Monitoring

### System Health

Get comprehensive system health information.

#### Endpoint
```
GET /admin/health
```

#### Response
```json
{
  "overall_status": "healthy",
  "timestamp": "2024-01-15T10:35:00Z",
  "uptime_seconds": 86400,
  "version": "1.0.0",
  "components": {
    "database": {
      "status": "healthy",
      "response_time_ms": 5.2,
      "connection_pool": {
        "active": 15,
        "idle": 5,
        "total": 20
      }
    },
    "worker_pool": {
      "status": "healthy",
      "active_workers": 5,
      "total_workers": 10,
      "pending_tasks": 12,
      "executing_tasks": 25
    },
    "storage": {
      "status": "healthy",
      "disk_usage": {
        "used": "45.2GB",
        "total": "500GB",
        "percent": 9.04
      }
    },
    "memory": {
      "status": "healthy",
      "used": "2.1GB",
      "total": "16GB",
      "percent": 13.1
    }
  },
  "metrics": {
    "requests_per_minute": 150,
    "average_response_time": 85.6,
    "error_rate_percent": 0.5,
    "active_executions": 25,
    "completed_executions_today": 1247
  }
}
```

### Performance Metrics

Get detailed performance metrics.

#### Endpoint
```
GET /admin/metrics
```

#### Query Parameters
- `timeframe` (string, optional): Time range - "1h", "24h", "7d", "30d" (default: "1h")
- `granularity` (string, optional): Data granularity - "1m", "5m", "1h", "1d" (default: "5m")

#### Response
```json
{
  "timeframe": "24h",
  "granularity": "1h",
  "metrics": {
    "execution_metrics": [
      {
        "timestamp": "2024-01-15T09:00:00Z",
        "executions_started": 45,
        "executions_completed": 42,
        "executions_failed": 2,
        "average_execution_time": 78.5
      },
      {
        "timestamp": "2024-01-15T10:00:00Z",
        "executions_started": 52,
        "executions_completed": 49,
        "executions_failed": 1,
        "average_execution_time": 82.1
      }
    ],
    "system_metrics": [
      {
        "timestamp": "2024-01-15T09:00:00Z",
        "cpu_percent": 45.2,
        "memory_percent": 67.8,
        "disk_io_mb": 15.2,
        "network_io_mb": 8.5
      }
    ],
    "worker_metrics": [
      {
        "timestamp": "2024-01-15T09:00:00Z",
        "active_workers": 8,
        "idle_workers": 2,
        "tasks_executing": 35,
        "tasks_queued": 8
      }
    ]
  },
  "summary": {
    "total_executions": 1247,
    "success_rate": 98.5,
    "average_execution_time": 80.3,
    "peak_concurrent_executions": 45
  }
}
```

## Database Management

### Database Statistics

Get database health and statistics.

#### Endpoint
```
GET /admin/database/stats
```

#### Response
```json
{
  "status": "healthy",
  "connection_info": {
    "host": "localhost",
    "port": 5432,
    "database": "flux",
    "active_connections": 15,
    "max_connections": 100
  },
  "table_statistics": {
    "workflows": {
      "row_count": 45,
      "size_mb": 2.1
    },
    "executions": {
      "row_count": 12847,
      "size_mb": 145.7
    },
    "tasks": {
      "row_count": 89234,
      "size_mb": 445.3
    },
    "events": {
      "row_count": 245678,
      "size_mb": 1250.4
    }
  },
  "performance": {
    "average_query_time": 5.2,
    "slow_queries_count": 3,
    "cache_hit_ratio": 95.8
  }
}
```

### Cleanup Operations

Perform database cleanup operations.

#### Endpoint
```
POST /admin/database/cleanup
```

#### Request Body
```json
{
  "operations": ["old_executions", "completed_tasks", "expired_events"],
  "retention_days": 30,
  "dry_run": false
}
```

#### Response
```json
{
  "status": "success",
  "operations_performed": [
    {
      "operation": "old_executions",
      "records_deleted": 1247,
      "space_freed_mb": 45.2
    },
    {
      "operation": "completed_tasks",
      "records_deleted": 8934,
      "space_freed_mb": 123.4
    }
  ],
  "total_records_deleted": 10181,
  "total_space_freed_mb": 168.6,
  "cleanup_duration_seconds": 15.3
}
```

## Audit Logs

### Get Audit Logs

Retrieve system audit logs.

#### Endpoint
```
GET /admin/audit
```

#### Query Parameters
- `start_date` (string, optional): Start date (ISO 8601)
- `end_date` (string, optional): End date (ISO 8601)
- `user` (string, optional): Filter by user
- `action` (string, optional): Filter by action type
- `page` (integer, optional): Page number (default: 1)
- `per_page` (integer, optional): Results per page (default: 50)

#### Response
```json
{
  "audit_logs": [
    {
      "id": "audit_123456",
      "timestamp": "2024-01-15T10:30:00Z",
      "user": "admin_user",
      "action": "secret_created",
      "resource": "database_password",
      "ip_address": "192.168.1.100",
      "user_agent": "curl/7.68.0",
      "details": {
        "secret_name": "database_password",
        "description": "Database connection password"
      }
    },
    {
      "id": "audit_123457",
      "timestamp": "2024-01-15T10:25:00Z",
      "user": "admin_user",
      "action": "config_updated",
      "resource": "server_config",
      "ip_address": "192.168.1.100",
      "details": {
        "changes": ["execution.default_timeout"]
      }
    }
  ],
  "pagination": {
    "current_page": 1,
    "per_page": 50,
    "total_pages": 1,
    "total_logs": 25
  }
}
```

## Error Handling

Administration endpoints return specific error codes:

### Insufficient Permissions
```json
{
  "status": "error",
  "error_code": "INSUFFICIENT_PERMISSIONS",
  "message": "Admin privileges required for this operation",
  "required_role": "admin",
  "current_role": "user"
}
```

### Configuration Error
```json
{
  "status": "error",
  "error_code": "INVALID_CONFIGURATION",
  "message": "Invalid configuration value",
  "field": "execution.max_concurrent_executions",
  "provided_value": -5,
  "valid_range": "1-1000"
}
```

### Resource Not Found
```json
{
  "status": "error",
  "error_code": "RESOURCE_NOT_FOUND",
  "message": "Worker 'worker_unknown' not found",
  "available_workers": ["worker_001", "worker_002", "worker_003"]
}
```

## Rate Limiting

Administration endpoints have specific rate limits:
- **Configuration operations**: 10 requests per minute
- **Worker management**: 20 requests per minute
- **Health checks**: 100 requests per minute
- **Audit log access**: 50 requests per minute

## Security Considerations

- All administration endpoints require admin-level authentication
- Sensitive operations are logged in the audit trail
- Rate limiting prevents abuse of administrative functions
- Configuration changes are validated before application

## Next Steps

- Learn about [CLI administration commands](../cli/secrets-commands.md)
- Explore [Server Configuration](../../reference/configuration/server-configuration.md) for detailed configuration options
- Check out [Monitoring and Observability](../../deployment/monitoring-observability.md) for production monitoring setup
