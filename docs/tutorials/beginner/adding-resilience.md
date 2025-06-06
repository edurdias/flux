# Adding Resilience to Workflows

Building robust workflows that can handle failures gracefully is crucial for production systems. In this tutorial, you'll learn how to implement comprehensive resilience patterns using Flux's built-in fault tolerance features.

## What You'll Learn

- Implementing retry strategies with exponential backoff
- Using fallback mechanisms for graceful degradation
- Creating rollback handlers for cleanup operations
- Handling different types of errors appropriately
- Building circuit breaker patterns
- Monitoring and alerting for failures

## Prerequisites

- Completed the [Building Your First Data Pipeline](first-data-pipeline.md) tutorial
- Understanding of Python exception handling
- Basic knowledge of distributed system failure modes

## Understanding Failure Patterns

Before implementing resilience, it's important to understand common failure patterns:

### Transient Failures
- Network timeouts
- Temporary service unavailability
- Resource contention
- Rate limiting

### Permanent Failures
- Invalid configuration
- Authentication errors
- Data corruption
- Programming errors

### Cascading Failures
- Downstream service failures
- Resource exhaustion
- Dependency failures

## Building a Resilient Workflow

Let's build a real-world example: an order processing system that integrates with multiple external services.

### Step 1: Define the Core Tasks

Create `resilient_order_processor.py`:

```python
from flux import task, workflow, ExecutionContext
from flux.tasks import parallel
import asyncio
import random
import httpx
from typing import Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class Order:
    id: str
    customer_id: str
    items: list
    total_amount: float
    shipping_address: dict

@dataclass
class ProcessingResult:
    success: bool
    order_id: str
    transaction_id: Optional[str] = None
    tracking_number: Optional[str] = None
    error_message: Optional[str] = None
```

### Step 2: Implement Tasks with Basic Resilience

```python
# Simulate external service calls with potential failures
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    timeout=30
)
async def validate_customer(customer_id: str) -> Dict:
    """Validate customer with retry logic."""
    # Simulate network call that might fail
    await asyncio.sleep(0.5)

    # Simulate random failures (20% failure rate)
    if random.random() < 0.2:
        raise ConnectionError("Customer service temporarily unavailable")

    # Simulate invalid customer (5% rate)
    if random.random() < 0.05:
        raise ValueError(f"Customer {customer_id} not found")

    return {
        "customer_id": customer_id,
        "status": "valid",
        "credit_limit": 10000,
        "verified": True
    }

@task.with_options(
    retry_max_attempts=3,
    retry_delay=2,
    retry_backoff=2,
    timeout=45
)
async def check_inventory(items: list) -> Dict:
    """Check item availability with retry logic."""
    await asyncio.sleep(0.3)

    # Simulate service failures
    if random.random() < 0.15:
        raise TimeoutError("Inventory service timeout")

    availability = {}
    for item in items:
        # Simulate some items being out of stock
        available_qty = max(0, item["quantity"] - random.randint(0, 2))
        availability[item["sku"]] = {
            "requested": item["quantity"],
            "available": available_qty,
            "in_stock": available_qty >= item["quantity"]
        }

    all_available = all(item["in_stock"] for item in availability.values())

    return {
        "all_available": all_available,
        "items": availability,
        "checked_at": datetime.now().isoformat()
    }

@task.with_options(
    retry_max_attempts=2,
    retry_delay=3,
    timeout=60
)
async def process_payment(customer_id: str, amount: float) -> Dict:
    """Process payment with limited retries (financial operations)."""
    await asyncio.sleep(1.0)  # Simulate payment processing time

    # Simulate payment failures (10% rate)
    if random.random() < 0.1:
        raise ConnectionError("Payment gateway unavailable")

    # Simulate declined payments (5% rate)
    if random.random() < 0.05:
        raise ValueError("Payment declined - insufficient funds")

    return {
        "transaction_id": f"txn_{random.randint(100000, 999999)}",
        "amount": amount,
        "status": "completed",
        "processed_at": datetime.now().isoformat()
    }

@task.with_options(
    retry_max_attempts=5,
    retry_delay=1,
    retry_backoff=1.5,
    timeout=30
)
async def reserve_inventory(items: list) -> Dict:
    """Reserve inventory with aggressive retry policy."""
    await asyncio.sleep(0.4)

    # Simulate reservation failures
    if random.random() < 0.1:
        raise ConnectionError("Inventory system unavailable")

    reservations = []
    for item in items:
        reservation_id = f"res_{random.randint(100000, 999999)}"
        reservations.append({
            "sku": item["sku"],
            "quantity": item["quantity"],
            "reservation_id": reservation_id
        })

    return {
        "reservations": reservations,
        "expires_at": (datetime.now() + timedelta(minutes=30)).isoformat()
    }
```

