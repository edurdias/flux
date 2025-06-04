# Testing and Debugging

This page covers comprehensive testing strategies and debugging techniques for Flux workflows and the platform itself.

## Overview

Effective testing and debugging are crucial for reliable workflow deployment:

- **Unit Testing**: Test individual tasks and components
- **Integration Testing**: Test workflow interactions and dependencies
- **End-to-End Testing**: Test complete workflow scenarios
- **Performance Testing**: Validate performance characteristics
- **Debugging Tools**: Identify and resolve issues efficiently

## Unit Testing

### Testing Individual Tasks

```python
import pytest
from unittest.mock import Mock, patch
from flux import task, ExecutionContext
from flux.testing import FluxTestCase

class TestDataProcessingTasks(FluxTestCase):
    """Test suite for data processing tasks."""

    def setUp(self):
        """Set up test fixtures."""
        self.context = self.create_test_context()
        self.sample_data = {
            "id": 123,
            "name": "Test User",
            "email": "test@example.com"
        }

    def test_validate_user_data_success(self):
        """Test successful user data validation."""
        from myapp.tasks import validate_user_data

        result = validate_user_data(self.sample_data, self.context)

        self.assertTrue(result["valid"])
        self.assertEqual(result["data"], self.sample_data)
        self.assertIn("validation_timestamp", result)

    def test_validate_user_data_missing_email(self):
        """Test validation failure for missing email."""
        from myapp.tasks import validate_user_data

        invalid_data = {"id": 123, "name": "Test User"}

        with self.assertRaises(ValueError) as cm:
            validate_user_data(invalid_data, self.context)

        self.assertIn("email", str(cm.exception))

    def test_process_user_data_with_mock(self):
        """Test user data processing with mocked dependencies."""
        from myapp.tasks import process_user_data

        with patch('myapp.tasks.external_api_call') as mock_api:
            mock_api.return_value = {"enriched": True}

            result = process_user_data(self.sample_data, self.context)

            # Verify API was called correctly
            mock_api.assert_called_once_with(self.sample_data["id"])

            # Verify result
            self.assertTrue(result["processed"])
            self.assertTrue(result["enriched"])

    def test_task_with_context_logging(self):
        """Test that task properly uses execution context."""
        from myapp.tasks import logging_task

        result = logging_task("test input", self.context)

        # Verify logging calls
        self.assert_log_contains("info", "Processing input")
        self.assert_log_contains("info", "Processing complete")

        # Verify context state
        self.assertEqual(self.context.get("last_input"), "test input")
```

### Testing Task Decorators and Configuration

```python
def test_task_retry_behavior(self):
    """Test task retry configuration."""
    from myapp.tasks import unreliable_task

    with patch('myapp.tasks.external_service_call') as mock_service:
        # Configure mock to fail twice, then succeed
        mock_service.side_effect = [
            Exception("Service unavailable"),
            Exception("Service unavailable"),
            {"success": True}
        ]

        result = unreliable_task(self.context)

        # Verify retries occurred
        self.assertEqual(mock_service.call_count, 3)
        self.assertTrue(result["success"])

def test_task_timeout_behavior(self):
    """Test task timeout configuration."""
    from myapp.tasks import slow_task

    with patch('myapp.tasks.slow_operation') as mock_op:
        # Configure mock to simulate timeout
        mock_op.side_effect = TimeoutError("Operation timed out")

        with self.assertRaises(TimeoutError):
            slow_task(self.context)

def test_task_caching(self):
    """Test task result caching."""
    from myapp.tasks import cached_expensive_task

    with patch('myapp.tasks.expensive_computation') as mock_comp:
        mock_comp.return_value = "computed_result"

        # First call should hit the computation
        result1 = cached_expensive_task("input", self.context)
        self.assertEqual(mock_comp.call_count, 1)

        # Second call should use cache
        result2 = cached_expensive_task("input", self.context)
        self.assertEqual(mock_comp.call_count, 1)  # Still 1

        # Results should be identical
        self.assertEqual(result1, result2)
```

## Integration Testing

### Testing Workflow Composition

