# Error Management

Effective error handling is crucial for building robust, production-ready workflows. Flux provides comprehensive error management capabilities including exception handling patterns, retry strategies, fallback mechanisms, and rollback procedures.

## Exception Handling Patterns

### Basic Exception Handling

Handle exceptions gracefully within tasks and workflows:

```python
from flux import task, workflow, ExecutionContext
from flux.errors import TaskFailedException, WorkflowFailedException

@task
async def risky_operation(data: str):
    """Task that might fail"""
    try:
        # Simulate operation that might fail
        if "error" in data.lower():
            raise ValueError(f"Invalid data: {data}")

        return f"Processed: {data}"
    except ValueError as e:
        # Log the error
        print(f"Data validation error: {e}")
        # Re-raise as a task-specific exception
        raise TaskFailedException(f"Data validation failed: {e}")

@workflow
async def error_handling_workflow(ctx: ExecutionContext[str]):
    try:
        result = await risky_operation(ctx.input)
        return {"success": True, "result": result}
    except TaskFailedException as e:
        return {"success": False, "error": str(e)}
```

### Typed Exception Handling

Use specific exception types for different error scenarios:

```python
class DataValidationError(Exception):
    """Raised when data validation fails"""
    pass

class ExternalServiceError(Exception):
    """Raised when external service calls fail"""
    pass

class ConfigurationError(Exception):
    """Raised when configuration is invalid"""
    pass

@task
async def comprehensive_error_handler(data: dict):
    """Task with comprehensive error handling"""
    try:
        # Validate configuration
        if not data.get("config"):
            raise ConfigurationError("Missing required configuration")

        # Validate data
        if not data.get("payload"):
            raise DataValidationError("Missing required payload")

        # Call external service
        response = await external_service_call(data["payload"])
        if response.status_code != 200:
            raise ExternalServiceError(f"Service returned {response.status_code}")

        return response.json()

    except ConfigurationError as e:
        return {"error": "configuration", "message": str(e)}
    except DataValidationError as e:
        return {"error": "validation", "message": str(e)}
    except ExternalServiceError as e:
        return {"error": "service", "message": str(e)}
    except Exception as e:
        return {"error": "unknown", "message": str(e)}
```

### Context-Aware Error Handling

Use execution context to make error handling decisions:

```python
@task
async def context_aware_error_handler(ctx: ExecutionContext, data: dict):
    """Error handling that considers execution context"""
    try:
        return await process_data(data)
    except Exception as e:
        # Use context information for error handling
        error_info = {
            "execution_id": ctx.execution_id,
            "task_name": ctx.current_task_name,
            "retry_count": ctx.retry_count,
            "error": str(e),
            "error_type": type(e).__name__
        }

        # Different handling based on retry count
        if ctx.retry_count < 2:
            # Log and re-raise for retry
            print(f"Attempt {ctx.retry_count + 1} failed, will retry: {error_info}")
            raise
        else:
            # Max retries reached, return error state
            print(f"Max retries reached: {error_info}")
            return {"status": "failed", "error": error_info}
```

## Retry Strategies

### Basic Retry Configuration

Configure automatic retries for transient failures:

```python
@task(retries=3, retry_delay=2.0)
async def retriable_task(data: str):
    """Task with automatic retry configuration"""
    # Simulate network call that might fail
    response = await external_api_call(data)

    if response.status_code == 500:
        # Server error - should retry
        raise Exception("Server error, will retry")
    elif response.status_code == 400:
        # Client error - should not retry
        raise ValueError("Invalid request, won't retry")

    return response.json()
```

### Exponential Backoff

Implement exponential backoff for retry delays:

```python
import asyncio
import random

@task(retries=5)
async def exponential_backoff_task(data: str, base_delay: float = 1.0):
    """Task with exponential backoff retry strategy"""
    try:
        return await potentially_failing_operation(data)
    except Exception as e:
        # Calculate exponential backoff delay
        retry_count = await get_current_retry_count()
        delay = base_delay * (2 ** retry_count) + random.uniform(0, 1)

        print(f"Retry {retry_count + 1} after {delay:.2f}s delay")
        await asyncio.sleep(delay)
        raise  # Re-raise for retry
```

### Conditional Retry Logic

Implement custom retry logic based on error types:

```python
@task
async def conditional_retry_task(data: dict):
    """Task with conditional retry logic"""
    max_retries = 3
    retry_count = 0

    while retry_count <= max_retries:
        try:
            return await process_with_external_dependency(data)
        except ConnectionError as e:
            # Network issues - should retry
            if retry_count < max_retries:
                retry_count += 1
                delay = min(2 ** retry_count, 60)  # Cap at 60 seconds
                print(f"Connection error, retrying in {delay}s: {e}")
                await asyncio.sleep(delay)
                continue
            else:
                raise Exception(f"Max retries exceeded for connection error: {e}")
        except ValueError as e:
            # Data validation error - should not retry
            raise Exception(f"Data validation error, not retrying: {e}")
        except Exception as e:
            # Unknown error - limited retries
            if retry_count < 1:  # Only retry once for unknown errors
                retry_count += 1
                await asyncio.sleep(5)
                continue
            else:
                raise Exception(f"Unknown error after retry: {e}")
```

