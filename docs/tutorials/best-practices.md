# Best Practices

This guide covers best practices for developing, testing, and deploying production-ready Flux workflows. Following these practices will help you build maintainable, reliable, and scalable workflow systems.

> 📚 **Foundation First:** Make sure you're comfortable with the basics by completing [Your First Workflow](your-first-workflow.md) and [Working with Tasks](working-with-tasks.md) tutorials before applying these advanced practices.

## Workflow Design Principles

### 1. Single Responsibility Principle

Each task should have one clear purpose and responsibility.

```python
# ✅ Good - Each task has a single responsibility
@task
def fetch_user_data(user_id: str) -> dict:
    """Fetch user data from database."""
    return database.get_user(user_id)

@task
def validate_user_data(user_data: dict) -> bool:
    """Validate user data completeness."""
    required_fields = ["id", "email", "name"]
    return all(field in user_data for field in required_fields)

@task
def send_welcome_email(user_data: dict) -> bool:
    """Send welcome email to user."""
    return email_service.send_welcome(user_data["email"])

# ❌ Bad - Task doing too many things
@task
def process_user_signup(user_id: str) -> dict:
    """This task does too many things."""
    # Fetching data
    user_data = database.get_user(user_id)

    # Validation
    if not all(field in user_data for field in ["id", "email", "name"]):
        raise ValueError("Invalid user data")

    # Email sending
    email_service.send_welcome(user_data["email"])

    # Analytics tracking
    analytics.track_signup(user_id)

    # Account setup
    account_service.setup_account(user_id)

    return {"status": "completed"}
```

### 2. Idempotency

Tasks should be idempotent - running them multiple times should produce the same result.

```python
# ✅ Good - Idempotent task
@task
def create_user_directory(user_id: str) -> str:
    """Create user directory if it doesn't exist."""
    directory_path = f"/users/{user_id}"

    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        print(f"Created directory: {directory_path}")
    else:
        print(f"Directory already exists: {directory_path}")

    return directory_path

# ✅ Good - Database operation with upsert
@task
def save_user_preferences(user_id: str, preferences: dict) -> bool:
    """Save user preferences with upsert logic."""
    return database.upsert_user_preferences(user_id, preferences)

# ❌ Bad - Not idempotent
@task
def increment_login_count(user_id: str) -> int:
    """This will give different results on each run."""
    current_count = database.get_login_count(user_id)
    new_count = current_count + 1
    database.set_login_count(user_id, new_count)
    return new_count
```

### 3. Clear Input/Output Contracts

Use type hints and validation to make task contracts explicit.

```python
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class UserProfile:
    user_id: str
    email: str
    name: str
    created_at: datetime
    preferences: Dict[str, Any]

@task
def fetch_user_profile(user_id: str) -> UserProfile:
    """Fetch user profile with strong typing."""
    if not user_id:
        raise ValueError("user_id cannot be empty")

    raw_data = database.get_user(user_id)
    if not raw_data:
        raise ValueError(f"User not found: {user_id}")

    return UserProfile(
        user_id=raw_data["id"],
        email=raw_data["email"],
        name=raw_data["name"],
        created_at=datetime.fromisoformat(raw_data["created_at"]),
        preferences=raw_data.get("preferences", {})
    )

@task
def validate_email_format(email: str) -> bool:
    """Validate email format with clear return type."""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

@task
def batch_process_users(
    user_ids: List[str],
    batch_size: Optional[int] = 100
) -> Dict[str, int]:
    """Process users in batches with clear parameters."""
    if not user_ids:
        return {"processed": 0, "errors": 0}

    if batch_size is None or batch_size <= 0:
        batch_size = 100

    processed = 0
    errors = 0

    for i in range(0, len(user_ids), batch_size):
        batch = user_ids[i:i + batch_size]
        try:
            process_user_batch(batch)
            processed += len(batch)
        except Exception as e:
            print(f"Batch processing error: {e}")
            errors += len(batch)

    return {"processed": processed, "errors": errors}
```

## Error Handling Best Practices

### 1. Fail Fast for Invalid Inputs

Validate inputs early and fail fast with clear error messages.