### Step 3: Add Fallback Mechanisms

```python
# Fallback functions for graceful degradation
async def customer_validation_fallback(customer_id: str) -> Dict:
    """Fallback for customer validation - allow with limitations."""
    print(f"WARNING: Using fallback validation for customer {customer_id}")
    return {
        "customer_id": customer_id,
        "status": "unverified",
        "credit_limit": 1000,  # Limited credit for unverified customers
        "verified": False,
        "fallback_used": True
    }

async def inventory_check_fallback(items: list) -> Dict:
    """Fallback for inventory check - proceed with caution."""
    print("WARNING: Using fallback inventory check - assuming availability")
    availability = {}
    for item in items:
        availability[item["sku"]] = {
            "requested": item["quantity"],
            "available": item["quantity"],
            "in_stock": True,
            "fallback_check": True
        }

    return {
        "all_available": True,
        "items": availability,
        "checked_at": datetime.now().isoformat(),
        "fallback_used": True
    }

# Enhanced tasks with fallbacks
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    timeout=30,
    fallback=customer_validation_fallback
)
async def validate_customer_with_fallback(customer_id: str) -> Dict:
    """Customer validation with fallback to limited access."""
    return await validate_customer(customer_id)

@task.with_options(
    retry_max_attempts=3,
    retry_delay=2,
    retry_backoff=2,
    timeout=45,
    fallback=inventory_check_fallback
)
async def check_inventory_with_fallback(items: list) -> Dict:
    """Inventory check with fallback to optimistic availability."""
    return await check_inventory(items)
```

### Step 4: Implement Rollback Operations

```python
# Rollback handlers for cleanup operations
async def rollback_payment(payment_result: Dict) -> None:
    """Rollback payment transaction."""
    if payment_result and "transaction_id" in payment_result:
        print(f"Rolling back payment transaction: {payment_result['transaction_id']}")
        # Simulate refund API call
        await asyncio.sleep(0.5)
        # In reality, this would call the payment gateway's refund API

async def rollback_inventory_reservation(reservation_result: Dict) -> None:
    """Release inventory reservations."""
    if reservation_result and "reservations" in reservation_result:
        print(f"Releasing {len(reservation_result['reservations'])} inventory reservations")
        # Simulate inventory release API calls
        await asyncio.sleep(0.3)

# Enhanced tasks with rollback handlers
@task.with_options(
    retry_max_attempts=2,
    retry_delay=3,
    timeout=60,
    rollback=rollback_payment
)
async def process_payment_with_rollback(customer_id: str, amount: float) -> Dict:
    """Payment processing with automatic rollback on failure."""
    return await process_payment(customer_id, amount)

@task.with_options(
    retry_max_attempts=5,
    retry_delay=1,
    retry_backoff=1.5,
    timeout=30,
    rollback=rollback_inventory_reservation
)
async def reserve_inventory_with_rollback(items: list) -> Dict:
    """Inventory reservation with automatic rollback on failure."""
    return await reserve_inventory(items)
```

### Step 5: Build the Resilient Workflow