### Circuit Breaker Pattern

Implement circuit breaker pattern to prevent cascade failures:

```python
import time
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED

    async def call(self, func, *args, **kwargs):
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
            else:
                raise Exception("Circuit breaker is OPEN")

        try:
            result = await func(*args, **kwargs)
            # Reset on success
            self.failure_count = 0
            self.state = CircuitState.CLOSED
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN

            raise

# Global circuit breaker instance
external_service_breaker = CircuitBreaker()

@task
async def circuit_breaker_task(data: dict):
    """Task protected by circuit breaker"""
    try:
        return await external_service_breaker.call(
            external_service_call,
            data
        )
    except Exception as e:
        return {"error": "circuit_breaker", "message": str(e)}
```

## Fallback Mechanisms

### Simple Fallback

Provide alternative processing when primary operations fail:

```python
@task
async def primary_processor(data: dict):
    """Primary processing logic"""
    return await advanced_processing(data)

@task
async def fallback_processor(data: dict):
    """Fallback processing logic"""
    return await simple_processing(data)

@task
async def processor_with_fallback(data: dict):
    """Task with fallback processing"""
    try:
        # Try primary processing
        return await primary_processor(data)
    except Exception as e:
        print(f"Primary processing failed: {e}, using fallback")
        # Fall back to simpler processing
        return await fallback_processor(data)
```

### Multi-Level Fallbacks

Implement multiple fallback levels for increased resilience:

```python
@task
async def multi_level_fallback_task(data: dict):
    """Task with multiple fallback levels"""
    fallback_methods = [
        ("primary", primary_processing),
        ("secondary", secondary_processing),
        ("basic", basic_processing),
        ("minimal", minimal_processing)
    ]

    last_error = None

    for method_name, method in fallback_methods:
        try:
            print(f"Attempting {method_name} processing")
            result = await method(data)
            result["processing_method"] = method_name
            return result
        except Exception as e:
            print(f"{method_name} processing failed: {e}")
            last_error = e
            continue

    # All fallbacks failed
    raise Exception(f"All processing methods failed. Last error: {last_error}")
```

### Service Fallback Pattern

Fallback to alternative services when primary service is unavailable:

```python
@task
async def service_fallback_task(request_data: dict):
    """Task with service fallback capabilities"""
    services = [
        {"name": "primary", "url": "https://api.primary.com", "timeout": 5},
        {"name": "secondary", "url": "https://api.secondary.com", "timeout": 10},
        {"name": "cache", "url": "local_cache", "timeout": 1}
    ]

    for service in services:
        try:
            if service["name"] == "cache":
                # Try local cache as last resort
                result = await get_from_cache(request_data)
            else:
                # Try external service
                result = await call_external_service(
                    service["url"],
                    request_data,
                    timeout=service["timeout"]
                )

            return {
                "data": result,
                "service_used": service["name"],
                "fallback_level": services.index(service)
            }

        except Exception as e:
            print(f"Service {service['name']} failed: {e}")
            continue

    raise Exception("All services failed")
```

## Rollback Procedures

### State-Based Rollback

Implement rollback procedures that restore previous state:

```python
@task
async def stateful_operation_with_rollback(data: dict, state_manager):
    """Task with automatic rollback capability"""
    # Save current state
    checkpoint = await state_manager.create_checkpoint()

    try:
        # Perform operations
        result1 = await operation_step_1(data)
        await state_manager.update_state("step1_complete", result1)

        result2 = await operation_step_2(result1)
        await state_manager.update_state("step2_complete", result2)

        result3 = await operation_step_3(result2)
        await state_manager.commit_state("final_state", result3)

        return result3

    except Exception as e:
        print(f"Operation failed, rolling back: {e}")
        # Rollback to checkpoint
        await state_manager.rollback_to_checkpoint(checkpoint)
        raise
```

### Compensating Actions

Implement compensating actions for irreversible operations:

```python
@task
async def compensating_transaction_task(order_data: dict):
    """Task with compensating actions for rollback"""
    compensation_actions = []

    try:
        # Step 1: Reserve inventory
        reservation_id = await reserve_inventory(order_data["items"])
        compensation_actions.append(("release_inventory", reservation_id))

        # Step 2: Charge payment
        payment_id = await charge_payment(order_data["payment"])
        compensation_actions.append(("refund_payment", payment_id))

        # Step 3: Create order
        order_id = await create_order(order_data)
        compensation_actions.append(("cancel_order", order_id))

        # Step 4: Send confirmation
        await send_confirmation(order_data["customer"], order_id)

        return {"order_id": order_id, "status": "completed"}

    except Exception as e:
        print(f"Transaction failed, executing compensation: {e}")

        # Execute compensation actions in reverse order
        for action_type, action_id in reversed(compensation_actions):
            try:
                if action_type == "release_inventory":
                    await release_inventory_reservation(action_id)
                elif action_type == "refund_payment":
                    await refund_payment(action_id)
                elif action_type == "cancel_order":
                    await cancel_order(action_id)
            except Exception as comp_error:
                print(f"Compensation action {action_type} failed: {comp_error}")

        raise Exception(f"Transaction failed and compensated: {e}")
```