```python
@task
def process_payment(amount: float, currency: str, payment_method: str) -> dict:
    """Process payment with comprehensive input validation."""

    # Validate amount
    if not isinstance(amount, (int, float)):
        raise TypeError(f"Amount must be numeric, got {type(amount)}")

    if amount <= 0:
        raise ValueError(f"Amount must be positive, got {amount}")

    if amount > 100000:
        raise ValueError(f"Amount exceeds maximum limit: {amount}")

    # Validate currency
    valid_currencies = ["USD", "EUR", "GBP", "JPY"]
    if currency not in valid_currencies:
        raise ValueError(f"Invalid currency '{currency}'. Valid: {valid_currencies}")

    # Validate payment method
    valid_methods = ["credit_card", "debit_card", "paypal", "bank_transfer"]
    if payment_method not in valid_methods:
        raise ValueError(f"Invalid payment method '{payment_method}'. Valid: {valid_methods}")

    # Process payment
    try:
        result = payment_service.charge(amount, currency, payment_method)
        return {
            "status": "success",
            "transaction_id": result["transaction_id"],
            "amount": amount,
            "currency": currency
        }
    except PaymentServiceError as e:
        # Convert service errors to workflow errors
        raise Exception(f"Payment processing failed: {e}")
```

### 2. Graceful Degradation

Design tasks to handle partial failures gracefully.

```python
@task
def send_notifications(user_id: str, notification_types: List[str]) -> dict:
    """Send multiple notifications with graceful failure handling."""

    results = {
        "user_id": user_id,
        "requested": notification_types,
        "successful": [],
        "failed": [],
        "errors": []
    }

    for notification_type in notification_types:
        try:
            send_single_notification(user_id, notification_type)
            results["successful"].append(notification_type)
        except Exception as e:
            results["failed"].append(notification_type)
            results["errors"].append({
                "type": notification_type,
                "error": str(e)
            })
            print(f"Failed to send {notification_type} notification to {user_id}: {e}")

    # Determine overall status
    if not results["successful"]:
        results["status"] = "all_failed"
    elif results["failed"]:
        results["status"] = "partial_success"
    else:
        results["status"] = "success"

    return results

@task
def fetch_data_from_multiple_sources(sources: List[str]) -> dict:
    """Fetch data from multiple sources with fallback logic."""

    data_collected = {}
    errors = {}

    for source in sources:
        try:
            data = fetch_from_source(source)
            data_collected[source] = data
        except Exception as e:
            errors[source] = str(e)
            print(f"Failed to fetch from {source}: {e}")

    # Ensure we have at least some data
    if not data_collected:
        raise Exception(f"Failed to fetch data from all sources: {errors}")

    return {
        "data": data_collected,
        "successful_sources": list(data_collected.keys()),
        "failed_sources": list(errors.keys()),
        "errors": errors
    }
```

### 3. Retry Logic with Backoff

Implement intelligent retry logic for transient failures.

```python
import time
import random
from typing import Callable, Any

@task
def reliable_api_call(
    endpoint: str,
    data: dict,
    max_retries: int = 3,
    base_delay: float = 1.0
) -> dict:
    """Make API call with exponential backoff retry logic."""

    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = api_client.post(endpoint, json=data)
            return {
                "status": "success",
                "data": response.json(),
                "attempts": attempt + 1
            }

        except (ConnectionError, TimeoutError, HTTPError) as e:
            last_error = e

            if attempt == max_retries:
                # Final attempt failed
                break

            # Calculate delay with exponential backoff and jitter
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            print(f"API call attempt {attempt + 1} failed: {e}")
            print(f"Retrying in {delay:.1f} seconds...")
            time.sleep(delay)

        except Exception as e:
            # Non-retryable error
            raise Exception(f"Non-retryable API error: {e}")

    # All retries exhausted
    raise Exception(f"API call failed after {max_retries + 1} attempts. Last error: {last_error}")

def with_retry(max_retries: int = 3, base_delay: float = 1.0):
    """Decorator for adding retry logic to any task."""
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs) -> Any:
            last_error = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e

                    if attempt == max_retries:
                        break

                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    print(f"Task {func.__name__} attempt {attempt + 1} failed: {e}")
                    print(f"Retrying in {delay:.1f} seconds...")
                    time.sleep(delay)

            raise Exception(f"Task {func.__name__} failed after {max_retries + 1} attempts. Last error: {last_error}")

        return wrapper
    return decorator

# Usage example
@task
@with_retry(max_retries=3, base_delay=0.5)
def unreliable_database_operation(query: str) -> list:
    """Database operation that might fail due to connection issues."""
    return database.execute_query(query)
```