```python
@workflow
async def resilient_order_processing(ctx: ExecutionContext[Order]) -> ProcessingResult:
    """
    Process an order with comprehensive error handling and resilience.
    """
    order = ctx.input

    try:
        # Step 1: Parallel validation (fail fast for invalid orders)
        validation_results = await parallel(
            validate_customer_with_fallback(order.customer_id),
            check_inventory_with_fallback(order.items)
        )

        customer_validation = validation_results[0]
        inventory_check = validation_results[1]

        # Check if customer validation failed permanently
        if not customer_validation.get("verified") and not customer_validation.get("fallback_used"):
            return ProcessingResult(
                success=False,
                order_id=order.id,
                error_message="Customer validation failed"
            )

        # Check inventory availability
        if not inventory_check["all_available"]:
            unavailable_items = [
                sku for sku, item in inventory_check["items"].items()
                if not item["in_stock"]
            ]
            return ProcessingResult(
                success=False,
                order_id=order.id,
                error_message=f"Items out of stock: {unavailable_items}"
            )

        # Adjust credit limit for unverified customers
        effective_credit_limit = customer_validation["credit_limit"]
        if order.total_amount > effective_credit_limit:
            return ProcessingResult(
                success=False,
                order_id=order.id,
                error_message=f"Order amount ${order.total_amount} exceeds credit limit ${effective_credit_limit}"
            )

        # Step 2: Reserve inventory and process payment in parallel
        try:
            transaction_results = await parallel(
                reserve_inventory_with_rollback(order.items),
                process_payment_with_rollback(order.customer_id, order.total_amount)
            )

            inventory_reservation = transaction_results[0]
            payment_result = transaction_results[1]

        except Exception as e:
            # If either operation fails, rollbacks will be automatically triggered
            return ProcessingResult(
                success=False,
                order_id=order.id,
                error_message=f"Transaction failed: {str(e)}"
            )

        # Step 3: Create shipping label (with its own resilience)
        try:
            shipping_result = await create_shipping_label_resilient(
                order.shipping_address,
                order.items
            )
        except Exception as e:
            # If shipping fails, we need to manually rollback previous operations
            await rollback_payment(payment_result)
            await rollback_inventory_reservation(inventory_reservation)

            return ProcessingResult(
                success=False,
                order_id=order.id,
                error_message=f"Shipping label creation failed: {str(e)}"
            )

        # Success!
        return ProcessingResult(
            success=True,
            order_id=order.id,
            transaction_id=payment_result["transaction_id"],
            tracking_number=shipping_result["tracking_number"]
        )

    except Exception as e:
        # Catch-all for any unexpected errors
        return ProcessingResult(
            success=False,
            order_id=order.id,
            error_message=f"Unexpected error: {str(e)}"
        )

@task.with_options(
    retry_max_attempts=4,
    retry_delay=2,
    retry_backoff=2,
    timeout=40
)
async def create_shipping_label_resilient(address: dict, items: list) -> Dict:
    """Create shipping label with retry logic."""
    await asyncio.sleep(0.6)

    # Simulate shipping service failures
    if random.random() < 0.15:
        raise ConnectionError("Shipping service unavailable")

    return {
        "tracking_number": f"TRACK_{random.randint(1000000, 9999999)}",
        "label_url": f"https://shipping.example.com/labels/track_{random.randint(1000, 9999)}.pdf",
        "estimated_delivery": (datetime.now() + timedelta(days=3)).isoformat()
    }
```

### Step 6: Implement Circuit Breaker Pattern

For frequently failing services, implement a circuit breaker:

```python
from dataclasses import dataclass
from enum import Enum
import time

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout: int = 60
    expected_exception: type = Exception

    def __post_init__(self):
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        else:  # HALF_OPEN
            return True

    def record_success(self):
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN

# Global circuit breaker for external services
payment_circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)

@task.with_options(
    retry_max_attempts=1,  # Let circuit breaker handle failures
    timeout=60
)
async def process_payment_with_circuit_breaker(customer_id: str, amount: float) -> Dict:
    """Payment processing with circuit breaker pattern."""
    if not payment_circuit_breaker.can_execute():
        raise ConnectionError("Payment service circuit breaker is OPEN")

    try:
        result = await process_payment(customer_id, amount)
        payment_circuit_breaker.record_success()
        return result
    except Exception as e:
        payment_circuit_breaker.record_failure()
        raise
```

