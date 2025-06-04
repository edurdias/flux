# Workflow Patterns

Flux supports various workflow patterns that help you build complex, maintainable applications. This guide explores the most common patterns and when to use them effectively.

## What You'll Learn

This guide covers:
- Sequential workflow patterns
- Parallel execution strategies
- Conditional workflow logic
- Dynamic workflow generation
- Subworkflow composition
- Best practices for each pattern

## Sequential Workflows

Sequential workflows execute tasks one after another, where each task can depend on the results of previous tasks.

### Basic Sequential Pattern

```python
from flux import ExecutionContext, task, workflow

@task
async def fetch_user_data(user_id: str):
    """Fetch user data from database."""
    # Simulate database fetch
    await asyncio.sleep(0.5)
    return {
        "user_id": user_id,
        "name": "John Doe",
        "email": "john@example.com",
        "preferences": {"theme": "dark"}
    }

@task
async def enrich_user_data(user_data: dict):
    """Enrich user data with additional information."""
    user_data["last_login"] = "2025-06-04T10:00:00Z"
    user_data["profile_complete"] = True
    return user_data

@task
async def generate_user_profile(user_data: dict):
    """Generate final user profile."""
    return {
        "profile": user_data,
        "display_name": user_data["name"],
        "summary": f"User {user_data['name']} with {user_data['preferences']['theme']} theme"
    }

@workflow
async def sequential_user_profile(ctx: ExecutionContext[str]):
    """Sequential workflow for user profile generation."""
    user_id = ctx.input

    # Step 1: Fetch user data
    user_data = await fetch_user_data(user_id)

    # Step 2: Enrich the data
    enriched_data = await enrich_user_data(user_data)

    # Step 3: Generate final profile
    profile = await generate_user_profile(enriched_data)

    return profile
```

### Pipeline Pattern with Error Handling

```python
from flux.tasks import pipeline

@task.with_options(retry_max_attempts=2)
async def validate_user_input(user_input: dict):
    """Validate user input with retries."""
    required_fields = ["name", "email", "age"]
    for field in required_fields:
        if field not in user_input:
            raise ValueError(f"Missing required field: {field}")

    if user_input["age"] < 0:
        raise ValueError("Age must be positive")

    return user_input

@task
async def normalize_user_data(user_input: dict):
    """Normalize user data formats."""
    return {
        "name": user_input["name"].title(),
        "email": user_input["email"].lower(),
        "age": int(user_input["age"]),
        "normalized_at": await now()
    }

@task
async def save_user_data(user_data: dict):
    """Save user data to database."""
    # Simulate database save
    await asyncio.sleep(0.3)
    user_data["user_id"] = str(uuid4())
    user_data["created_at"] = await now()
    return user_data

@workflow
async def user_registration_pipeline(ctx: ExecutionContext[dict]):
    """User registration using pipeline pattern."""
    try:
        result = await pipeline(
            validate_user_input(ctx.input),
            normalize_user_data,
            save_user_data
        )
        return {"success": True, "user": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

## Parallel Execution Patterns

Parallel execution allows independent tasks to run simultaneously, improving performance.

### Independent Parallel Tasks

```python
from flux.tasks import parallel

@task
async def fetch_user_profile(user_id: str):
    """Fetch user profile data."""
    await asyncio.sleep(1)  # Simulate API call
    return {"user_id": user_id, "name": "John Doe", "email": "john@example.com"}

@task
async def fetch_user_preferences(user_id: str):
    """Fetch user preferences."""
    await asyncio.sleep(0.8)  # Simulate API call
    return {"user_id": user_id, "theme": "dark", "notifications": True}

@task
async def fetch_user_activity(user_id: str):
    """Fetch user activity history."""
    await asyncio.sleep(1.2)  # Simulate API call
    return {"user_id": user_id, "last_login": "2025-06-04", "total_sessions": 42}

@workflow
async def parallel_user_data_fetch(ctx: ExecutionContext[str]):
    """Fetch all user data in parallel."""
    user_id = ctx.input

    # Execute all fetches in parallel
    results = await parallel(
        fetch_user_profile(user_id),
        fetch_user_preferences(user_id),
        fetch_user_activity(user_id)
    )

    profile, preferences, activity = results

    # Combine results
    return {
        "user_profile": profile,
        "preferences": preferences,
        "activity": activity,
        "combined_at": await now()
    }
```

### Parallel Processing with Different Task Types

```python
@task
async def generate_thumbnail(image_data: dict):
    """Generate thumbnail for image."""
    await asyncio.sleep(2)  # Simulate image processing
    return {
        "image_id": image_data["id"],
        "thumbnail_url": f"thumbnails/{image_data['id']}.jpg",
        "size": "150x150"
    }