## Testing Best Practices

### 1. Unit Test Individual Tasks

Write comprehensive unit tests for task functions.

```python
# test_tasks.py
import pytest
from unittest.mock import patch, MagicMock
from my_workflows import fetch_user_profile, process_payment, send_notifications

def test_fetch_user_profile_success():
    """Test successful user profile fetching."""
    # Arrange
    user_id = "test_user_123"
    mock_user_data = {
        "id": user_id,
        "email": "test@example.com",
        "name": "Test User",
        "created_at": "2024-01-01T00:00:00",
        "preferences": {"theme": "dark"}
    }

    with patch('my_workflows.database.get_user', return_value=mock_user_data):
        # Act
        result = fetch_user_profile(user_id)

        # Assert
        assert result.user_id == user_id
        assert result.email == "test@example.com"
        assert result.name == "Test User"
        assert result.preferences == {"theme": "dark"}

def test_fetch_user_profile_not_found():
    """Test user profile fetching when user doesn't exist."""
    with patch('my_workflows.database.get_user', return_value=None):
        with pytest.raises(ValueError, match="User not found"):
            fetch_user_profile("nonexistent_user")

def test_process_payment_invalid_amount():
    """Test payment processing with invalid amount."""
    with pytest.raises(ValueError, match="Amount must be positive"):
        process_payment(-100, "USD", "credit_card")

    with pytest.raises(ValueError, match="Amount exceeds maximum"):
        process_payment(200000, "USD", "credit_card")

def test_send_notifications_partial_failure():
    """Test notification sending with partial failures."""
    user_id = "test_user"
    notification_types = ["email", "sms", "push"]

    def mock_send_notification(uid, ntype):
        if ntype == "sms":
            raise Exception("SMS service unavailable")
        return True

    with patch('my_workflows.send_single_notification', side_effect=mock_send_notification):
        result = send_notifications(user_id, notification_types)

        assert result["status"] == "partial_success"
        assert "email" in result["successful"]
        assert "push" in result["successful"]
        assert "sms" in result["failed"]
        assert len(result["errors"]) == 1

class TestPaymentProcessing:
    """Test class for payment processing tasks."""

    @pytest.fixture
    def valid_payment_data(self):
        return {
            "amount": 100.00,
            "currency": "USD",
            "payment_method": "credit_card"
        }

    def test_successful_payment(self, valid_payment_data):
        mock_response = {"transaction_id": "txn_123"}

        with patch('my_workflows.payment_service.charge', return_value=mock_response):
            result = process_payment(**valid_payment_data)

            assert result["status"] == "success"
            assert result["transaction_id"] == "txn_123"
            assert result["amount"] == 100.00

    @pytest.mark.parametrize("invalid_amount", [-10, 0, "invalid", None])
    def test_invalid_amounts(self, invalid_amount):
        with pytest.raises((ValueError, TypeError)):
            process_payment(invalid_amount, "USD", "credit_card")
```

### 2. Integration Test Workflows

Test complete workflows with realistic data.