### Step 7: Add Comprehensive Error Monitoring

```python
import logging
from typing import Dict, Any
from dataclasses import asdict

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class OrderProcessingMetrics:
    def __init__(self):
        self.success_count = 0
        self.failure_count = 0
        self.fallback_usage = 0
        self.retry_count = 0

    def record_success(self):
        self.success_count += 1
        logger.info("Order processing succeeded", extra={"metric": "success"})

    def record_failure(self, error_type: str, error_message: str):
        self.failure_count += 1
        logger.error("Order processing failed", extra={
            "metric": "failure",
            "error_type": error_type,
            "error_message": error_message
        })

    def record_fallback_usage(self, service: str):
        self.fallback_usage += 1
        logger.warning("Fallback mechanism used", extra={
            "metric": "fallback",
            "service": service
        })

    def get_metrics(self) -> Dict:
        total = self.success_count + self.failure_count
        return {
            "total_orders": total,
            "success_rate": (self.success_count / total * 100) if total > 0 else 0,
            "failure_rate": (self.failure_count / total * 100) if total > 0 else 0,
            "fallback_usage_rate": (self.fallback_usage / total * 100) if total > 0 else 0
        }

# Global metrics instance
metrics = OrderProcessingMetrics()

@workflow
async def monitored_order_processing(ctx: ExecutionContext[Order]) -> ProcessingResult:
    """Order processing with comprehensive monitoring and metrics."""
    order = ctx.input
    start_time = time.time()

    logger.info("Starting order processing", extra={
        "order_id": order.id,
        "customer_id": order.customer_id,
        "amount": order.total_amount
    })

    try:
        result = await resilient_order_processing(ctx)

        processing_time = time.time() - start_time

        if result.success:
            metrics.record_success()
            logger.info("Order processing completed successfully", extra={
                "order_id": order.id,
                "transaction_id": result.transaction_id,
                "processing_time": processing_time
            })
        else:
            metrics.record_failure("business_logic", result.error_message)
            logger.error("Order processing failed", extra={
                "order_id": order.id,
                "error_message": result.error_message,
                "processing_time": processing_time
            })

        return result

    except Exception as e:
        processing_time = time.time() - start_time
        metrics.record_failure("system_error", str(e))
        logger.error("Order processing failed with system error", extra={
            "order_id": order.id,
            "error": str(e),
            "processing_time": processing_time
        })

        return ProcessingResult(
            success=False,
            order_id=order.id,
            error_message=f"System error: {str(e)}"
        )
```

## Testing Resilience

Create comprehensive tests to verify your resilience patterns:

```python
import pytest
from unittest.mock import patch, AsyncMock
from resilient_order_processor import (
    resilient_order_processing, Order, ProcessingResult
)

@pytest.fixture
def sample_order():
    return Order(
        id="order_123",
        customer_id="customer_456",
        items=[
            {"sku": "item1", "quantity": 2, "price": 10.0},
            {"sku": "item2", "quantity": 1, "price": 15.0}
        ],
        total_amount=35.0,
        shipping_address={
            "street": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "zip": "12345"
        }
    )

@pytest.mark.asyncio
async def test_successful_order_processing(sample_order):
    """Test successful order processing."""
    ctx = resilient_order_processing.run(sample_order)
    assert ctx.succeeded
    result = ctx.output
    assert result.success
    assert result.order_id == sample_order.id
    assert result.transaction_id is not None

@pytest.mark.asyncio
async def test_retry_behavior():
    """Test that retries work as expected."""
    with patch('resilient_order_processor.validate_customer') as mock_validate:
        # Fail twice, then succeed
        mock_validate.side_effect = [
            ConnectionError("Service unavailable"),
            ConnectionError("Service unavailable"),
            {"customer_id": "test", "status": "valid", "credit_limit": 10000, "verified": True}
        ]

        result = await validate_customer_with_fallback("test_customer")
        assert result["status"] == "valid"
        assert mock_validate.call_count == 3

@pytest.mark.asyncio
async def test_fallback_behavior():
    """Test that fallbacks are triggered appropriately."""
    with patch('resilient_order_processor.validate_customer') as mock_validate:
        # Always fail
        mock_validate.side_effect = ConnectionError("Service permanently down")

        result = await validate_customer_with_fallback("test_customer")
        assert result["fallback_used"] is True
        assert result["verified"] is False
        assert result["credit_limit"] == 1000  # Limited credit for fallback

@pytest.mark.asyncio
async def test_circuit_breaker():
    """Test circuit breaker functionality."""
    circuit_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1)

    # Record failures to open circuit
    circuit_breaker.record_failure()
    circuit_breaker.record_failure()

    assert circuit_breaker.state == CircuitState.OPEN
    assert not circuit_breaker.can_execute()

    # Wait for recovery
    await asyncio.sleep(1.1)
    assert circuit_breaker.can_execute()
    assert circuit_breaker.state == CircuitState.HALF_OPEN

if __name__ == "__main__":
    pytest.main([__file__])
```