```python
class TestWorkflowIntegration(FluxTestCase):
    """Integration tests for complete workflows."""

    def test_data_pipeline_workflow(self):
        """Test complete data processing pipeline."""
        from myapp.workflows import data_pipeline_workflow

        # Prepare test data
        input_data = {
            "source": "test_database",
            "table": "users",
            "filters": {"active": True}
        }

        # Execute workflow
        result = data_pipeline_workflow(input_data, self.context)

        # Verify workflow completion
        self.assertEqual(result["status"], "completed")
        self.assertGreater(result["records_processed"], 0)
        self.assertIn("output_path", result)

        # Verify intermediate state
        self.assertTrue(self.context.has("extraction_complete"))
        self.assertTrue(self.context.has("transformation_complete"))
        self.assertTrue(self.context.has("load_complete"))

    def test_workflow_with_parallel_tasks(self):
        """Test workflow with parallel task execution."""
        from myapp.workflows import parallel_processing_workflow

        items = [f"item_{i}" for i in range(10)]

        start_time = time.time()
        result = parallel_processing_workflow(items, self.context)
        end_time = time.time()

        # Verify all items processed
        self.assertEqual(len(result["results"]), 10)

        # Verify parallel execution was faster than sequential
        # (This assumes each item takes at least 0.1 seconds to process)
        self.assertLess(end_time - start_time, 1.0)  # Should complete in under 1 second

    def test_workflow_error_recovery(self):
        """Test workflow error handling and recovery."""
        from myapp.workflows import resilient_workflow

        # Test with problematic input that causes partial failures
        problematic_input = {
            "items": ["good1", "bad_item", "good2", "invalid_item", "good3"]
        }

        result = resilient_workflow(problematic_input, self.context)

        # Verify partial success
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["successful_items"], 3)
        self.assertEqual(result["failed_items"], 2)

        # Verify error logging
        self.assert_error_count(2)
```

### Testing External Dependencies

```python
class TestExternalIntegrations(FluxTestCase):
    """Test workflows with external dependencies."""

    def test_database_integration(self):
        """Test workflow with real database integration."""
        from myapp.workflows import database_workflow

        # Set up test database
        test_db_url = self.get_test_database_url()
        self.setup_test_data(test_db_url)

        try:
            # Execute workflow
            result = database_workflow(test_db_url, self.context)

            # Verify database operations
            self.verify_test_data_processed(test_db_url)
            self.assertEqual(result["status"], "success")

        finally:
            # Clean up test database
            self.cleanup_test_data(test_db_url)

    def test_api_integration_with_mock_server(self):
        """Test workflow with mocked external API."""
        from myapp.workflows import api_integration_workflow

        with self.mock_http_server() as mock_server:
            # Configure mock responses
            mock_server.expect_request(
                "GET", "/api/data",
                headers={"Authorization": "Bearer test-token"}
            ).respond_with_json({"data": "test_response"})

            mock_server.expect_request(
                "POST", "/api/results"
            ).respond_with_json({"id": "12345"})

            # Execute workflow
            result = api_integration_workflow(
                api_base_url=mock_server.url,
                api_token="test-token",
                context=self.context
            )

            # Verify API interactions
            self.assertEqual(result["data_received"], "test_response")
            self.assertEqual(result["result_id"], "12345")
```

## End-to-End Testing

### Testing Complete Deployment Scenarios

```python
class TestEndToEndScenarios(FluxTestCase):
    """End-to-end tests for complete deployment scenarios."""

    def test_production_workflow_simulation(self):
        """Simulate complete production workflow."""
        from myapp.workflows import production_etl_workflow

        # Use production-like test data
        large_dataset = self.generate_large_test_dataset(1000)

        # Configure production-like environment
        production_config = {
            "parallel_workers": 4,
            "batch_size": 100,
            "timeout": 300,
            "retry_attempts": 3
        }

        # Execute workflow
        result = production_etl_workflow(
            dataset=large_dataset,
            config=production_config,
            context=self.context
        )

        # Verify production requirements
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["records_processed"], 1000)
        self.assertLess(result["execution_time"], 300)  # Within timeout
        self.assertEqual(result["error_count"], 0)

    def test_disaster_recovery_scenario(self):
        """Test disaster recovery and resumption."""
        from myapp.workflows import resumable_workflow

        # Start workflow
        checkpoint_data = {"processed_items": 0, "checkpoint_id": None}

        # Simulate failure midway through
        with patch('myapp.tasks.critical_task') as mock_task:
            mock_task.side_effect = [
                "result1", "result2", Exception("Simulated failure")
            ]

            with self.assertRaises(Exception):
                resumable_workflow(checkpoint_data, self.context)

        # Verify checkpoint was created
        self.assertTrue(self.context.has("checkpoint_data"))
        checkpoint = self.context.get("checkpoint_data")
        self.assertEqual(checkpoint["processed_items"], 2)

        # Resume from checkpoint
        with patch('myapp.tasks.critical_task') as mock_task:
            mock_task.return_value = "result3"

            result = resumable_workflow(checkpoint, self.context)

            # Verify successful resumption
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["total_processed"], 3)
```