```python
# test_workflows.py
import pytest
from my_workflows import user_onboarding_workflow, order_processing_workflow

@pytest.fixture
def sample_user_data():
    return {
        "name": "John Doe",
        "email": "john.doe@example.com",
        "phone": "+1-555-123-4567",
        "preferences": {"newsletter": True}
    }

@pytest.fixture
def sample_order_data():
    return {
        "order_id": "order_123",
        "customer_id": "customer_456",
        "items": [
            {"product_id": "prod_1", "quantity": 2, "price": 25.00},
            {"product_id": "prod_2", "quantity": 1, "price": 50.00}
        ],
        "shipping_address": {
            "street": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "zip": "12345"
        }
    }

def test_user_onboarding_workflow_success(sample_user_data):
    """Test complete user onboarding workflow."""

    # Mock external dependencies
    with patch('my_workflows.database') as mock_db, \
         patch('my_workflows.email_service') as mock_email, \
         patch('my_workflows.analytics') as mock_analytics:

        mock_db.create_user.return_value = {"user_id": "user_123"}
        mock_email.send_welcome.return_value = True
        mock_analytics.track_signup.return_value = True

        # Execute workflow
        result = user_onboarding_workflow(sample_user_data)

        # Verify workflow completion
        assert result["status"] == "success"
        assert result["user_id"] == "user_123"

        # Verify all steps were called
        mock_db.create_user.assert_called_once()
        mock_email.send_welcome.assert_called_once()
        mock_analytics.track_signup.assert_called_once()

def test_order_processing_workflow_with_payment_failure(sample_order_data):
    """Test order processing when payment fails."""

    with patch('my_workflows.inventory_service') as mock_inventory, \
         patch('my_workflows.payment_service') as mock_payment, \
         patch('my_workflows.notification_service') as mock_notification:

        mock_inventory.reserve_items.return_value = True
        mock_payment.charge.side_effect = Exception("Payment declined")

        # Execute workflow
        result = order_processing_workflow(sample_order_data)

        # Verify failure handling
        assert result["status"] == "failed"
        assert "payment" in result["error"]

        # Verify inventory was released on payment failure
        mock_inventory.release_items.assert_called_once()
        mock_notification.send_payment_failed.assert_called_once()

@pytest.mark.integration
def test_end_to_end_workflow():
    """End-to-end test with real Flux execution."""
    # This test would actually register and run workflows
    # Use this for integration testing with real Flux server
    pass
```

### 3. Performance Testing

Test workflow performance under load.

```python
# test_performance.py
import time
import statistics
import pytest
from concurrent.futures import ThreadPoolExecutor
from my_workflows import data_processing_workflow

def measure_execution_time(func, *args, **kwargs):
    """Measure function execution time."""
    start_time = time.time()
    result = func(*args, **kwargs)
    execution_time = time.time() - start_time
    return result, execution_time

def test_workflow_performance():
    """Test workflow execution time."""
    test_data = generate_test_data(size=1000)

    result, execution_time = measure_execution_time(
        data_processing_workflow,
        test_data
    )

    # Assert performance requirements
    assert execution_time < 10.0, f"Workflow too slow: {execution_time:.2f}s"
    assert result["processed_count"] == 1000

def test_workflow_scalability():
    """Test workflow performance with different data sizes."""
    data_sizes = [100, 500, 1000, 2000]
    execution_times = []

    for size in data_sizes:
        test_data = generate_test_data(size=size)
        _, execution_time = measure_execution_time(
            data_processing_workflow,
            test_data
        )
        execution_times.append(execution_time)
        print(f"Size {size}: {execution_time:.2f}s")

    # Verify roughly linear scaling
    # (This is a simplified check - real tests would be more sophisticated)
    time_per_item = [t/s for t, s in zip(execution_times, data_sizes)]
    avg_time_per_item = statistics.mean(time_per_item)

    for i, time_per_item_value in enumerate(time_per_item):
        # Allow 50% variance from average
        assert abs(time_per_item_value - avg_time_per_item) < avg_time_per_item * 0.5, \
            f"Performance degradation at size {data_sizes[i]}"

def test_concurrent_workflow_execution():
    """Test multiple workflows running concurrently."""
    def run_workflow():
        test_data = generate_test_data(size=100)
        return data_processing_workflow(test_data)

    start_time = time.time()

    # Run 5 workflows concurrently
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(run_workflow) for _ in range(5)]
        results = [future.result() for future in futures]

    total_time = time.time() - start_time

    # Verify all workflows completed successfully
    assert len(results) == 5
    assert all(r["status"] == "success" for r in results)

    # Concurrent execution should be faster than sequential
    assert total_time < 25.0, f"Concurrent execution too slow: {total_time:.2f}s"
```

## Production Deployment Best Practices

