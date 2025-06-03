# Frequently Asked Questions

Common questions and answers about Flux workflow orchestration.

> 🚀 **New to Flux?** Start with [Your First Workflow Tutorial](your-first-workflow.md) for a hands-on introduction before diving into these Q&As.

## General Questions

### What is Flux?

**Q**: What exactly is Flux and how is it different from other workflow engines?

**A**: Flux is a Python-first workflow orchestration engine that lets you define workflows as code using decorators. Unlike configuration-driven tools (like Airflow) or visual workflow builders, Flux workflows are written in pure Python, giving you full IDE support, type checking, and the ability to test workflows like regular code.

Key differences:
- **Code-first**: Write workflows in Python, not YAML or GUI
- **Lightweight**: No complex UI or database setup required
- **Developer-friendly**: Full IDE support, debugging, and testing
- **Flexible deployment**: Run locally, in containers, or distributed

### When should I use Flux?

**Q**: What types of problems is Flux best suited for?

**A**: Flux excels at:

- **Data processing pipelines**: ETL/ELT workflows with complex logic
- **Business process automation**: Multi-step workflows with error handling
- **API orchestration**: Coordinating multiple service calls
- **Batch processing**: Processing large datasets with parallel execution
- **Event-driven workflows**: Responding to triggers and events

Flux is ideal when you need more than simple task queues but want something lighter than full workflow platforms.

### How does Flux compare to Celery?

**Q**: I'm using Celery. Why would I switch to Flux?

**A**: Celery is a task queue, while Flux is a workflow orchestration engine:

**Celery**:
- Great for distributed task execution
- Requires message broker (Redis/RabbitMQ)
- Tasks are independent
- No built-in workflow orchestration

**Flux**:
- Built-in workflow orchestration
- No external message broker needed
- Tasks can be composed into workflows
- Built-in state management and error handling
- Visual workflow monitoring

Use Flux when you need to coordinate multiple tasks into workflows, not just execute independent tasks.

## Installation and Setup

### System Requirements

**Q**: What are the minimum system requirements for Flux?

**A**:
- **Python**: 3.8 or higher
- **RAM**: 512MB minimum (2GB+ recommended for production)
- **CPU**: Any modern processor
- **OS**: Linux, macOS, or Windows
- **Network**: HTTP connectivity between server and workers

### Installation Issues

**Q**: I installed Flux but the `flux` command isn't found. What's wrong?

**A**: This usually means:

1. **Wrong Python environment**: Make sure you're in the correct virtual environment
2. **PATH issues**: Try `python -m flux.cli` instead of `flux`
3. **User installation**: If using `pip install --user`, ensure `~/.local/bin` is in your PATH

```bash
# Check installation
pip list | grep flux

# Try alternative command
python -m flux.cli --help

# Fix PATH (Linux/Mac)
export PATH="$PATH:$HOME/.local/bin"
```

### Can I use Flux with Docker?

**Q**: How do I run Flux in Docker containers?

**A**: Yes! Here's a basic Docker setup:

```dockerfile
FROM python:3.9

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Server
EXPOSE 8080
CMD ["flux", "start", "server", "--host", "0.0.0.0"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  flux-server:
    build: .
    ports:
      - "8080:8080"

  flux-worker:
    build: .
    command: flux start worker --server-url http://flux-server:8080
    depends_on:
      - flux-server
```

## Workflow Development

### Workflow Design

**Q**: What's the best way to structure complex workflows?

**A**: Follow these patterns:

1. **Break down into small tasks**: Each task should have a single responsibility
2. **Use composition**: Combine simple tasks into complex workflows
3. **Handle errors gracefully**: Add error handling at each level
4. **Make workflows testable**: Design for easy unit testing

```python
# Good: Small, focused tasks
@task
def fetch_data(source: str) -> dict:
    return get_data_from_source(source)

@task
def validate_data(data: dict) -> dict:
    return validate_schema(data)

@task
def process_data(data: dict) -> dict:
    return transform_data(data)

@workflow
def data_pipeline(source: str):
    raw_data = fetch_data(source)
    valid_data = validate_data(raw_data)
    return process_data(valid_data)
```

### Task Dependencies

**Q**: How do I handle complex dependencies between tasks?

**A**: Flux handles dependencies automatically through data flow:

```python
@workflow
def complex_dependencies():
    # Parallel tasks - no dependencies
    data_a = fetch_data_a()
    data_b = fetch_data_b()

    # Depends on both data_a and data_b
    combined = merge_data(data_a, data_b)

    # Depends on combined result
    result = process_combined(combined)

    return result
```

For more complex patterns, use conditional logic:

```python
@workflow
def conditional_workflow(mode: str):
    if mode == "fast":
        return quick_process()
    elif mode == "thorough":
        data = fetch_data()
        validated = validate_data(data)
        return detailed_process(validated)
    else:
        raise ValueError(f"Unknown mode: {mode}")
```

### Error Handling

**Q**: How do I handle errors in workflows?

**A**: Flux provides several error handling patterns:

1. **Task-level error handling**:
```python
@task
def safe_task(data):
    try:
        return risky_operation(data)
    except Exception as e:
        return {"error": str(e), "data": data}
```

2. **Workflow-level error handling**:
```python
@workflow
def robust_workflow(input_data):
    try:
        result = process_data(input_data)
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}
```

3. **Retry logic**:
```python
@task
def task_with_retries(data, max_retries=3):
    for attempt in range(max_retries):
        try:
            return process_data(data)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            print(f"Attempt {attempt + 1} failed, retrying...")
```

## Performance and Scaling

### Performance Optimization

**Q**: My workflows are running slowly. How can I speed them up?

**A**: Try these optimization strategies:

1. **Use parallel processing**:
```python
from flux import parallel

@workflow
def fast_workflow(items):
    # Process items in parallel
    return parallel([process_item(item) for item in items])
```

2. **Optimize data handling**:
```python
@task
def efficient_processing(large_dataset):
    # Process in chunks
    results = []
    for chunk in chunks(large_dataset, size=1000):
        results.extend(process_chunk(chunk))
    return results
```

3. **Reduce data transfer**:
```python
# ❌ Bad - transfers large data between tasks
@workflow
def inefficient():
    large_data = fetch_large_dataset()
    return process_data(large_data)

# ✅ Good - process data where it's fetched
@task
def efficient_fetch_and_process(source):
    data = fetch_from_source(source)
    return process_data(data)  # Process immediately
```

### Scaling Workers

**Q**: How do I scale Flux to handle more workload?

**A**: Scale horizontally by adding more workers:

```bash
# Start multiple workers on same machine
flux start worker --worker-id worker-1 &
flux start worker --worker-id worker-2 &
flux start worker --worker-id worker-3 &

# Or on different machines
flux start worker --server-url http://flux-server:8080 --worker-id remote-worker-1
```

For auto-scaling, use container orchestration:

```yaml
# Kubernetes deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: flux-workers
spec:
  replicas: 5  # Scale as needed
  selector:
    matchLabels:
      app: flux-worker
  template:
    spec:
      containers:
      - name: worker
        image: your-flux-image
        command: ["flux", "start", "worker"]
```

### Memory Management

**Q**: My workflows consume too much memory. What can I do?

**A**: Use these memory optimization techniques:

1. **Process data in chunks**:
```python
@task
def memory_efficient_task(file_path):
    results = []
    with open(file_path, 'r') as f:
        for chunk in read_chunks(f, size=8192):
            result = process_chunk(chunk)
            results.append(result)
    return results
```

2. **Clean up resources**:
```python
@task
def cleanup_task():
    try:
        large_data = load_data()
        result = process_data(large_data)
        del large_data  # Explicit cleanup
        return result
    finally:
        cleanup_resources()
```

3. **Use generators for large datasets**:
```python
@task
def generator_task(items):
    # Return summary instead of full data
    processed_count = 0
    for item in items:
        process_item(item)
        processed_count += 1
    return {"processed": processed_count}
```

## Production Deployment

### Security

**Q**: How do I secure Flux in production?

**A**: Follow these security best practices:

1. **Use secrets management**:
```bash
# Store sensitive data as secrets
flux secrets create api_key "your-secret-api-key"
flux secrets create db_password "secure-password"
```

2. **Network security**:
```bash
# Run server on private network
flux start server --host 127.0.0.1 --port 8080

# Use HTTPS in production (with reverse proxy)
# nginx/haproxy → https → flux server
```

3. **Input validation**:
```python
@task
def secure_task(user_input: str) -> dict:
    # Validate and sanitize inputs
    if not isinstance(user_input, str):
        raise ValueError("Input must be string")

    cleaned_input = sanitize_input(user_input)
    return process_input(cleaned_input)
```

### Monitoring

**Q**: How do I monitor Flux workflows in production?

**A**: Implement monitoring at multiple levels:

1. **Workflow-level logging**:
```python
import logging

@workflow
def monitored_workflow(input_data):
    logging.info(f"Starting workflow with input: {input_data}")

    try:
        result = process_data(input_data)
        logging.info(f"Workflow completed successfully")
        return result
    except Exception as e:
        logging.error(f"Workflow failed: {e}")
        raise
```

2. **Health checks**:
```bash
# Check server health
curl http://flux-server:8080/health

# Monitor workflow status
flux workflow status my_workflow execution_id
```

3. **Resource monitoring**:
```bash
# Monitor system resources
top
htop
docker stats  # If using containers
```

### Backup and Recovery

**Q**: How do I backup workflow state and handle disasters?

**A**: Implement these backup strategies:

1. **Workflow code backup**:
```bash
# Version control your workflows
git add workflows/
git commit -m "Backup workflows"
git push origin main
```

2. **State backup**: Flux stores state in its database. Back up the database regularly:
```bash
# If using SQLite (development)
cp flux.db flux.db.backup

# If using PostgreSQL (production)
pg_dump flux_db > flux_backup.sql
```

