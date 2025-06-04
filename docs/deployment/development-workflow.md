# Development Workflow

This guide covers best practices and recommended workflows for developing applications with Flux. Whether you're building data pipelines, automation workflows, or complex distributed applications, following these practices will help you create maintainable, testable, and reliable workflows.

## Development Environment Setup

### Project Structure

Organize your Flux project with a clear structure:

```
my-flux-project/
├── workflows/
│   ├── __init__.py
│   ├── data_pipeline.py
│   ├── user_onboarding.py
│   └── batch_processing.py
├── tasks/
│   ├── __init__.py
│   ├── data_tasks.py
│   ├── email_tasks.py
│   └── validation_tasks.py
├── tests/
│   ├── __init__.py
│   ├── test_data_pipeline.py
│   ├── test_user_onboarding.py
│   └── fixtures/
├── config/
│   ├── development.yaml
│   ├── staging.yaml
│   └── production.yaml
├── requirements.txt
├── pyproject.toml
├── README.md
└── .gitignore
```

### Virtual Environment

Set up an isolated Python environment:

```bash
# Create virtual environment
python -m venv flux-env

# Activate virtual environment
source flux-env/bin/activate  # Linux/macOS
# or
flux-env\Scripts\activate     # Windows

# Install Flux and dependencies
pip install flux-workflow
pip install -r requirements.txt
```

### Development Dependencies

Include development tools in your `requirements-dev.txt`:

```txt
# requirements-dev.txt
flux-workflow
pytest>=7.0.0
pytest-asyncio>=0.21.0
pytest-cov>=4.0.0
black>=23.0.0
isort>=5.12.0
flake8>=6.0.0
mypy>=1.0.0
pre-commit>=3.0.0
```

## Workflow Development Lifecycle

### 1. Design Phase

Before writing code, design your workflow:

```python
# workflows/data_pipeline.py - Design sketch
"""
Data Pipeline Workflow

Input: {"source": "database", "table": "users", "filters": {...}}

Steps:
1. Validate input parameters
2. Extract data from source
3. Transform data (clean, normalize)
4. Validate transformed data
5. Load data to destination
6. Send completion notification

Error handling:
- Retry transient failures (network, temporary DB issues)
- Fallback to alternative data sources
- Rollback on critical failures
"""
```

### 2. Implementation Phase

Start with a simple implementation:

```python
# workflows/data_pipeline.py
from flux import task, workflow, ExecutionContext
from typing import Dict, List, Any

@task
async def validate_input(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate pipeline configuration."""
    required_fields = ["source", "table"]
    for field in required_fields:
        if field not in config:
            raise ValueError(f"Missing required field: {field}")

    return config

@task
async def extract_data(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract data from source."""
    # Implementation here
    return []

@task
async def transform_data(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Transform and clean data."""
    # Implementation here
    return data

@workflow
async def data_pipeline(ctx: ExecutionContext[Dict[str, Any]]):
    """Main data pipeline workflow."""
    # Validate input
    config = await validate_input(ctx.input)

    # Extract data
    raw_data = await extract_data(config)

    # Transform data
    clean_data = await transform_data(raw_data)

    return {
        "status": "completed",
        "records_processed": len(clean_data),
        "output_data": clean_data
    }
```

### 3. Testing Phase

Write comprehensive tests:

```python
# tests/test_data_pipeline.py
import pytest
from workflows.data_pipeline import data_pipeline, validate_input, extract_data

@pytest.mark.asyncio
async def test_validate_input_success():
    """Test successful input validation."""
    config = {"source": "database", "table": "users"}
    result = await validate_input(config)
    assert result == config

@pytest.mark.asyncio
async def test_validate_input_missing_field():
    """Test validation with missing required field."""
    config = {"source": "database"}
    with pytest.raises(ValueError, match="Missing required field: table"):
        await validate_input(config)

@pytest.mark.asyncio
async def test_data_pipeline_integration():
    """Test complete pipeline integration."""
    input_data = {
        "source": "test_database",
        "table": "test_users",
        "filters": {"active": True}
    }

    result = data_pipeline.run(input_data)

    assert result.output["status"] == "completed"
    assert "records_processed" in result.output
```

### 4. Local Testing

Test workflows locally during development:

```python
# test_local.py
from workflows.data_pipeline import data_pipeline

async def test_pipeline_locally():
    """Test pipeline with local execution."""
    test_input = {
        "source": "test_db",
        "table": "users",
        "filters": {"active": True}
    }

    # Run locally
    result = data_pipeline.run(test_input)
    print(f"Pipeline result: {result.output}")

    # Check execution details
    print(f"Execution time: {result.execution_time}")
    print(f"Tasks executed: {len(result.events)}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_pipeline_locally())
```

## Development Best Practices

### Code Organization

#### Modular Tasks

Create reusable tasks in separate modules:

```python
# tasks/data_tasks.py
from flux import task
from typing import List, Dict, Any

@task
async def validate_data_format(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate data format and types."""
    for record in data:
        if not isinstance(record, dict):
            raise ValueError("Invalid record format")
    return data

@task
async def clean_data(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clean and normalize data."""
    cleaned = []
    for record in data:
        # Cleaning logic here
        cleaned_record = {k: v for k, v in record.items() if v is not None}
        cleaned.append(cleaned_record)
    return cleaned
```

#### Configuration Management

Use configuration files for different environments:

```yaml
# config/development.yaml
database:
  host: localhost
  port: 5432
  name: flux_dev
  user: dev_user

flux:
  server:
    host: localhost
    port: 8080

logging:
  level: DEBUG

retry_policy:
  max_retries: 2
  base_delay: 1.0
```

```python
# config/settings.py
import yaml
import os
from typing import Dict, Any

def load_config(env: str = "development") -> Dict[str, Any]:
    """Load configuration for specified environment."""
    config_file = f"config/{env}.yaml"

    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    with open(config_file, 'r') as f:
        return yaml.safe_load(f)

# Usage in workflows
config = load_config(os.getenv("FLUX_ENV", "development"))
```

### Error Handling Patterns

#### Graceful Degradation

Design workflows to handle partial failures:

```python
@workflow
async def resilient_pipeline(ctx: ExecutionContext[Dict[str, Any]]):
    """Pipeline with graceful degradation."""
    results = {"status": "partial", "components": {}}

    # Critical component (must succeed)
    try:
        core_result = await process_core_data(ctx.input)
        results["components"]["core"] = {"status": "success", "data": core_result}
    except Exception as e:
        results["status"] = "failed"
        results["error"] = str(e)
        return results

    # Optional components (can fail)
    for component in ["analytics", "notifications", "reporting"]:
        try:
            component_result = await process_component(core_result, component)
            results["components"][component] = {"status": "success", "data": component_result}
        except Exception as e:
            results["components"][component] = {"status": "failed", "error": str(e)}

    return results
```

#### Circuit Breaker Pattern

Implement circuit breakers for external dependencies:

```python
# tasks/external_tasks.py
from flux import task
import time
from typing import Dict, Any

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open

    async def call(self, func, *args, **kwargs):
        if self.state == "open":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
            else:
                raise Exception("Circuit breaker is open")

        try:
            result = await func(*args, **kwargs)
            if self.state == "half-open":
                self.state = "closed"
                self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.failure_count >= self.failure_threshold:
                self.state = "open"

            raise

# Global circuit breaker instance
external_api_breaker = CircuitBreaker()

@task
async def call_external_api(data: Dict[str, Any]) -> Dict[str, Any]:
    """Call external API with circuit breaker protection."""
    return await external_api_breaker.call(make_api_call, data)
```

### Performance Optimization

#### Parallel Execution

Use parallel tasks for independent operations:

```python
from flux.tasks import parallel

@workflow
async def optimized_pipeline(ctx: ExecutionContext[Dict[str, Any]]):
    """Pipeline with parallel task execution."""
    # Sequential preprocessing
    validated_data = await validate_input(ctx.input)

    # Parallel processing
    processing_tasks = [
        transform_data(validated_data),
        generate_report(validated_data),
        update_metrics(validated_data)
    ]

    results = await parallel(*processing_tasks)
    transform_result, report_result, metrics_result = results

    # Sequential postprocessing
    final_result = await finalize_results({
        "transformed_data": transform_result,
        "report": report_result,
        "metrics": metrics_result
    })

    return final_result
```

#### Caching Strategies

Implement caching for expensive operations:

```python
@task(cache=True, cache_ttl=3600)  # Cache for 1 hour
async def expensive_computation(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Expensive computation with caching."""
    # Expensive operation here
    result = perform_complex_calculation(input_data)
    return result

@task
async def cached_data_fetch(query: str) -> List[Dict[str, Any]]:
    """Fetch data with manual caching."""
    cache_key = f"data_fetch_{hash(query)}"

    # Check cache first
    cached_result = await get_from_cache(cache_key)
    if cached_result:
        return cached_result

    # Fetch from source
    result = await fetch_from_database(query)

    # Store in cache
    await store_in_cache(cache_key, result, ttl=1800)  # 30 minutes

    return result
```

## Testing Strategies

### Unit Testing

Test individual tasks in isolation:

```python
# tests/test_data_tasks.py
import pytest
from unittest.mock import AsyncMock, patch
from tasks.data_tasks import validate_data_format, clean_data

@pytest.mark.asyncio
async def test_validate_data_format_valid():
    """Test data format validation with valid data."""
    valid_data = [
        {"id": 1, "name": "John", "email": "john@example.com"},
        {"id": 2, "name": "Jane", "email": "jane@example.com"}
    ]

    result = await validate_data_format(valid_data)
    assert result == valid_data

@pytest.mark.asyncio
async def test_validate_data_format_invalid():
    """Test data format validation with invalid data."""
    invalid_data = ["not_a_dict", {"id": 1}]

    with pytest.raises(ValueError, match="Invalid record format"):
        await validate_data_format(invalid_data)

@pytest.mark.asyncio
async def test_clean_data():
    """Test data cleaning functionality."""
    dirty_data = [
        {"id": 1, "name": "John", "email": None},
        {"id": 2, "name": None, "email": "jane@example.com", "phone": "123"}
    ]

    result = await clean_data(dirty_data)

    assert result == [
        {"id": 1, "name": "John"},
        {"id": 2, "email": "jane@example.com", "phone": "123"}
    ]
```