### 1. Environment Configuration

Use environment variables and configuration files for different environments.

```python
# config.py
import os
from typing import Optional

class Config:
    """Base configuration class."""

    # Server configuration
    SERVER_HOST = os.getenv("FLUX_SERVER_HOST", "localhost")
    SERVER_PORT = int(os.getenv("FLUX_SERVER_PORT", "8080"))

    # Database configuration
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///flux.db")

    # External service URLs
    API_BASE_URL = os.getenv("API_BASE_URL", "https://api.example.com")
    PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "https://payments.example.com")

    # Security
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-key-change-in-production")
    API_TIMEOUT = int(os.getenv("API_TIMEOUT", "30"))

    # Performance
    MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
    BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def from_environment(cls) -> 'Config':
        """Create config from environment variables."""
        return cls()

class DevelopmentConfig(Config):
    """Development environment configuration."""
    DEBUG = True
    LOG_LEVEL = "DEBUG"
    API_TIMEOUT = 60  # Longer timeout for debugging

class ProductionConfig(Config):
    """Production environment configuration."""
    DEBUG = False
    LOG_LEVEL = "WARNING"

    def __init__(self):
        super().__init__()
        # Validate required production settings
        required_vars = ["SECRET_KEY", "DATABASE_URL", "API_BASE_URL"]
        missing_vars = [var for var in required_vars if not getattr(self, var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {missing_vars}")

def get_config() -> Config:
    """Get configuration based on environment."""
    env = os.getenv("FLASK_ENV", "development").lower()

    if env == "production":
        return ProductionConfig()
    else:
        return DevelopmentConfig()

# Usage in workflows
config = get_config()

@task
def configured_api_call(endpoint: str, data: dict) -> dict:
    """Make API call using configuration."""
    full_url = f"{config.API_BASE_URL}/{endpoint}"

    try:
        response = requests.post(
            full_url,
            json=data,
            timeout=config.API_TIMEOUT,
            headers={"Authorization": f"Bearer {config.SECRET_KEY}"}
        )
        return response.json()
    except requests.TimeoutError:
        raise Exception(f"API call timed out after {config.API_TIMEOUT}s")
```

### 2. Logging and Monitoring

Implement comprehensive logging and monitoring.