## Performance Testing

### Load Testing

```python
class TestPerformance(FluxTestCase):
    """Performance and load testing."""

    def test_workflow_performance_benchmarks(self):
        """Test workflow performance against benchmarks."""
        from myapp.workflows import performance_critical_workflow

        # Define performance benchmarks
        max_execution_time = 10.0  # seconds
        max_memory_usage = 500 * 1024 * 1024  # 500MB

        # Monitor performance
        start_time = time.time()
        start_memory = self.get_memory_usage()

        # Execute workflow
        result = performance_critical_workflow(
            dataset_size=10000,
            context=self.context
        )

        end_time = time.time()
        end_memory = self.get_memory_usage()

        # Verify performance benchmarks
        execution_time = end_time - start_time
        memory_used = end_memory - start_memory

        self.assertLess(execution_time, max_execution_time,
                       f"Execution took {execution_time:.2f}s, expected < {max_execution_time}s")
        self.assertLess(memory_used, max_memory_usage,
                       f"Memory usage {memory_used} bytes, expected < {max_memory_usage}")

    def test_concurrent_workflow_execution(self):
        """Test multiple workflows running concurrently."""
        from myapp.workflows import concurrent_safe_workflow

        import concurrent.futures

        # Execute multiple workflows concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for i in range(10):
                context = self.create_test_context(execution_id=f"test_{i}")
                future = executor.submit(
                    concurrent_safe_workflow,
                    data={"id": i, "batch": "concurrent_test"},
                    context=context
                )
                futures.append(future)

            # Collect results
            results = [f.result() for f in futures]

        # Verify all workflows completed successfully
        self.assertEqual(len(results), 10)
        for i, result in enumerate(results):
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["id"], i)

    def test_memory_leak_detection(self):
        """Test for memory leaks in long-running workflows."""
        from myapp.workflows import iterative_workflow

        initial_memory = self.get_memory_usage()

        # Run workflow multiple times
        for i in range(100):
            result = iterative_workflow(
                iteration=i,
                context=self.create_test_context()
            )
            self.assertEqual(result["status"], "completed")

            # Check memory every 10 iterations
            if i % 10 == 0:
                current_memory = self.get_memory_usage()
                memory_growth = current_memory - initial_memory

                # Allow some memory growth but flag excessive growth
                max_allowed_growth = 50 * 1024 * 1024  # 50MB
                self.assertLess(memory_growth, max_allowed_growth,
                               f"Memory grew by {memory_growth} bytes after {i} iterations")
```

## Debugging Tools and Techniques

### Debug Mode Configuration

```python
# debug_config.py
DEBUG_CONFIG = {
    "log_level": "DEBUG",
    "enable_step_debugging": True,
    "capture_intermediate_state": True,
    "enable_profiling": True,
    "save_execution_trace": True
}

# Usage in workflows
@workflow
def debuggable_workflow(data: dict, context: ExecutionContext):
    """Workflow with debugging capabilities."""

    if context.environment.get("DEBUG_MODE"):
        context.log_debug("Starting debuggable workflow", extra={"input_data": data})

        # Enable step-by-step debugging
        context.set_debug_mode(True)

    # Step 1
    step1_result = debug_step_1(data, context)
    if context.debug_mode:
        context.capture_state("after_step1", step1_result)

    # Step 2
    step2_result = debug_step_2(step1_result, context)
    if context.debug_mode:
        context.capture_state("after_step2", step2_result)

    return {"final_result": step2_result}
```