## Running and Testing the Resilient System

### Local Testing

Create a test runner `test_resilience.py`:

```python
import asyncio
import random
from resilient_order_processor import monitored_order_processing, Order, metrics

async def simulate_load_test():
    """Simulate multiple orders to test resilience patterns."""
    orders = []

    # Create test orders
    for i in range(20):
        order = Order(
            id=f"order_{i:03d}",
            customer_id=f"customer_{i % 5}",  # Reuse some customers
            items=[
                {"sku": f"item_{random.randint(1, 10)}", "quantity": random.randint(1, 3), "price": random.uniform(10, 50)}
            ],
            total_amount=random.uniform(20, 200),
            shipping_address={
                "street": f"{random.randint(100, 999)} Test St",
                "city": "Test City",
                "state": "TS",
                "zip": f"{random.randint(10000, 99999)}"
            }
        )
        orders.append(order)

    # Process orders concurrently
    results = []
    for order in orders:
        try:
            ctx = monitored_order_processing.run(order)
            results.append(ctx.output)
        except Exception as e:
            print(f"Failed to process order {order.id}: {e}")

    # Print results
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    print(f"\n=== Load Test Results ===")
    print(f"Total orders: {len(orders)}")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")
    print(f"Success rate: {len(successful)/len(orders)*100:.1f}%")

    print(f"\n=== System Metrics ===")
    system_metrics = metrics.get_metrics()
    for key, value in system_metrics.items():
        print(f"{key}: {value}")

    if failed:
        print(f"\n=== Failure Reasons ===")
        failure_reasons = {}
        for result in failed:
            reason = result.error_message
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

        for reason, count in failure_reasons.items():
            print(f"{reason}: {count}")

if __name__ == "__main__":
    asyncio.run(simulate_load_test())
```

Run the test:

```bash
python test_resilience.py
```

### Distributed Testing

For testing in a distributed environment:

```bash
# Start Flux server and workers
flux start server &
flux start worker &
flux start worker &  # Start multiple workers

# Register the workflow
flux workflow register resilient_order_processor.py

# Run load test
for i in {1..10}; do
  flux workflow run monitored_order_processing "{\"id\":\"order_$i\",\"customer_id\":\"customer_$(($i % 3))\",\"items\":[{\"sku\":\"item1\",\"quantity\":2,\"price\":25.0}],\"total_amount\":50.0,\"shipping_address\":{\"street\":\"123 Test St\",\"city\":\"Test\",\"state\":\"TS\",\"zip\":\"12345\"}}" --mode async &
done

wait  # Wait for all jobs to complete
```

## Production Deployment Considerations

### 1. Health Checks and Monitoring