```python
# logging_setup.py
import logging
import json
from datetime import datetime
from typing import Dict, Any

class StructuredFormatter(logging.Formatter):
    """Format logs as structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add custom fields
        if hasattr(record, "workflow_id"):
            log_entry["workflow_id"] = record.workflow_id
        if hasattr(record, "task_name"):
            log_entry["task_name"] = record.task_name
        if hasattr(record, "execution_id"):
            log_entry["execution_id"] = record.execution_id

        return json.dumps(log_entry)

def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Set up structured logging."""
    logger = logging.getLogger("flux_workflows")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Console handler with structured format
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(StructuredFormatter())
    logger.addHandler(console_handler)

    # File handler for production
    if config.LOG_LEVEL == "WARNING":  # Production
        file_handler = logging.FileHandler("flux_workflows.log")
        file_handler.setFormatter(StructuredFormatter())
        logger.addHandler(file_handler)

    return logger

# Usage in workflows
logger = setup_logging(config.LOG_LEVEL)

@task
def monitored_task(data: dict, task_context: dict = None) -> dict:
    """Task with comprehensive monitoring."""

    task_id = task_context.get("task_id", "unknown") if task_context else "unknown"
    workflow_id = task_context.get("workflow_id", "unknown") if task_context else "unknown"

    # Create logger with context
    task_logger = logging.LoggerAdapter(logger, {
        "task_name": "monitored_task",
        "task_id": task_id,
        "workflow_id": workflow_id
    })

    task_logger.info("Task started", extra={"input_size": len(data)})
    start_time = time.time()

    try:
        # Task logic
        result = process_data(data)

        execution_time = time.time() - start_time
        task_logger.info(
            "Task completed successfully",
            extra={
                "execution_time": execution_time,
                "output_size": len(result)
            }
        )

        return result

    except Exception as e:
        execution_time = time.time() - start_time
        task_logger.error(
            "Task failed",
            extra={
                "execution_time": execution_time,
                "error": str(e)
            },
            exc_info=True
        )
        raise

# Metrics collection
class MetricsCollector:
    """Collect and report workflow metrics."""

    def __init__(self):
        self.metrics = {
            "task_executions": 0,
            "task_failures": 0,
            "total_execution_time": 0.0,
            "workflow_executions": 0,
            "workflow_failures": 0
        }

    def record_task_execution(self, execution_time: float, success: bool = True):
        """Record task execution metrics."""
        self.metrics["task_executions"] += 1
        self.metrics["total_execution_time"] += execution_time

        if not success:
            self.metrics["task_failures"] += 1

    def record_workflow_execution(self, success: bool = True):
        """Record workflow execution metrics."""
        self.metrics["workflow_executions"] += 1

        if not success:
            self.metrics["workflow_failures"] += 1

    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics."""
        metrics = self.metrics.copy()

        if metrics["task_executions"] > 0:
            metrics["avg_task_execution_time"] = (
                metrics["total_execution_time"] / metrics["task_executions"]
            )
            metrics["task_failure_rate"] = (
                metrics["task_failures"] / metrics["task_executions"]
            )

        if metrics["workflow_executions"] > 0:
            metrics["workflow_failure_rate"] = (
                metrics["workflow_failures"] / metrics["workflow_executions"]
            )

        return metrics

# Global metrics collector
metrics = MetricsCollector()

def with_metrics(func):
    """Decorator to add metrics collection to tasks."""
    def wrapper(*args, **kwargs):
        start_time = time.time()
        success = True

        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            success = False
            raise
        finally:
            execution_time = time.time() - start_time
            metrics.record_task_execution(execution_time, success)

    return wrapper

# Usage
@task
@with_metrics
def metrics_enabled_task(data: dict) -> dict:
    """Task with automatic metrics collection."""
    return process_data(data)
```

### 3. Security Best Practices

Implement proper security measures for production.

```python
# security.py
import os
import hashlib
import hmac
from typing import Optional, Dict, Any
from cryptography.fernet import Fernet

class SecureConfig:
    """Secure configuration management."""

    def __init__(self):
        self.encryption_key = os.getenv("ENCRYPTION_KEY")
        if not self.encryption_key:
            raise ValueError("ENCRYPTION_KEY environment variable required")

        self.fernet = Fernet(self.encryption_key.encode())

    def encrypt_sensitive_data(self, data: str) -> str:
        """Encrypt sensitive data."""
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt_sensitive_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data."""
        return self.fernet.decrypt(encrypted_data.encode()).decode()

secure_config = SecureConfig()

@task
def secure_api_call(endpoint: str, data: dict, api_key_encrypted: str) -> dict:
    """Make secure API call with encrypted credentials."""

    # Decrypt API key
    api_key = secure_config.decrypt_sensitive_data(api_key_encrypted)

    # Validate input data
    validated_data = validate_and_sanitize_input(data)

    try:
        response = requests.post(
            endpoint,
            json=validated_data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=30,
            verify=True  # Always verify SSL certificates
        )

        # Don't log sensitive data
        logger.info(f"API call successful to {endpoint}")

        return response.json()

    except Exception as e:
        # Log error without exposing sensitive data
        logger.error(f"API call failed to {endpoint}: {type(e).__name__}")
        raise
    finally:
        # Clear sensitive data from memory
        api_key = None

def validate_and_sanitize_input(data: dict) -> dict:
    """Validate and sanitize input data."""

    # Check for malicious patterns
    dangerous_patterns = ["<script>", "javascript:", "data:"]

    def check_string_value(value: str) -> str:
        if any(pattern in value.lower() for pattern in dangerous_patterns):
            raise ValueError(f"Potentially malicious input detected")
        return value.strip()

    def sanitize_dict(d: dict) -> dict:
        sanitized = {}
        for key, value in d.items():
            if isinstance(key, str):
                key = check_string_value(key)

            if isinstance(value, str):
                sanitized[key] = check_string_value(value)
            elif isinstance(value, dict):
                sanitized[key] = sanitize_dict(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    check_string_value(item) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                sanitized[key] = value

        return sanitized

    return sanitize_dict(data)

def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify webhook signature for security."""
    expected_signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected_signature}", signature)

@task
def process_webhook(payload: dict, signature: str) -> dict:
    """Process webhook with signature verification."""

    webhook_secret = os.getenv("WEBHOOK_SECRET")
    if not webhook_secret:
        raise ValueError("WEBHOOK_SECRET not configured")

    # Convert payload back to bytes for signature verification
    payload_bytes = json.dumps(payload, sort_keys=True).encode()

    if not verify_webhook_signature(payload_bytes, signature, webhook_secret):
        raise ValueError("Invalid webhook signature")

    # Process verified webhook
    return process_verified_webhook_data(payload)
```