@task
async def extract_metadata(image_data: dict):
    """Extract image metadata."""
    await asyncio.sleep(0.5)  # Simulate metadata extraction
    return {
        "image_id": image_data["id"],
        "dimensions": "1920x1080",
        "file_size": "2.5MB",
        "format": "JPEG"
    }

@task
async def scan_for_content(image_data: dict):
    """Scan image for inappropriate content."""
    await asyncio.sleep(1.5)  # Simulate AI scanning
    return {
        "image_id": image_data["id"],
        "safe": True,
        "confidence": 0.95,
        "tags": ["nature", "landscape"]
    }

@workflow
async def image_processing_workflow(ctx: ExecutionContext[dict]):
    """Process uploaded image with parallel operations."""
    image_data = ctx.input

    # Process image in parallel
    results = await parallel(
        generate_thumbnail(image_data),
        extract_metadata(image_data),
        scan_for_content(image_data)
    )

    thumbnail, metadata, content_scan = results

    return {
        "image_id": image_data["id"],
        "thumbnail": thumbnail,
        "metadata": metadata,
        "content_scan": content_scan,
        "processing_status": "completed"
    }
```

## Conditional Workflows

Conditional workflows make decisions based on data or state, creating different execution paths.

### Simple Conditional Logic

```python
@task
async def determine_user_type(user_data: dict):
    """Determine user type based on data."""
    if user_data.get("role") == "admin":
        return "admin"
    elif user_data.get("premium", False):
        return "premium"
    else:
        return "basic"

@task
async def process_admin_user(user_data: dict):
    """Process admin user with full privileges."""
    return {
        **user_data,
        "permissions": ["read", "write", "admin"],
        "dashboard": "admin_dashboard",
        "features": ["all"]
    }

@task
async def process_premium_user(user_data: dict):
    """Process premium user with enhanced features."""
    return {
        **user_data,
        "permissions": ["read", "write"],
        "dashboard": "premium_dashboard",
        "features": ["premium_reports", "advanced_analytics"]
    }

@task
async def process_basic_user(user_data: dict):
    """Process basic user with standard features."""
    return {
        **user_data,
        "permissions": ["read"],
        "dashboard": "basic_dashboard",
        "features": ["basic_reports"]
    }

@workflow
async def conditional_user_processing(ctx: ExecutionContext[dict]):
    """Process user based on their type."""
    user_data = ctx.input

    # Determine user type
    user_type = await determine_user_type(user_data)

    # Process based on type
    if user_type == "admin":
        result = await process_admin_user(user_data)
    elif user_type == "premium":
        result = await process_premium_user(user_data)
    else:
        result = await process_basic_user(user_data)

    return {
        "user_type": user_type,
        "processed_user": result
    }
```

### Complex Conditional with Multiple Paths

```python
@task
async def analyze_order(order_data: dict):
    """Analyze order to determine processing path."""
    total = order_data.get("total", 0)
    country = order_data.get("shipping_country", "")
    express = order_data.get("express_shipping", False)

    return {
        "high_value": total > 1000,
        "international": country.upper() not in ["US", "CA"],
        "express": express,
        "requires_approval": total > 5000
    }

@task
async def standard_processing(order_data: dict):
    """Standard order processing."""
    await asyncio.sleep(1)
    return {**order_data, "processing_type": "standard", "estimated_days": 5}

@task
async def express_processing(order_data: dict):
    """Express order processing."""
    await asyncio.sleep(0.5)
    return {**order_data, "processing_type": "express", "estimated_days": 1}

@task
async def international_processing(order_data: dict):
    """International order processing."""
    await asyncio.sleep(2)
    return {**order_data, "processing_type": "international", "estimated_days": 10}

@task
async def approval_workflow(order_data: dict):
    """High-value order approval workflow."""
    await asyncio.sleep(1.5)
    return {**order_data, "approval_status": "pending", "approver": "manager@company.com"}

@workflow
async def order_processing_workflow(ctx: ExecutionContext[dict]):
    """Complex order processing with multiple conditions."""
    order_data = ctx.input

    # Analyze order
    analysis = await analyze_order(order_data)

    # Handle high-value orders first
    if analysis["requires_approval"]:
        return await approval_workflow(order_data)

    # Determine processing type
    if analysis["express"]:
        result = await express_processing(order_data)
    elif analysis["international"]:
        result = await international_processing(order_data)
    else:
        result = await standard_processing(order_data)

    # Add analysis results
    result["analysis"] = analysis

    return result
```

## Dynamic Workflows

Dynamic workflows generate tasks or change execution paths based on runtime data.

### Dynamic Task Generation

```python
@task
async def process_single_item(item: dict, processor_config: dict):
    """Process a single item with given configuration."""
    item_type = item.get("type", "default")
    processing_rule = processor_config.get(item_type, {})

    await asyncio.sleep(processing_rule.get("delay", 0.5))

    return {
        "item_id": item["id"],
        "original_value": item.get("value", 0),
        "processed_value": item.get("value", 0) * processing_rule.get("multiplier", 1),
        "processor": processor_config.get("name", "default")
    }