### Interactive Debugging

```python
def debug_workflow_interactively():
    """Interactive debugging session for workflow development."""
    from flux.debug import FluxDebugger

    debugger = FluxDebugger()

    # Set breakpoints
    debugger.set_breakpoint("myapp.workflows.data_pipeline_workflow", line=25)
    debugger.set_breakpoint("myapp.tasks.process_data", condition="data['size'] > 1000")

    # Start debugging session
    with debugger.session() as session:
        # Execute workflow with debugging
        result = session.execute_workflow(
            "data_pipeline_workflow",
            inputs={"source": "debug_data.json"}
        )

        # Interactive debugging commands available:
        # - session.step_over()
        # - session.step_into()
        # - session.continue_execution()
        # - session.inspect_variable("variable_name")
        # - session.evaluate_expression("context.get('state')")

        print(f"Workflow result: {result}")
```

### Logging and Tracing

```python
# Enhanced logging configuration
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "detailed": {
            "format": "[{asctime}] {levelname} | {name} | {funcName}:{lineno} | {message}",
            "style": "{"
        },
        "json": {
            "()": "flux.logging.JSONFormatter",
            "include_context": True
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "detailed",
            "level": "DEBUG"
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "workflow_debug.log",
            "formatter": "json",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5
        }
    },
    "loggers": {
        "flux": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False
        },
        "myapp": {
            "handlers": ["console", "file"],
            "level": "DEBUG"
        }
    }
}

# Distributed tracing integration
@task
def traced_task(data: dict, context: ExecutionContext):
    """Task with distributed tracing."""
    from flux.tracing import trace_span

    with trace_span("traced_task", context) as span:
        span.set_attribute("input_size", len(str(data)))

        # Your task logic here
        result = process_data(data)

        span.set_attribute("output_size", len(str(result)))
        span.set_status("OK")

        return result
```

### Error Analysis Tools

```python
class WorkflowErrorAnalyzer:
    """Tool for analyzing workflow errors and failures."""

    def __init__(self, execution_id: str):
        self.execution_id = execution_id
        self.flux_client = FluxClient()

    def analyze_execution(self):
        """Analyze a failed execution."""
        execution = self.flux_client.get_execution(self.execution_id)

        analysis = {
            "execution_id": self.execution_id,
            "status": execution.status,
            "error_summary": self._analyze_errors(execution),
            "performance_issues": self._analyze_performance(execution),
            "resource_usage": self._analyze_resources(execution),
            "recommendations": self._generate_recommendations(execution)
        }

        return analysis

    def _analyze_errors(self, execution):
        """Analyze error patterns."""
        errors = execution.get_errors()

        error_patterns = {}
        for error in errors:
            error_type = type(error.exception).__name__
            if error_type not in error_patterns:
                error_patterns[error_type] = []
            error_patterns[error_type].append({
                "task": error.task_name,
                "message": str(error.exception),
                "timestamp": error.timestamp
            })

        return {
            "total_errors": len(errors),
            "error_types": list(error_patterns.keys()),
            "error_patterns": error_patterns,
            "most_common_error": max(error_patterns.keys(),
                                   key=lambda k: len(error_patterns[k])) if error_patterns else None
        }

    def _analyze_performance(self, execution):
        """Analyze performance bottlenecks."""
        tasks = execution.get_tasks()

        slow_tasks = []
        for task in tasks:
            if task.duration > 30:  # Tasks taking more than 30 seconds
                slow_tasks.append({
                    "name": task.name,
                    "duration": task.duration,
                    "memory_peak": task.memory_peak,
                    "cpu_usage": task.cpu_usage
                })

        return {
            "total_duration": execution.duration,
            "slow_tasks": slow_tasks,
            "bottlenecks": self._identify_bottlenecks(tasks)
        }

    def generate_debug_report(self, output_file: str):
        """Generate comprehensive debug report."""
        analysis = self.analyze_execution()

        report = f"""
# Workflow Debug Report
**Execution ID:** {self.execution_id}
**Status:** {analysis['status']}
**Generated:** {datetime.now().isoformat()}

## Error Summary
- Total Errors: {analysis['error_summary']['total_errors']}
- Error Types: {', '.join(analysis['error_summary']['error_types'])}

## Performance Analysis
- Total Duration: {analysis['performance_issues']['total_duration']}s
- Slow Tasks: {len(analysis['performance_issues']['slow_tasks'])}

## Recommendations
{chr(10).join(f"- {rec}" for rec in analysis['recommendations'])}

## Detailed Error Log
{self._format_detailed_errors(analysis['error_summary'])}
        """

        with open(output_file, 'w') as f:
            f.write(report)

        print(f"Debug report saved to {output_file}")

# Usage
analyzer = WorkflowErrorAnalyzer("execution_12345")
analyzer.generate_debug_report("debug_report.md")
```