## Development Workflow Best Practices

### 1. Version Control and CI/CD

```yaml
# .github/workflows/flux-workflows.yml
name: Flux Workflows CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:13
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_USER: test
          POSTGRES_DB: flux_test
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
        pip install -r requirements-dev.txt

    - name: Run linting
      run: |
        flake8 workflows/
        black --check workflows/
        mypy workflows/

    - name: Run tests
      run: |
        pytest tests/ --cov=workflows --cov-report=xml
      env:
        DATABASE_URL: postgresql://test:test@localhost/flux_test

    - name: Upload coverage
      uses: codecov/codecov-action@v1

    - name: Test workflow registration
      run: |
        flux start server &
        sleep 5
        flux start worker &
        sleep 5
        flux workflow register workflows/
        flux workflow list
        killall flux

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'

    steps:
    - uses: actions/checkout@v2

    - name: Deploy to production
      run: |
        # Deploy workflows to production environment
        echo "Deploying to production..."
```

### 2. Code Quality Standards

```python
# pyproject.toml
[tool.black]
line-length = 88
target-version = ['py38']
include = '\.pyi?$'
extend-exclude = '''
/(
  # directories
  \.eggs
  | \.git
  | \.hg
  | \.mypy_cache
  | \.tox
  | \.venv
  | build
  | dist
)/
'''

[tool.isort]
profile = "black"
multi_line_output = 3

[tool.mypy]
python_version = "3.8"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short --strict-markers"
markers = [
    "slow: marks tests as slow",
    "integration: marks tests as integration tests",
    "unit: marks tests as unit tests"
]

[tool.coverage.run]
source = ["workflows"]
omit = ["*/tests/*", "*/test_*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError"
]
```

## Documentation Best Practices

### 1. Document Workflows and Tasks

```python
@workflow
def user_data_processing_pipeline(
    user_ids: List[str],
    include_analytics: bool = True,
    batch_size: int = 100
) -> Dict[str, Any]:
    """
    Process user data through validation, enrichment, and analytics pipeline.

    This workflow processes a batch of users through multiple stages:
    1. Fetches user data from the database
    2. Validates data completeness and format
    3. Enriches data with external sources
    4. Optionally runs analytics processing
    5. Stores processed data back to database

    Args:
        user_ids: List of user IDs to process
        include_analytics: Whether to run analytics processing (default: True)
        batch_size: Number of users to process in each batch (default: 100)

    Returns:
        Dict containing:
        - total_processed: Number of users successfully processed
        - total_failed: Number of users that failed processing
        - processing_time: Total time taken for processing
        - batch_results: Detailed results for each batch

    Raises:
        ValueError: If user_ids list is empty
        Exception: If critical processing steps fail

    Example:
        >>> result = user_data_processing_pipeline(
        ...     user_ids=["user1", "user2", "user3"],
        ...     include_analytics=True,
        ...     batch_size=50
        ... )
        >>> print(f"Processed {result['total_processed']} users")
    """

    if not user_ids:
        raise ValueError("user_ids cannot be empty")

    logger.info(f"Starting user data processing for {len(user_ids)} users")
    start_time = time.time()

    # Implementation details...

    return {
        "total_processed": total_processed,
        "total_failed": total_failed,
        "processing_time": time.time() - start_time,
        "batch_results": batch_results
    }

@task
def validate_user_data(user_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate user data for completeness and format correctness.

    Performs comprehensive validation including:
    - Required field presence
    - Email format validation
    - Phone number format validation
    - Date field validation
    - Data type validation

    Args:
        user_data: Dictionary containing user information with keys:
            - user_id (str): Unique user identifier
            - email (str): User email address
            - name (str): User full name
            - phone (str, optional): Phone number
            - birth_date (str, optional): Birth date in ISO format

    Returns:
        Dict containing:
        - is_valid (bool): Whether all validations passed
        - validation_errors (List[str]): List of validation error messages
        - validated_data (Dict): Cleaned and normalized data

    Raises:
        TypeError: If user_data is not a dictionary
        KeyError: If required fields are missing

    Example:
        >>> user = {"user_id": "123", "email": "test@example.com", "name": "John Doe"}
        >>> result = validate_user_data(user)
        >>> if result["is_valid"]:
        ...     print("User data is valid")
    """

    if not isinstance(user_data, dict):
        raise TypeError("user_data must be a dictionary")

    # Implementation details...
```

