# Fault Tolerance

Fault tolerance is a core design principle of Flux, ensuring that workflows can survive and recover from various types of failures including hardware failures, network partitions, and software errors. This comprehensive guide covers state persistence, recovery patterns, and graceful degradation strategies.

## State Persistence

### Automatic State Checkpointing

Flux automatically persists workflow state at key execution points:

```python
from flux import task, workflow, ExecutionContext

@task
async def data_processing_step(data: dict):
    """Task with automatic state persistence"""
    # State is automatically saved before and after task execution
    processed = await complex_data_processing(data)
    return processed

@workflow
async def fault_tolerant_workflow(ctx: ExecutionContext[dict]):
    """Workflow with automatic checkpointing"""

    # State checkpoint 1: Before processing
    step1_result = await data_processing_step(ctx.input)
    # Automatic checkpoint after step1_result

    # State checkpoint 2: Before transformation
    step2_result = await data_transformation_step(step1_result)
    # Automatic checkpoint after step2_result

    # State checkpoint 3: Before finalization
    final_result = await finalization_step(step2_result)
    # Final state saved

    return final_result
```

### Manual State Management

Control state persistence explicitly for critical operations:

```python
@task
async def manual_checkpoint_task(ctx: ExecutionContext, data: dict):
    """Task with manual state management"""

    # Create explicit checkpoint before risky operation
    checkpoint_id = await ctx.create_checkpoint("before_critical_operation")

    try:
        # Perform critical operation
        critical_result = await critical_operation(data)

        # Save intermediate state
        await ctx.save_state("critical_result", critical_result)

        # Continue with additional processing
        final_result = await additional_processing(critical_result)

        # Commit successful state
        await ctx.commit_checkpoint(checkpoint_id)

        return final_result

    except Exception as e:
        # Rollback to checkpoint on failure
        await ctx.rollback_to_checkpoint(checkpoint_id)
        raise
```

### State Versioning and Snapshots

Maintain multiple state versions for advanced recovery scenarios:

```python
@task
async def versioned_state_task(ctx: ExecutionContext, data: dict):
    """Task with versioned state management"""

    # Create versioned snapshots at different stages
    v1_snapshot = await ctx.create_snapshot("initial_processing")

    phase1_result = await phase1_processing(data)
    await ctx.save_snapshot(v1_snapshot, {"phase1": phase1_result})

    v2_snapshot = await ctx.create_snapshot("intermediate_processing")

    phase2_result = await phase2_processing(phase1_result)
    await ctx.save_snapshot(v2_snapshot, {"phase2": phase2_result})

    # Access previous snapshots if needed
    if phase2_result.get("needs_reprocessing"):
        previous_state = await ctx.load_snapshot(v1_snapshot)
        phase1_result = previous_state["phase1"]
        phase2_result = await alternative_phase2_processing(phase1_result)

    return {"phase1": phase1_result, "phase2": phase2_result}
```

### External State Persistence

Integrate with external storage systems for state persistence:

```python
from flux.output_storage import DatabaseStorage, S3Storage

# Configure external storage for workflow state
database_storage = DatabaseStorage(
    connection_string="postgresql://user:pass@host/db",
    table_name="workflow_state"
)

s3_storage = S3Storage(
    bucket_name="workflow-state-bucket",
    region="us-west-2"
)

@task(state_storage=database_storage)
async def database_persisted_task(data: dict):
    """Task with database state persistence"""
    # State automatically persisted to database
    result = await process_data(data)
    return result

@task(state_storage=s3_storage)
async def s3_persisted_task(data: dict):
    """Task with S3 state persistence"""
    # State automatically persisted to S3
    result = await process_large_dataset(data)
    return result
```

## Recovery Patterns

### Automatic Recovery

Flux provides automatic recovery from common failure scenarios:

```python
@workflow
async def auto_recovery_workflow(ctx: ExecutionContext[dict]):
    """Workflow with automatic recovery capabilities"""

    # Recovery from worker failures
    # If a worker crashes, another worker automatically picks up execution
    step1 = await long_running_task(ctx.input)

    # Recovery from network partitions
    # Tasks automatically retry with exponential backoff
    step2 = await network_dependent_task(step1)

    # Recovery from temporary resource unavailability
    # Built-in resource management handles temporary failures
    step3 = await resource_intensive_task(step2)

    return step3

# Configure automatic recovery settings
@task(
    retries=5,
    retry_delay=2.0,
    max_retry_delay=60.0,
    retry_backoff_multiplier=2.0
)
async def resilient_task(data: dict):
    """Task configured for automatic recovery"""
    return await potentially_failing_operation(data)
```

### Custom Recovery Logic

Implement custom recovery strategies for specific failure scenarios:

```python
@task
async def custom_recovery_task(ctx: ExecutionContext, data: dict):
    """Task with custom recovery logic"""

    # Check if this is a recovery attempt
    if ctx.is_recovery_execution:
        print(f"Recovering from previous failure, attempt {ctx.recovery_attempt}")

        # Load recovery context
        recovery_data = await ctx.get_recovery_context()

        if recovery_data.get("failure_type") == "network_timeout":
            # Use cached data for network failures
            return await recover_from_network_failure(data, recovery_data)
        elif recovery_data.get("failure_type") == "resource_exhaustion":
            # Use reduced resource approach
            return await recover_from_resource_failure(data, recovery_data)
        else:
            # Generic recovery approach
            return await generic_recovery(data, recovery_data)

    try:
        # Normal execution path
        return await normal_processing(data)
    except NetworkTimeoutError as e:
        # Save recovery context for network failures
        await ctx.save_recovery_context({
            "failure_type": "network_timeout",
            "error": str(e),
            "partial_data": getattr(e, "partial_data", None)
        })
        raise
    except ResourceExhaustionError as e:
        # Save recovery context for resource failures
        await ctx.save_recovery_context({
            "failure_type": "resource_exhaustion",
            "error": str(e),
            "resource_usage": e.resource_usage
        })
        raise
```

### Multi-Level Recovery

Implement hierarchical recovery strategies:

```python
@workflow
async def multi_level_recovery_workflow(ctx: ExecutionContext[dict]):
    """Workflow with multi-level recovery strategies"""

    recovery_levels = [
        ("primary", primary_processing),
        ("fallback_1", fallback_processing_level_1),
        ("fallback_2", fallback_processing_level_2),
        ("emergency", emergency_processing)
    ]

    last_error = None

    for level_name, processing_func in recovery_levels:
        try:
            print(f"Attempting {level_name} processing")

            # Create checkpoint for each level
            checkpoint = await ctx.create_checkpoint(f"recovery_level_{level_name}")

            result = await processing_func(ctx.input)

            # Success - commit and return
            await ctx.commit_checkpoint(checkpoint)
            return {
                "result": result,
                "recovery_level": level_name,
                "success": True
            }

        except Exception as e:
            print(f"{level_name} processing failed: {e}")
            last_error = e

            # Rollback current level and try next
            await ctx.rollback_to_checkpoint(checkpoint)
            continue

    # All recovery levels failed
    raise Exception(f"All recovery levels failed. Last error: {last_error}")
```

### Cross-Workflow Recovery

Implement recovery patterns that span multiple workflows:

```python
@workflow
async def parent_workflow_with_recovery(ctx: ExecutionContext[dict]):
    """Parent workflow with cross-workflow recovery"""

    try:
        # Execute child workflow
        child_result = await child_workflow(ctx.input)
        return {"child_result": child_result, "status": "success"}

    except Exception as e:
        print(f"Child workflow failed: {e}")

        # Attempt recovery workflow
        try:
            recovery_result = await recovery_workflow({
                "original_input": ctx.input,
                "failure_info": str(e),
                "parent_execution_id": ctx.execution_id
            })

            return {
                "recovery_result": recovery_result,
                "status": "recovered",
                "original_error": str(e)
            }

        except Exception as recovery_error:
            # Recovery also failed
            return {
                "status": "failed",
                "original_error": str(e),
                "recovery_error": str(recovery_error)
            }

@workflow
async def recovery_workflow(ctx: ExecutionContext[dict]):
    """Dedicated recovery workflow"""
    failure_info = ctx.input

    # Analyze failure and attempt appropriate recovery
    if "network" in failure_info["failure_info"].lower():
        return await network_failure_recovery(failure_info)
    elif "timeout" in failure_info["failure_info"].lower():
        return await timeout_failure_recovery(failure_info)
    else:
        return await generic_failure_recovery(failure_info)
```

## Graceful Degradation

### Service-Level Degradation

Implement graceful degradation at the service level:

```python
from enum import Enum

class ServiceLevel(Enum):
    FULL = "full"
    DEGRADED = "degraded"
    MINIMAL = "minimal"
    EMERGENCY = "emergency"

@task
async def adaptive_service_task(data: dict, service_level: ServiceLevel = ServiceLevel.FULL):
    """Task that adapts behavior based on service level"""

    if service_level == ServiceLevel.FULL:
        # Full feature set
        return await full_processing(data)
    elif service_level == ServiceLevel.DEGRADED:
        # Reduced features but good performance
        return await degraded_processing(data)
    elif service_level == ServiceLevel.MINIMAL:
        # Basic functionality only
        return await minimal_processing(data)
    else:  # EMERGENCY
        # Cached/static response
        return await emergency_response(data)

@workflow
async def adaptive_workflow(ctx: ExecutionContext[dict]):
    """Workflow that adapts to system conditions"""

    # Determine current service level
    system_health = await check_system_health()

    if system_health["cpu_usage"] > 90 or system_health["memory_usage"] > 85:
        service_level = ServiceLevel.MINIMAL
    elif system_health["error_rate"] > 5:
        service_level = ServiceLevel.DEGRADED
    elif any(dep["status"] != "healthy" for dep in system_health["dependencies"]):
        service_level = ServiceLevel.DEGRADED
    else:
        service_level = ServiceLevel.FULL

    print(f"Operating at service level: {service_level.value}")

    return await adaptive_service_task(ctx.input, service_level)
```

### Feature-Level Degradation

Degrade specific features while maintaining core functionality:

```python
@workflow
async def feature_degradation_workflow(ctx: ExecutionContext[dict]):
    """Workflow with feature-level degradation"""

    result = {"core_data": None, "features": {}}

    # Core functionality - must succeed
    try:
        result["core_data"] = await core_processing(ctx.input)
    except Exception as e:
        raise Exception(f"Core processing failed: {e}")

    # Enhanced analytics - degrade gracefully
    try:
        analytics = await enhanced_analytics(result["core_data"])
        result["features"]["analytics"] = {"status": "available", "data": analytics}
    except Exception as e:
        # Fall back to basic analytics
        try:
            basic_analytics = await basic_analytics(result["core_data"])
            result["features"]["analytics"] = {
                "status": "degraded",
                "data": basic_analytics,
                "note": "Enhanced analytics unavailable"
            }
        except Exception:
            result["features"]["analytics"] = {"status": "unavailable"}

    # Real-time features - optional
    try:
        realtime_data = await realtime_processing(result["core_data"])
        result["features"]["realtime"] = {"status": "available", "data": realtime_data}
    except Exception:
        # Use cached data
        cached_data = await get_cached_realtime_data(ctx.input.get("id"))
        if cached_data:
            result["features"]["realtime"] = {
                "status": "cached",
                "data": cached_data,
                "note": "Using cached data"
            }
        else:
            result["features"]["realtime"] = {"status": "unavailable"}

    # Notification features - best effort
    try:
        await send_notifications(result["core_data"])
        result["features"]["notifications"] = {"status": "sent"}
    except Exception:
        # Queue for later delivery
        await queue_notifications(result["core_data"])
        result["features"]["notifications"] = {"status": "queued"}

    return result
```

### Data Quality Degradation

Handle degraded data quality gracefully:

```python
@task
async def quality_aware_processing(data: dict):
    """Process data with quality-aware degradation"""

    # Assess data quality
    quality_score = await assess_data_quality(data)

    if quality_score >= 0.9:
        # High quality - full processing
        return await high_precision_processing(data)
    elif quality_score >= 0.7:
        # Medium quality - standard processing with validation
        result = await standard_processing(data)
        result["quality_level"] = "medium"
        result["confidence"] = quality_score
        return result
    elif quality_score >= 0.5:
        # Low quality - simplified processing
        result = await simplified_processing(data)
        result["quality_level"] = "low"
        result["confidence"] = quality_score
        result["warnings"] = ["Data quality below optimal threshold"]
        return result
    else:
        # Very low quality - minimal processing with warnings
        result = await minimal_processing(data)
        result["quality_level"] = "minimal"
        result["confidence"] = quality_score
        result["warnings"] = ["Data quality critically low", "Results may be unreliable"]
        return result
```

## High Availability Patterns

### Active-Passive Failover

Implement active-passive failover for critical workflows:

```python
@workflow
async def ha_workflow_with_failover(ctx: ExecutionContext[dict]):
    """High availability workflow with failover"""

    primary_endpoints = ["https://primary-api.com", "https://primary-backup.com"]
    secondary_endpoints = ["https://secondary-api.com", "https://fallback-api.com"]

    # Try primary endpoints
    for endpoint in primary_endpoints:
        try:
            result = await call_external_service(endpoint, ctx.input)
            return {"result": result, "endpoint_used": endpoint, "tier": "primary"}
        except Exception as e:
            print(f"Primary endpoint {endpoint} failed: {e}")
            continue

    # Fall back to secondary endpoints
    for endpoint in secondary_endpoints:
        try:
            result = await call_external_service(endpoint, ctx.input)
            return {"result": result, "endpoint_used": endpoint, "tier": "secondary"}
        except Exception as e:
            print(f"Secondary endpoint {endpoint} failed: {e}")
            continue

    # All endpoints failed - use cached data
    cached_result = await get_cached_result(ctx.input)
    if cached_result:
        return {
            "result": cached_result,
            "endpoint_used": "cache",
            "tier": "cache",
            "warning": "All endpoints unavailable, using cached data"
        }

    raise Exception("All primary and secondary endpoints failed, no cached data available")
```