@workflow
async def dynamic_batch_processing(ctx: ExecutionContext[dict]):
    """Dynamically process items based on configuration."""
    config = ctx.input
    items = config["items"]
    processor_config = config["processor"]

    # Dynamically create tasks for each item
    processing_tasks = []
    for item in items:
        task_instance = process_single_item(item, processor_config)
        processing_tasks.append(task_instance)

    # Execute all tasks in parallel
    results = await parallel(*processing_tasks)

    return {
        "total_items": len(items),
        "processor_used": processor_config.get("name", "default"),
        "results": results
    }
```

### Dynamic Workflow with Variable Steps

```python
@task
async def setup_environment(config: dict):
    """Setup environment based on configuration."""
    return {
        "environment": config.get("env", "development"),
        "features_enabled": config.get("features", []),
        "setup_complete": True
    }

@task
async def feature_authentication(env_data: dict):
    """Authentication feature setup."""
    await asyncio.sleep(0.5)
    return {**env_data, "authentication": "enabled"}

@task
async def feature_analytics(env_data: dict):
    """Analytics feature setup."""
    await asyncio.sleep(0.8)
    return {**env_data, "analytics": "enabled"}

@task
async def feature_notifications(env_data: dict):
    """Notifications feature setup."""
    await asyncio.sleep(0.3)
    return {**env_data, "notifications": "enabled"}

@task
async def finalize_setup(env_data: dict):
    """Finalize environment setup."""
    return {
        **env_data,
        "setup_finalized": True,
        "ready": True
    }

@workflow
async def dynamic_environment_setup(ctx: ExecutionContext[dict]):
    """Setup environment with dynamic feature enabling."""
    config = ctx.input

    # Always start with environment setup
    env_data = await setup_environment(config)

    # Map features to their setup tasks
    feature_tasks = {
        "auth": feature_authentication,
        "analytics": feature_analytics,
        "notifications": feature_notifications
    }

    # Execute feature setups based on configuration
    enabled_features = config.get("features", [])

    if enabled_features:
        # Create tasks for enabled features
        feature_setup_tasks = []
        for feature in enabled_features:
            if feature in feature_tasks:
                feature_setup_tasks.append(feature_tasks[feature](env_data))

        if feature_setup_tasks:
            # Execute feature setups in parallel
            feature_results = await parallel(*feature_setup_tasks)
            # Use the last result (they all have the same base data)
            env_data = feature_results[-1] if feature_results else env_data

    # Finalize setup
    final_result = await finalize_setup(env_data)

    return final_result
```

## Subworkflow Composition

Break complex workflows into smaller, reusable subworkflows.

### Basic Subworkflow Pattern

```python
@workflow
async def user_validation_subworkflow(ctx: ExecutionContext[dict]):
    """Subworkflow for user validation."""
    user_data = ctx.input

    # Validation steps
    validated = await validate_user_format(user_data)
    enriched = await enrich_user_data(validated)

    return enriched

@workflow
async def notification_subworkflow(ctx: ExecutionContext[dict]):
    """Subworkflow for sending notifications."""
    user_data = ctx.input

    # Notification steps
    email_sent = await send_welcome_email(user_data)
    sms_sent = await send_welcome_sms(user_data)

    return {
        "email_sent": email_sent,
        "sms_sent": sms_sent,
        "notifications_complete": True
    }

@workflow
async def user_registration_main_workflow(ctx: ExecutionContext[dict]):
    """Main workflow composing multiple subworkflows."""
    user_input = ctx.input

    # Execute subworkflows in sequence
    validated_user = await user_validation_subworkflow.call(user_input)
    saved_user = await save_user_to_database(validated_user)
    notification_result = await notification_subworkflow.call(saved_user)

    return {
        "user": saved_user,
        "notifications": notification_result,
        "registration_complete": True
    }
```

### Parallel Subworkflows

```python
@workflow
async def inventory_check_subworkflow(ctx: ExecutionContext[dict]):
    """Check inventory for order items."""
    order_data = ctx.input

    availability = await check_item_availability(order_data["items"])
    reservation = await reserve_items(availability)

    return reservation

@workflow
async def payment_processing_subworkflow(ctx: ExecutionContext[dict]):
    """Process payment for order."""
    order_data = ctx.input

    validation = await validate_payment_method(order_data["payment"])
    charge = await process_payment(validation)

    return charge

@workflow
async def shipping_calculation_subworkflow(ctx: ExecutionContext[dict]):
    """Calculate shipping for order."""
    order_data = ctx.input

    rates = await get_shipping_rates(order_data["address"])
    selected = await select_shipping_option(rates, order_data.get("shipping_preference"))

    return selected

