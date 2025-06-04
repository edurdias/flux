# Worker Management

This guide covers deploying, configuring, and managing Flux workers for optimal performance and reliability.

## Worker Overview

Flux workers are the execution units that run your workflow tasks. Understanding how to properly deploy and manage workers is crucial for scalable and reliable workflow execution.

### Worker Responsibilities
- Execute individual workflow tasks
- Manage task lifecycle and resources
- Report status back to the server
- Handle task isolation and security
- Manage local caching and temporary data

## Worker Deployment

### Basic Worker Startup

#### Local Development
```bash
# Start a single worker with default settings
flux start worker

# Worker with custom configuration
flux start worker --server-host=localhost --server-port=8000 --workers=2
```

#### Production Deployment
```bash
# Start worker with production settings
flux start worker \
  --server-host=flux-server.company.com \
  --server-port=8000 \
  --workers=8 \
  --log-level=INFO \
  --metrics-port=9090
```

### Worker Configuration Options

#### Command Line Options
```bash
flux start worker --help

Options:
  --server-host TEXT      Server hostname (default: localhost)
  --server-port INTEGER   Server port (default: 8000)
  --workers INTEGER       Number of worker processes (default: CPU count)
  --log-level TEXT        Logging level (DEBUG, INFO, WARN, ERROR)
  --metrics-port INTEGER  Port for metrics endpoint
  --node-labels TEXT      Labels for worker node selection
  --max-memory TEXT       Maximum memory per worker (e.g., 2Gi)
  --max-cpu TEXT          Maximum CPU per worker (e.g., 2.0)
  --work-dir TEXT         Working directory for task execution
  --temp-dir TEXT         Temporary directory for task data
  --keepalive INTEGER     Heartbeat interval in seconds
```

#### Environment Variables
```bash
# Server connection
export FLUX_SERVER_HOST=flux-server.company.com
export FLUX_SERVER_PORT=8000

# Worker configuration
export FLUX_WORKER_COUNT=8
export FLUX_WORKER_MAX_MEMORY=4Gi
export FLUX_WORKER_MAX_CPU=2.0

# Directories
export FLUX_WORK_DIR=/opt/flux/work
export FLUX_TEMP_DIR=/tmp/flux

# Security
export FLUX_API_TOKEN=your-api-token
export FLUX_TLS_ENABLED=true

# Start worker with environment config
flux start worker
```

### Configuration Files

#### Worker Configuration (worker.yaml)
```yaml
# Worker configuration file
server:
  host: "flux-server.company.com"
  port: 8000
  tls_enabled: true
  api_token: "${FLUX_API_TOKEN}"

worker:
  count: 8
  max_memory: "4Gi"
  max_cpu: "2.0"

  # Resource limits per task
  task_limits:
    default_memory: "512Mi"
    default_cpu: "0.5"
    max_memory: "2Gi"
    max_cpu: "1.0"
    timeout: 300

  # Working directories
  work_dir: "/opt/flux/work"
  temp_dir: "/tmp/flux"

  # Health and monitoring
  heartbeat_interval: 30
  health_check_port: 8001
  metrics_port: 9090

  # Node labels for task selection
  labels:
    environment: "production"
    availability_zone: "us-west-2a"
    instance_type: "c5.2xlarge"
    hardware: "cpu"

logging:
  level: "INFO"
  format: "json"
  file: "/var/log/flux/worker.log"
  max_size: "100MB"
  max_files: 10

security:
  task_isolation: true
  network_isolation: false
  readonly_filesystem: false
```

#### Starting with Configuration File
```bash
flux start worker --config=/etc/flux/worker.yaml
```

## Worker Scaling

### Manual Scaling

#### Adding Workers
```bash
# Add more workers to existing deployment
flux start worker --workers=16  # Double the workers

# Start specialized workers
flux start worker \
  --workers=4 \
  --node-labels="hardware=gpu,memory=high" \
  --max-memory=32Gi
```

#### Removing Workers
```bash
# Graceful shutdown - completes running tasks
pkill -TERM flux-worker

# Force shutdown - terminates immediately
pkill -KILL flux-worker
```

### Auto-scaling Strategies

#### Container Orchestration (Kubernetes)
```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: flux-worker
spec:
  replicas: 5
  selector:
    matchLabels:
      app: flux-worker
  template:
    metadata:
      labels:
        app: flux-worker
    spec:
      containers:
      - name: worker
        image: flux:latest
        command: ["flux", "start", "worker"]
        env:
        - name: FLUX_SERVER_HOST
          value: "flux-server"
        - name: FLUX_WORKER_COUNT
          value: "4"
        resources:
          requests:
            memory: "2Gi"
            cpu: "1.0"
          limits:
            memory: "4Gi"
            cpu: "2.0"

---
# Auto-scaling configuration
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: flux-worker-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: flux-worker
  minReplicas: 2
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

#### Docker Compose Scaling
```yaml
# docker-compose.yml
version: '3.8'
services:
  flux-server:
    image: flux:latest
    command: flux start server
    ports:
      - "8000:8000"

  flux-worker:
    image: flux:latest
    command: flux start worker --server-host=flux-server
    depends_on:
      - flux-server
    deploy:
      replicas: 5
    environment:
      - FLUX_WORKER_COUNT=4
      - FLUX_WORKER_MAX_MEMORY=2Gi