### Load Balancing and Distribution

Distribute load across multiple processing nodes:

```python
@task
async def distributed_processing_task(data: dict, node_pool: list):
    """Distribute processing across multiple nodes"""

    # Select node based on current load
    selected_node = await select_optimal_node(node_pool)

    try:
        result = await process_on_node(selected_node, data)
        return {"result": result, "node_used": selected_node}
    except Exception as e:
        # Remove failed node and retry on another
        available_nodes = [n for n in node_pool if n != selected_node]

        if not available_nodes:
            raise Exception("No available nodes for processing")

        backup_node = await select_optimal_node(available_nodes)
        result = await process_on_node(backup_node, data)
        return {
            "result": result,
            "node_used": backup_node,
            "note": f"Failed over from {selected_node}"
        }
```

## Monitoring and Observability

### Health Checks

Implement comprehensive health monitoring:

```python
@task
async def health_check_task():
    """Comprehensive health check"""
    health_status = {
        "timestamp": datetime.utcnow().isoformat(),
        "overall_status": "healthy",
        "components": {}
    }

    # Check database connectivity
    try:
        await check_database_connection()
        health_status["components"]["database"] = {"status": "healthy"}
    except Exception as e:
        health_status["components"]["database"] = {"status": "unhealthy", "error": str(e)}
        health_status["overall_status"] = "degraded"

    # Check external services
    try:
        await check_external_services()
        health_status["components"]["external_services"] = {"status": "healthy"}
    except Exception as e:
        health_status["components"]["external_services"] = {"status": "unhealthy", "error": str(e)}
        health_status["overall_status"] = "degraded"

    # Check resource utilization
    try:
        resources = await check_resource_utilization()
        if resources["cpu_percent"] > 90 or resources["memory_percent"] > 90:
            health_status["components"]["resources"] = {"status": "degraded", "metrics": resources}
            health_status["overall_status"] = "degraded"
        else:
            health_status["components"]["resources"] = {"status": "healthy", "metrics": resources}
    except Exception as e:
        health_status["components"]["resources"] = {"status": "unhealthy", "error": str(e)}
        health_status["overall_status"] = "unhealthy"

    return health_status
```

### Failure Detection and Alerting

Implement proactive failure detection:

```python
@task
async def failure_detection_task(ctx: ExecutionContext):
    """Proactive failure detection and alerting"""

    # Analyze execution patterns
    recent_executions = await get_recent_executions(hours=1)
    failure_rate = calculate_failure_rate(recent_executions)

    # Detect anomalies
    anomalies = []

    if failure_rate > 0.1:  # More than 10% failure rate
        anomalies.append({
            "type": "high_failure_rate",
            "value": failure_rate,
            "threshold": 0.1
        })

    # Check execution times
    avg_execution_time = calculate_avg_execution_time(recent_executions)
    historical_avg = await get_historical_avg_execution_time()

    if avg_execution_time > historical_avg * 2:  # Execution time doubled
        anomalies.append({
            "type": "slow_execution",
            "current": avg_execution_time,
            "historical": historical_avg
        })

    # Send alerts if anomalies detected
    if anomalies:
        await send_alert({
            "execution_id": ctx.execution_id,
            "timestamp": datetime.utcnow().isoformat(),
            "anomalies": anomalies
        })

    return {"anomalies_detected": len(anomalies), "details": anomalies}
```

## Best Practices

### State Management

1. **Minimal State**: Keep persisted state minimal and focused
2. **Immutable Data**: Use immutable data structures for consistency
3. **Regular Checkpoints**: Create checkpoints at logical boundaries
4. **State Validation**: Validate state consistency after recovery

### Recovery Design

1. **Idempotent Operations**: Design operations to be safely retryable
2. **Progressive Recovery**: Implement multiple recovery levels
3. **Recovery Testing**: Regularly test recovery mechanisms
4. **Documentation**: Document recovery procedures and runbooks

### Graceful Degradation

1. **Core vs Optional**: Clearly distinguish core from optional features
2. **Performance Monitoring**: Monitor performance to trigger degradation
3. **User Communication**: Communicate degraded service levels to users
4. **Automatic Recovery**: Implement automatic service level restoration

### High Availability

1. **Redundancy**: Build redundancy at all critical levels
2. **Geographic Distribution**: Distribute across multiple regions/zones
3. **Load Testing**: Regularly test under high load conditions
4. **Disaster Recovery**: Maintain comprehensive disaster recovery plans