@workflow
async def order_processing_main_workflow(ctx: ExecutionContext[dict]):
    """Main order processing workflow using parallel subworkflows."""
    order_data = ctx.input

    # Execute subworkflows in parallel for efficiency
    results = await parallel(
        inventory_check_subworkflow.call(order_data),
        payment_processing_subworkflow.call(order_data),
        shipping_calculation_subworkflow.call(order_data)
    )

    inventory_result, payment_result, shipping_result = results

    # Combine results and finalize order
    final_order = await finalize_order(
        order_data,
        inventory_result,
        payment_result,
        shipping_result
    )

    return final_order
```

## Mixed Patterns

Combine different patterns for complex business logic.

### Sequential with Conditional Parallel Processing

```python
@workflow
async def data_processing_workflow(ctx: ExecutionContext[dict]):
    """Complex workflow mixing sequential and parallel patterns."""
    config = ctx.input

    # Sequential: Setup and validation
    validated_config = await validate_configuration(config)
    data_sources = await setup_data_sources(validated_config)

    # Conditional: Determine processing strategy
    strategy = await determine_processing_strategy(data_sources)

    if strategy["type"] == "parallel":
        # Parallel: Process multiple data sources simultaneously
        processing_tasks = []
        for source in data_sources["sources"]:
            processing_tasks.append(process_data_source(source, strategy["config"]))

        processed_data = await parallel(*processing_tasks)

        # Sequential: Combine parallel results
        combined = await combine_parallel_results(processed_data)

    else:
        # Sequential: Process sources one by one
        combined = await process_data_sources_sequentially(data_sources, strategy["config"])

    # Sequential: Final processing
    final_result = await generate_final_report(combined)

    return final_result
```

## Best Practices for Workflow Patterns

### 1. Choose the Right Pattern

**Sequential** when:
- Tasks depend on previous results
- Order of execution matters
- Simple, linear processing

**Parallel** when:
- Tasks are independent
- Performance is critical
- I/O-bound operations

**Conditional** when:
- Business logic requires different paths
- Data determines processing approach
- User roles affect functionality

**Dynamic** when:
- Number of tasks varies at runtime
- Configuration drives execution
- Flexible, data-driven processing

### 2. Design for Maintainability

```python
# Good: Clear, focused workflows
@workflow
async def user_onboarding_workflow(ctx: ExecutionContext[dict]):
    """Clear purpose and single responsibility."""
    user_data = ctx.input

    # Clear sequential steps
    validated = await validate_new_user(user_data)
    account = await create_user_account(validated)
    welcome = await send_welcome_package(account)

    return welcome

# Avoid: Complex, multi-purpose workflows
@workflow
async def everything_workflow(ctx: ExecutionContext[dict]):
    """Tries to do too many different things."""
    # This workflow is hard to understand and maintain
    # Better to split into focused workflows
```

### 3. Handle Errors Appropriately

```python
@workflow
async def robust_processing_workflow(ctx: ExecutionContext[dict]):
    """Workflow with proper error handling."""
    try:
        # Critical path with error handling
        validated_data = await validate_input_data(ctx.input)

        # Parallel processing with fallbacks
        results = await parallel(
            process_data_with_fallback(validated_data),
            generate_metadata_with_retry(validated_data)
        )

        return await combine_results(results)

    except ValidationError as e:
        # Handle validation errors specifically
        return {"error": "validation_failed", "details": str(e)}

    except Exception as e:
        # Handle unexpected errors
        await log_error(str(e), ctx.input)
        return {"error": "processing_failed", "message": "Please try again later"}
```

### 4. Monitor and Debug

```python
@task
async def log_workflow_step(step_name: str, data: dict):
    """Log workflow steps for debugging."""
    print(f"Workflow step: {step_name} - Data: {data}")
    return data

@workflow
async def monitored_workflow(ctx: ExecutionContext[dict]):
    """Workflow with monitoring and logging."""
    input_data = ctx.input

    # Log input
    await log_workflow_step("input_received", input_data)

    # Process with logging
    processed = await process_data(input_data)
    await log_workflow_step("data_processed", processed)

    # Validate with logging
    validated = await validate_results(processed)
    await log_workflow_step("validation_complete", validated)

    return validated
```

## Summary

This guide covered the main workflow patterns in Flux:

- **Sequential Workflows**: Step-by-step processing with task dependencies
- **Parallel Execution**: Simultaneous processing of independent tasks
- **Conditional Workflows**: Decision-based execution paths
- **Dynamic Workflows**: Runtime-determined task generation
- **Subworkflow Composition**: Breaking complex workflows into reusable components
- **Mixed Patterns**: Combining patterns for complex business logic

Choose the appropriate pattern based on your specific requirements, and don't hesitate to combine patterns when needed. The key is to maintain clarity, handle errors properly, and design for maintainability.