# Scale workers
docker-compose up --scale flux-worker=10
```

## Worker Specialization

### Hardware-Specific Workers

#### GPU Workers
```bash
# Start GPU-enabled workers
flux start worker \
  --node-labels="hardware=gpu,gpu_type=v100" \
  --workers=2 \
  --max-memory=16Gi
```

```python
# Tasks that require GPU workers
@task(node_selector={"hardware": "gpu"})
def train_model(dataset: Dataset) -> Model:
    """Runs only on GPU-enabled workers"""
    import torch
    device = torch.device("cuda")
    return train_neural_network(dataset, device)

@task(node_selector={"gpu_type": "v100"})
def inference_task(model: Model, data: Data) -> Predictions:
    """Requires specific GPU type"""
    return run_inference(model, data)
```

#### High-Memory Workers
```bash
# Workers for memory-intensive tasks
flux start worker \
  --node-labels="memory=high" \
  --workers=4 \
  --max-memory=64Gi
```

```python
@task(node_selector={"memory": "high"})
def process_large_dataset(data: LargeDataset) -> ProcessedData:
    """Requires high-memory workers"""
    return process_in_memory(data)
```

### Environment-Specific Workers

#### Different Environments
```bash
# Development environment workers
flux start worker --node-labels="env=dev,tier=development"

# Staging environment workers
flux start worker --node-labels="env=staging,tier=staging"

# Production environment workers
flux start worker --node-labels="env=prod,tier=production"
```

```python
# Environment-specific task execution
@task(node_selector={"env": "prod"})
def production_task(data: Data) -> Result:
    """Only runs in production environment"""
    return process_production_data(data)

@task(node_selector={"tier": "development"})
def debug_task(data: Data) -> DebugInfo:
    """Only runs in development"""
    return generate_debug_info(data)
```

## Resource Management

### Memory Management

#### Per-Worker Limits
```yaml
worker:
  max_memory: "8Gi"          # Total memory per worker process
  task_limits:
    default_memory: "512Mi"   # Default per task
    max_memory: "2Gi"        # Maximum per task
```

#### Memory Monitoring
```python
# Monitor memory usage in tasks
@task(memory_limit="1Gi")
def memory_monitored_task(ctx: ExecutionContext, data: Data) -> Result:
    # Get current memory usage
    memory_usage = ctx.get_resource_usage()["memory"]
    ctx.log(f"Current memory usage: {memory_usage}")

    if memory_usage > 0.8:  # 80% of limit
        ctx.log("High memory usage detected")

    return process_data(data)
```

### CPU Management

#### CPU Allocation
```yaml
worker:
  max_cpu: "4.0"            # 4 CPU cores per worker
  task_limits:
    default_cpu: "0.5"      # Half a core per task
    max_cpu: "2.0"          # Maximum 2 cores per task
```

#### CPU-Intensive Tasks
```python
@task(cpu_limit="4.0", parallel=True)
def parallel_computation(data: List[Data]) -> List[Result]:
    """Use multiple CPU cores for parallel processing"""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(process_item, data))
```

### I/O Management

#### Disk Space Management
```yaml
worker:
  work_dir: "/opt/flux/work"
  temp_dir: "/tmp/flux"
  cleanup:
    temp_files: true
    max_age: "24h"
    max_size: "10Gi"
```

#### Network I/O Limits
```python
@task(
    network_policy=NetworkPolicy(
        outbound_rate_limit="100Mbps",
        inbound_rate_limit="50Mbps",
        allowed_hosts=["api.company.com", "*.amazonaws.com"]
    )
)
def api_integration_task(endpoint: str) -> Response:
    """Task with network limitations"""
    return call_external_api(endpoint)
```

## Health Monitoring

### Worker Health Checks

#### Built-in Health Endpoints
```bash
# Health check endpoint (default: port 8001)
curl http://worker-host:8001/health

# Response
{
  "status": "healthy",
  "worker_id": "worker-123",
  "uptime": 3600,
  "tasks_completed": 42,
  "current_tasks": 3,
  "memory_usage": "2.1Gi",
  "cpu_usage": "45%",
  "last_heartbeat": "2024-01-20T10:30:00Z"
}
```

#### Custom Health Checks
```python
# Custom health check for workers
@health_check(interval=30)
def custom_worker_health(ctx: WorkerContext) -> HealthStatus:
    """Custom health check logic"""

    # Check disk space
    disk_usage = ctx.get_disk_usage()
    if disk_usage > 0.9:
        return HealthStatus.UNHEALTHY("Disk space critical")

    # Check database connectivity
    if not ctx.test_database_connection():
        return HealthStatus.DEGRADED("Database connection issues")

    return HealthStatus.HEALTHY()