### Integration Testing

Test workflow integration:

```python
# tests/test_integration.py
import pytest
from workflows.data_pipeline import data_pipeline

@pytest.mark.asyncio
async def test_data_pipeline_integration(test_database):
    """Test complete pipeline with test database."""
    # Setup test data
    await test_database.insert_test_data()

    input_config = {
        "source": "test_database",
        "table": "test_users",
        "filters": {"active": True}
    }

    # Run pipeline
    result = data_pipeline.run(input_config)

    # Verify results
    assert result.output["status"] == "completed"
    assert result.output["records_processed"] > 0

    # Verify side effects
    processed_data = await test_database.get_processed_data()
    assert len(processed_data) > 0
```

### Mock External Dependencies

Mock external services for testing:

```python
# tests/test_external_integration.py
import pytest
from unittest.mock import AsyncMock, patch
from workflows.api_workflow import api_processing_workflow

@pytest.mark.asyncio
@patch('tasks.external_tasks.call_external_api')
async def test_api_workflow_with_mock(mock_api_call):
    """Test workflow with mocked external API."""
    # Setup mock
    mock_api_call.return_value = {
        "status": "success",
        "data": {"result": "mocked_data"}
    }

    input_data = {"api_endpoint": "/test", "payload": {"test": "data"}}

    # Run workflow
    result = api_processing_workflow.run(input_data)

    # Verify mock was called
    mock_api_call.assert_called_once()

    # Verify result
    assert result.output["status"] == "completed"
```

## Debugging Workflows

### Local Debugging

Debug workflows locally with detailed logging:

```python
# debug_workflow.py
import logging
import asyncio
from workflows.data_pipeline import data_pipeline

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

async def debug_pipeline():
    """Debug pipeline execution locally."""
    test_input = {
        "source": "test_db",
        "table": "users"
    }

    try:
        # Run with local execution for debugging
        result = data_pipeline.run(test_input)

        print("=== Execution Summary ===")
        print(f"Status: {result.status}")
        print(f"Output: {result.output}")
        print(f"Execution time: {result.execution_time}")

        print("\n=== Task Events ===")
        for event in result.events:
            print(f"{event.timestamp}: {event.type} - {event.task_name}")

    except Exception as e:
        print(f"Execution failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_pipeline())
```

### Remote Debugging

Debug workflows running on remote servers:

```python
# remote_debug.py
import asyncio
from flux.client import FluxClient

async def debug_remote_execution():
    """Debug remote workflow execution."""
    client = FluxClient("http://remote-server:8080")

    # Start execution
    execution_id = await client.execute_workflow_async(
        "data_pipeline",
        {"source": "production_db", "table": "users"}
    )

    print(f"Started execution: {execution_id}")

    # Monitor execution with real-time events
    async for event in client.stream_execution_events(execution_id):
        print(f"[{event.timestamp}] {event.type}: {event.data}")

        if event.type == "execution_completed":
            break
        elif event.type == "task_failed":
            print(f"Task failed: {event.data}")
            # Get detailed error information
            error_details = await client.get_execution_status(execution_id)
            print(f"Error details: {error_details}")

if __name__ == "__main__":
    asyncio.run(debug_remote_execution())
```

## Continuous Integration

### Pre-commit Hooks

Set up pre-commit hooks for code quality:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.1.0
    hooks:
      - id: black
        language_version: python3

  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort
        args: ["--profile", "black"]

  - repo: https://github.com/pycqa/flake8
    rev: 6.0.0
    hooks:
      - id: flake8
        args: ["--max-line-length=88", "--extend-ignore=E203"]

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.0.0
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
```

### GitHub Actions

Automate testing and deployment:

```yaml
# .github/workflows/test.yml
name: Test Workflows

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:13
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: flux_test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
    - uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'

    - name: Install dependencies
      run: |
        pip install -r requirements-dev.txt

    - name: Run tests
      run: |
        pytest tests/ --cov=workflows --cov=tasks

    - name: Upload coverage
      uses: codecov/codecov-action@v3
```

## Next Steps

- Learn about [Testing and Debugging](testing-debugging.md) for advanced testing strategies
- Explore [Deployment Strategies](deployment-strategies.md) for production deployment
- Check out [Performance Optimization](../performance/task-optimization.md) for performance tuning
- Review [Security Considerations](../user-guide/security.md) for secure development practices