### Workflow-Level Rollback

Implement rollback procedures at the workflow level:

```python
@workflow
async def rollback_aware_workflow(ctx: ExecutionContext[dict]):
    """Workflow with comprehensive rollback capabilities"""
    rollback_stack = []

    try:
        # Phase 1: Data preparation
        prepared_data = await prepare_data(ctx.input)
        rollback_stack.append(("cleanup_prepared_data", prepared_data["temp_files"]))

        # Phase 2: External system updates
        external_refs = await update_external_systems(prepared_data)
        rollback_stack.append(("revert_external_updates", external_refs))

        # Phase 3: Database updates
        db_transaction_id = await update_database(prepared_data)
        rollback_stack.append(("rollback_database", db_transaction_id))

        # Phase 4: Finalization
        result = await finalize_workflow(prepared_data, external_refs)

        return result

    except Exception as e:
        print(f"Workflow failed, executing rollback: {e}")

        # Execute rollback actions
        for action_type, action_data in reversed(rollback_stack):
            try:
                if action_type == "cleanup_prepared_data":
                    await cleanup_temp_files(action_data)
                elif action_type == "revert_external_updates":
                    await revert_external_system_updates(action_data)
                elif action_type == "rollback_database":
                    await rollback_database_transaction(action_data)
            except Exception as rollback_error:
                # Log rollback failures but continue
                print(f"Rollback action {action_type} failed: {rollback_error}")

        # Return error state instead of raising
        return {
            "status": "failed",
            "error": str(e),
            "rollback_completed": True
        }
```

## Error Recovery Patterns

### Graceful Degradation

Design workflows to continue operating with reduced functionality:

```python
@workflow
async def graceful_degradation_workflow(ctx: ExecutionContext[dict]):
    """Workflow that degrades gracefully on errors"""
    results = {"status": "completed", "components": {}}

    # Core functionality (must succeed)
    try:
        core_result = await core_processing(ctx.input)
        results["components"]["core"] = {"status": "success", "data": core_result}
    except Exception as e:
        # Core failure is fatal
        return {"status": "failed", "error": f"Core processing failed: {e}"}

    # Enhanced functionality (optional)
    try:
        enhanced_result = await enhanced_processing(core_result)
        results["components"]["enhanced"] = {"status": "success", "data": enhanced_result}
    except Exception as e:
        # Enhanced failure is non-fatal
        results["components"]["enhanced"] = {"status": "failed", "error": str(e)}
        results["status"] = "partial"

    # Analytics functionality (optional)
    try:
        analytics_result = await analytics_processing(core_result)
        results["components"]["analytics"] = {"status": "success", "data": analytics_result}
    except Exception as e:
        # Analytics failure is non-fatal
        results["components"]["analytics"] = {"status": "failed", "error": str(e)}
        results["status"] = "partial"

    return results
```

### Progressive Recovery

Implement progressive recovery strategies:

```python
@task
async def progressive_recovery_task(data: dict, recovery_level: int = 0):
    """Task that attempts progressive recovery"""
    recovery_strategies = [
        ("full_processing", full_processing),
        ("reduced_processing", reduced_processing),
        ("minimal_processing", minimal_processing),
        ("cached_result", get_cached_result)
    ]

    for level in range(recovery_level, len(recovery_strategies)):
        strategy_name, strategy_func = recovery_strategies[level]

        try:
            print(f"Attempting recovery level {level}: {strategy_name}")
            result = await strategy_func(data)
            return {
                "data": result,
                "recovery_level": level,
                "strategy": strategy_name
            }
        except Exception as e:
            print(f"Recovery level {level} failed: {e}")
            continue

    raise Exception("All recovery strategies failed")
```

## Best Practices

### Error Handling Design

1. **Fail Fast**: Validate inputs early and fail fast for invalid data
2. **Error Context**: Include relevant context in error messages
3. **Error Classification**: Distinguish between retryable and non-retryable errors
4. **Logging**: Log errors with sufficient detail for debugging

### Retry Strategy Guidelines

1. **Exponential Backoff**: Use exponential backoff with jitter for external services
2. **Retry Limits**: Always set reasonable retry limits
3. **Error Types**: Only retry transient errors, not permanent failures
4. **Circuit Breakers**: Use circuit breakers for external dependencies

### Fallback Design

1. **Graceful Degradation**: Design fallbacks that provide reduced but acceptable functionality
2. **Service Diversity**: Use diverse fallback services to avoid common failure modes
3. **Performance**: Ensure fallbacks have acceptable performance characteristics
4. **Testing**: Regularly test fallback mechanisms

### Rollback Procedures

1. **Idempotency**: Make rollback operations idempotent
2. **Compensation**: Design compensating actions for irreversible operations
3. **Ordering**: Execute rollback actions in reverse order of operations
4. **Monitoring**: Monitor rollback operations for success/failure