```

### Monitoring Integration

#### Prometheus Metrics
```yaml
# Metrics configuration
metrics:
  enabled: true
  port: 9090
  path: "/metrics"

  # Custom metrics
  custom_metrics:
    - name: "task_processing_time"
      type: "histogram"
      help: "Time spent processing tasks"

    - name: "worker_errors_total"
      type: "counter"
      help: "Total number of worker errors"
```

#### Example Metrics
```python
# Worker metrics (exposed at /metrics)
flux_worker_tasks_completed_total{worker_id="worker-1"}
flux_worker_tasks_failed_total{worker_id="worker-1"}
flux_worker_memory_usage_bytes{worker_id="worker-1"}
flux_worker_cpu_usage_percent{worker_id="worker-1"}
flux_worker_uptime_seconds{worker_id="worker-1"}
flux_worker_heartbeat_timestamp{worker_id="worker-1"}
```

## Security and Isolation

### Task Isolation

#### Process Isolation
```yaml
security:
  task_isolation: true
  process_isolation: "strict"

  # Resource isolation
  cgroups:
    enabled: true
    memory_limit: true
    cpu_limit: true

  # Filesystem isolation
  filesystem:
    readonly_root: false
    private_temp: true
    mount_isolation: true
```

#### Network Isolation
```yaml
security:
  network_isolation: true

  # Allowed network access
  network_policy:
    outbound:
      - "api.company.com:443"
      - "*.amazonaws.com:443"
      - "127.0.0.1:*"

    # Block everything else
    default_deny: true
```

### Secrets Management in Workers

#### Secure Secret Access
```python
@task(secrets=["database_url", "api_key"])
def secure_task(ctx: ExecutionContext, data: Data) -> Result:
    """Task with secure secret access"""

    # Secrets are injected securely
    db_url = ctx.get_secret("database_url")
    api_key = ctx.get_secret("api_key")

    # Use secrets securely
    with database_connection(db_url) as db:
        return process_with_api(data, api_key, db)
```

## Troubleshooting Workers

### Common Issues

#### Worker Connection Problems
```bash
# Check worker connectivity
flux worker status --server-host=flux-server.company.com

# Debug connection issues
flux start worker --log-level=DEBUG --server-host=flux-server.company.com
```

#### Resource Exhaustion
```bash
# Monitor resource usage
top -p $(pgrep flux-worker)

# Check disk space
df -h /opt/flux/work
df -h /tmp/flux

# Memory usage per worker
ps aux | grep flux-worker
```

#### Task Failures
```python
# Add comprehensive error handling
@task(
    retry_policy=RetryPolicy(
        max_attempts=3,
        backoff=ExponentialBackoff(initial=1.0, max=60.0)
    ),
    timeout=300
)
def robust_task(ctx: ExecutionContext, data: Data) -> Result:
    try:
        return process_data(data)
    except Exception as e:
        ctx.log(f"Task failed: {e}", level="ERROR")
        ctx.log(f"Worker info: {ctx.get_worker_info()}")
        raise
```

### Diagnostic Commands

#### Worker Status
```bash
# List all workers
flux worker list

# Get worker details
flux worker info worker-123

# Worker logs
flux worker logs worker-123 --tail=100
```

#### Performance Analysis
```bash
# Worker performance metrics
curl http://worker-host:9090/metrics | grep flux_worker

# Task execution history
flux task history --worker=worker-123 --limit=50
```

## Best Practices

### Deployment Best Practices
1. **Right-Size Workers**: Match worker resources to typical task requirements
2. **Use Labels**: Implement proper node selection for task routing
3. **Monitor Resources**: Set up comprehensive monitoring and alerting
4. **Graceful Shutdown**: Always allow workers to complete tasks before stopping
5. **Health Checks**: Implement custom health checks for application-specific needs

### Performance Best Practices
1. **Resource Limits**: Set appropriate CPU and memory limits
2. **Task Batching**: Use batching for small, frequent tasks
3. **Caching**: Implement local caching for frequently accessed data
4. **Connection Pooling**: Reuse connections to external services
5. **Cleanup**: Regular cleanup of temporary files and logs

### Security Best Practices
1. **Least Privilege**: Run workers with minimal required permissions
2. **Network Isolation**: Restrict network access to necessary services only
3. **Secret Management**: Use secure secret injection, never environment variables
4. **Process Isolation**: Enable task isolation for untrusted code
5. **Regular Updates**: Keep worker images and dependencies updated

## Next Steps

- **[Network Configuration](network-configuration.md)** - Network setup and security
- **[Deployment Strategies](deployment-strategies.md)** - Production deployment patterns
- **[Scaling and Performance](scaling-performance.md)** - Performance optimization
- **[Monitoring and Observability](monitoring-observability.md)** - Comprehensive monitoring

## See Also

- **[Server Architecture](server-architecture.md)** - Understanding the overall architecture
- **[Task Configuration](../user-guide/task-configuration.md)** - Configuring tasks for workers
- **[Performance Optimization](../performance/resource-management.md)** - Advanced performance tuning