```python
@task
async def health_check() -> Dict:
    """Comprehensive health check for external dependencies."""
    health_status = {
        "timestamp": datetime.now().isoformat(),
        "status": "healthy",
        "checks": {}
    }

    services = [
        ("customer_service", "https://customer-api.example.com/health"),
        ("inventory_service", "https://inventory-api.example.com/health"),
        ("payment_service", "https://payments-api.example.com/health"),
        ("shipping_service", "https://shipping-api.example.com/health")
    ]

    for service_name, health_url in services:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(health_url, timeout=5.0)
                health_status["checks"][service_name] = {
                    "status": "healthy" if response.status_code == 200 else "unhealthy",
                    "response_time": response.elapsed.total_seconds(),
                    "status_code": response.status_code
                }
        except Exception as e:
            health_status["checks"][service_name] = {
                "status": "unhealthy",
                "error": str(e)
            }
            health_status["status"] = "degraded"

    return health_status
```

### 2. Configuration Management

```python
import os
from dataclasses import dataclass

@dataclass
class ResilienceConfig:
    # Retry configuration
    default_retry_attempts: int = int(os.getenv("DEFAULT_RETRY_ATTEMPTS", "3"))
    default_retry_delay: int = int(os.getenv("DEFAULT_RETRY_DELAY", "1"))
    default_retry_backoff: int = int(os.getenv("DEFAULT_RETRY_BACKOFF", "2"))

    # Timeout configuration
    default_timeout: int = int(os.getenv("DEFAULT_TIMEOUT", "30"))
    payment_timeout: int = int(os.getenv("PAYMENT_TIMEOUT", "60"))

    # Circuit breaker configuration
    circuit_breaker_threshold: int = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5"))
    circuit_breaker_timeout: int = int(os.getenv("CIRCUIT_BREAKER_TIMEOUT", "60"))

    # Monitoring configuration
    enable_detailed_logging: bool = os.getenv("ENABLE_DETAILED_LOGGING", "true").lower() == "true"
    metrics_export_interval: int = int(os.getenv("METRICS_EXPORT_INTERVAL", "60"))

config = ResilienceConfig()
```

### 3. Alerting Integration

```python
async def send_alert(severity: str, message: str, context: Dict = None):
    """Send alerts to monitoring systems."""
    alert_payload = {
        "severity": severity,
        "message": message,
        "timestamp": datetime.now().isoformat(),
        "service": "flux-order-processor",
        "context": context or {}
    }

    # Send to alerting system (e.g., PagerDuty, Slack, etc.)
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://alerts.example.com/webhook",
                json=alert_payload,
                timeout=10.0
            )
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")

# Use in workflow
if metrics.failure_count > 10:  # High failure rate
    await send_alert("high", "Order processing failure rate exceeded threshold", {
        "failure_count": metrics.failure_count,
        "success_rate": metrics.get_metrics()["success_rate"]
    })
```

## Best Practices Summary

1. **Layer Your Resilience**: Use multiple patterns together (retries + fallbacks + circuit breakers)
2. **Fail Fast**: Don't retry on permanent errors (authentication, validation)
3. **Limit Retries**: Especially for financial operations
4. **Monitor Everything**: Track success rates, failure patterns, and fallback usage
5. **Test Failure Scenarios**: Regularly test your resilience patterns
6. **Document Fallback Behavior**: Make it clear what degraded mode looks like
7. **Use Timeouts**: Always set reasonable timeouts for external calls
8. **Implement Health Checks**: Monitor dependency health proactively

## Next Steps

Now that you've learned to build resilient workflows:

1. **Explore Advanced Patterns**: Learn about saga patterns for distributed transactions
2. **Implement Chaos Engineering**: Test your resilience with controlled failures
3. **Build Monitoring Dashboards**: Visualize your system's health and performance
4. **Create Runbooks**: Document incident response procedures

## Related Resources

- [Working with External APIs](external-apis.md) - Integration best practices
- [Error Management](../../user-guide/error-management.md) - Comprehensive error handling guide
- [Fault Tolerance](../../user-guide/fault-tolerance.md) - Advanced fault tolerance patterns
- [Performance Optimization](../advanced/performance-optimization.md) - Optimizing resilient systems