### 2. Workflow Documentation Templates

Create standardized documentation for workflows:

```markdown
# Workflow: User Onboarding Pipeline

## Overview
This workflow handles the complete user onboarding process from initial signup through account activation.

## Inputs
- `user_data`: User registration information
- `onboarding_type`: Type of onboarding flow ("standard", "premium", "enterprise")
- `send_notifications`: Whether to send welcome notifications

## Outputs
- `user_id`: Created user identifier
- `onboarding_status`: Success/failure status
- `notifications_sent`: List of notifications sent
- `errors`: Any errors encountered during processing

## Process Flow
```mermaid
graph TD
    A[Validate User Data] --> B[Create User Account]
    B --> C[Setup User Preferences]
    C --> D[Send Welcome Email]
    D --> E[Create Analytics Profile]
    E --> F[Complete Onboarding]
```

## Error Handling
- **Validation Errors**: User notified via email with specific issues
- **Account Creation Failures**: Retry up to 3 times with exponential backoff
- **Email Delivery Failures**: Log error but don't fail workflow
- **Analytics Failures**: Continue workflow, log for later processing

## Performance Characteristics
- **Typical Execution Time**: 2-5 seconds
- **Peak Load Capacity**: 1000 users/minute
- **Resource Requirements**: Low CPU, moderate I/O

## Monitoring and Alerts
- **Success Rate**: Should be >99%
- **Execution Time**: Alert if >10 seconds
- **Email Delivery**: Alert if <95% delivery rate

## Testing
- Unit tests: `test_user_onboarding.py`
- Integration tests: `test_onboarding_integration.py`
- Performance tests: `test_onboarding_performance.py`

## Dependencies
- User database
- Email service
- Analytics service
- Notification service

## Related Workflows
- [User Profile Update](user_profile_update.md)
- [Account Activation](account_activation.md)
- [Welcome Series](welcome_series.md)
```

Following these best practices will help you build robust, maintainable, and scalable Flux workflows that perform well in production environments. Remember to adapt these practices to your specific use case and requirements!

## See Also

### Core Concepts
- **[Core Concepts: Workflow Management](../core-concepts/workflow-management.md)** - Advanced workflow patterns
- **[Core Concepts: Task System](../core-concepts/tasks.md)** - Task architecture and composition
- **[Core Concepts: Error Handling](../core-concepts/error-handling.md)** - Advanced error handling patterns

### Practical Guides
- **[Working with Tasks Tutorial](working-with-tasks.md)** - Task design and composition patterns
- **[Parallel Processing Tutorial](parallel-processing.md)** - Performance optimization techniques
- **[Troubleshooting Guide](troubleshooting.md)** - Common issues and solutions

### Reference
- **[CLI Reference](../cli/index.md)** - Production deployment commands
- **[CLI Service Commands](../cli/start.md)** - Starting and managing Flux services
- **[CLI Secrets Commands](../cli/secrets.md)** - Secure secrets management

### Related Topics
- **[FAQ](faq.md)** - Common questions about production usage
- **[Use Cases](../introduction/use-cases.md)** - Real-world production examples

🏆