## Test Data Management

### Test Data Generators

```python
class TestDataGenerator:
    """Generate test data for workflow testing."""

    @staticmethod
    def generate_user_data(count: int = 100):
        """Generate test user data."""
        import random
        from faker import Faker

        fake = Faker()
        users = []

        for i in range(count):
            user = {
                "id": i + 1,
                "name": fake.name(),
                "email": fake.email(),
                "age": random.randint(18, 80),
                "active": random.choice([True, False]),
                "created_at": fake.date_time_this_year().isoformat()
            }
            users.append(user)

        return users

    @staticmethod
    def generate_time_series_data(days: int = 30):
        """Generate time series test data."""
        import pandas as pd

        dates = pd.date_range(
            start=datetime.now() - timedelta(days=days),
            end=datetime.now(),
            freq='H'
        )

        data = []
        for date in dates:
            data.append({
                "timestamp": date.isoformat(),
                "value": random.uniform(0, 100),
                "category": random.choice(["A", "B", "C"])
            })

        return data

    @staticmethod
    def generate_error_prone_data(error_rate: float = 0.1):
        """Generate data that will cause predictable errors."""
        data = TestDataGenerator.generate_user_data(100)

        # Introduce errors based on error_rate
        error_count = int(len(data) * error_rate)
        error_indices = random.sample(range(len(data)), error_count)

        for i in error_indices:
            # Introduce various types of errors
            error_type = random.choice(["missing_email", "invalid_age", "duplicate_id"])

            if error_type == "missing_email":
                del data[i]["email"]
            elif error_type == "invalid_age":
                data[i]["age"] = -1
            elif error_type == "duplicate_id":
                data[i]["id"] = data[0]["id"]  # Duplicate first ID

        return data
```

## Continuous Testing

### Automated Test Execution

```bash
#!/bin/bash
# test_runner.sh - Automated test execution script

set -e

echo "Starting Flux workflow test suite..."

# Set up test environment
export FLUX_ENV=test
export FLUX_LOG_LEVEL=DEBUG

# Run unit tests
echo "Running unit tests..."
python -m pytest tests/unit/ -v --cov=myapp --cov-report=xml

# Run integration tests
echo "Running integration tests..."
python -m pytest tests/integration/ -v --maxfail=1

# Run performance tests
echo "Running performance tests..."
python -m pytest tests/performance/ -v --benchmark-only

# Run end-to-end tests
echo "Running end-to-end tests..."
python -m pytest tests/e2e/ -v --tb=short

# Generate test report
echo "Generating test report..."
python -m pytest tests/ --html=test_report.html --self-contained-html

echo "All tests completed successfully!"
```

### CI/CD Integration

```yaml
# .github/workflows/test.yml
name: Test Flux Workflows

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
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
    - uses: actions/checkout@v2

    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: 3.9

    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install -r requirements-test.txt

    - name: Start Flux server
      run: |
        flux start server --port 8080 --detach
        sleep 10  # Wait for server to start

    - name: Run tests
      run: |
        ./scripts/test_runner.sh
      env:
        FLUX_SERVER: http://localhost:8080
        DATABASE_URL: postgresql://postgres:test@localhost/test

    - name: Upload coverage
      uses: codecov/codecov-action@v1
      with:
        file: ./coverage.xml
```

## See Also

- [Development Workflow](development-workflow.md) - Development best practices
- [Server Architecture](server-architecture.md) - Understanding Flux architecture
- [Monitoring and Observability](monitoring-observability.md) - Production monitoring
- [Troubleshooting](../../reference/troubleshooting/) - Common issues and solutions