3. **Disaster recovery plan**:
- Keep workflow code in version control
- Regular database backups
- Document deployment procedures
- Test recovery procedures

## Integration and Compatibility

### API Integration

**Q**: Can I trigger Flux workflows from external systems?

**A**: Yes, use the REST API:

```python
import requests

# Trigger workflow via API
response = requests.post(
    'http://flux-server:8080/api/workflows/my_workflow/run',
    json={"input": {"param": "value"}}
)

execution_id = response.json()["execution_id"]

# Check status
status_response = requests.get(
    f'http://flux-server:8080/api/workflows/my_workflow/status/{execution_id}'
)
```

### Database Integration

**Q**: How do I integrate Flux workflows with databases?

**A**: Use database connections within tasks:

```python
import psycopg2

@task
def database_task(query: str) -> list:
    conn = psycopg2.connect(
        host="db-host",
        database="mydb",
        user="user",
        password="password"
    )

    try:
        cur = conn.cursor()
        cur.execute(query)
        results = cur.fetchall()
        return results
    finally:
        conn.close()
```

For better security, use secrets:

```bash
flux secrets create db_password "your-db-password"
```

```python
@task
def secure_db_task(query: str) -> list:
    # Get password from secrets (implement get_secret function)
    password = get_secret("db_password")

    conn = psycopg2.connect(
        host="db-host",
        database="mydb",
        user="user",
        password=password
    )
    # ... rest of the code
```

### Cloud Integration

**Q**: Can I run Flux workflows on cloud platforms?

**A**: Yes, Flux works on all major cloud platforms:

**AWS**:
```bash
# Run on EC2 instances
# Use ECS/EKS for container deployment
# Store secrets in AWS Secrets Manager
```

**Google Cloud**:
```bash
# Run on Compute Engine or GKE
# Use Google Secret Manager
# Integrate with Cloud Functions
```

**Azure**:
```bash
# Run on Azure VMs or AKS
# Use Azure Key Vault for secrets
# Integrate with Azure Functions
```

## Development Workflow

### Testing

**Q**: How do I test Flux workflows?

**A**: Use these testing approaches:

1. **Unit test individual tasks**:
```python
def test_my_task():
    result = my_task(test_input)
    assert result == expected_output
```

2. **Integration test workflows**:
```python
@workflow
def test_workflow():
    return my_actual_workflow(test_data)

# Run test workflow
result = test_workflow()
assert result["status"] == "success"
```

3. **Mock external dependencies**:
```python
from unittest.mock import patch

@patch('my_module.external_api_call')
def test_with_mock(mock_api):
    mock_api.return_value = {"data": "test"}
    result = my_task()
    assert result == expected_result
```

### Debugging

**Q**: How do I debug Flux workflows?

**A**: Use these debugging techniques:

1. **Add debug logging**:
```python
@task
def debug_task(data):
    print(f"DEBUG: Input data: {data}")
    result = process_data(data)
    print(f"DEBUG: Output data: {result}")
    return result
```

2. **Use simple test workflows**:
```python
@workflow
def debug_workflow():
    print("Debug: Starting workflow")
    return {"debug": "completed"}
```

3. **Check execution status**:
```bash
# Get detailed execution information
flux workflow status my_workflow execution_id --verbose
```

### Version Control

**Q**: How should I manage workflow versions?

**A**: Follow these best practices:

1. **Use Git for workflow code**:
```bash
git add workflows/
git commit -m "Add customer processing workflow"
git tag v1.0.0
```

2. **Version your workflows**:
```python
@workflow
def customer_workflow_v2(data):
    # Updated logic
    return improved_processing(data)
```

3. **Backward compatibility**:
```python
@workflow
def legacy_workflow(data, version="v1"):
    if version == "v1":
        return legacy_processing(data)
    elif version == "v2":
        return new_processing(data)
    else:
        raise ValueError(f"Unknown version: {version}")
```

## Still Have Questions?

If your question isn't answered here, try these resources:

### Documentation
1. **[Troubleshooting Guide](troubleshooting.md)** - Common issues and solutions
2. **[Core Concepts](../core-concepts/workflow-management.md)** - In-depth explanations
3. **[Basic Concepts](../getting-started/basic_concepts.md)** - Fundamental concepts
4. **[CLI Reference](../cli/index.md)** - Complete command reference

### Learning Resources
1. **[Your First Workflow Tutorial](your-first-workflow.md)** - Step-by-step introduction
2. **[Working with Tasks Tutorial](working-with-tasks.md)** - Task design patterns
3. **[Best Practices](best-practices.md)** - Production-ready guidelines
4. **[Use Cases](../introduction/use-cases.md)** - Real-world examples

### Community
1. **[GitHub Issues](https://github.com/edurdias/flux/issues)** - Search existing questions
2. **[GitHub Discussions](https://github.com/edurdias/flux/discussions)** - Ask the community
3. **Examples**: Browse [working examples](https://github.com/edurdias/flux/tree/main/examples)

We're always updating this FAQ based on community questions, so don't hesitate to ask! 🤔
