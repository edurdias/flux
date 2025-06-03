# Working with Tasks

In this tutorial, you'll learn how to design, compose, and reuse tasks effectively. Tasks are the building blocks of Flux workflows, and understanding how to structure them properly is crucial for creating maintainable and scalable workflows.

> 📚 **Prerequisites:** This tutorial builds on [Your First Workflow](your-first-workflow.md). If you haven't completed it yet, start there first.

## What You'll Learn

- How to design reusable tasks
- Task composition patterns
- Parameter handling and type hints
- Task testing strategies
- Common task patterns and best practices

## Prerequisites

- Completed [Your First Workflow](your-first-workflow.md)
- Flux server and worker running
- Basic understanding of Python type hints

## Understanding Tasks

Tasks in Flux are Python functions decorated with `@task`. They represent discrete units of work that can be executed independently and combined into workflows.

### Task Characteristics

✅ **Stateless**: Tasks don't maintain state between executions
✅ **Deterministic**: Same input should produce same output
✅ **Isolated**: Tasks shouldn't depend on external state or side effects
✅ **Composable**: Tasks can be easily combined into workflows

## Step 1: Creating Reusable Tasks

Let's create a set of reusable tasks for data validation and transformation. Create a file called `reusable_tasks.py`:

```python
# reusable_tasks.py
from flux import task, workflow
from typing import List, Dict, Optional, Any
from datetime import datetime
import re

# Data Validation Tasks

@task
def validate_email(email: str) -> bool:
    """Validate email format using regex."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    is_valid = re.match(pattern, email) is not None
    print(f"Email validation for '{email}': {'✓' if is_valid else '✗'}")
    return is_valid

@task
def validate_phone(phone: str) -> bool:
    """Validate phone number format."""
    # Remove common separators
    cleaned = re.sub(r'[-()\s]', '', phone)
    # Check if it's 10-11 digits
    is_valid = cleaned.isdigit() and len(cleaned) in [10, 11]
    print(f"Phone validation for '{phone}': {'✓' if is_valid else '✗'}")
    return is_valid

@task
def validate_age(age: int, min_age: int = 0, max_age: int = 120) -> bool:
    """Validate age within reasonable bounds."""
    is_valid = min_age <= age <= max_age
    print(f"Age validation for {age}: {'✓' if is_valid else '✗'}")
    return is_valid

# Data Transformation Tasks

@task
def normalize_name(name: str) -> str:
    """Normalize name to title case."""
    normalized = name.strip().title()
    print(f"Name normalized: '{name}' → '{normalized}'")
    return normalized

@task
def format_phone(phone: str) -> str:
    """Format phone number consistently."""
    # Remove all non-digits
    digits = re.sub(r'\D', '', phone)

    if len(digits) == 10:
        # Format as (XXX) XXX-XXXX
        formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == '1':
        # Format as +1 (XXX) XXX-XXXX
        formatted = f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    else:
        formatted = phone  # Keep original if can't format

    print(f"Phone formatted: '{phone}' → '{formatted}'")
    return formatted

@task
def calculate_age_group(age: int) -> str:
    """Categorize age into groups."""
    if age < 18:
        group = "minor"
    elif age < 25:
        group = "young_adult"
    elif age < 35:
        group = "adult"
    elif age < 50:
        group = "middle_aged"
    elif age < 65:
        group = "senior"
    else:
        group = "elderly"

    print(f"Age group for {age}: {group}")
    return group

# Data Enrichment Tasks

@task
def lookup_timezone(zip_code: str) -> str:
    """Mock timezone lookup based on zip code."""
    # Simplified timezone mapping (in real app, use a proper service)
    zip_to_timezone = {
        "10001": "America/New_York",    # NYC
        "90210": "America/Los_Angeles", # Beverly Hills
        "60601": "America/Chicago",     # Chicago
        "80202": "America/Denver",      # Denver
    }

    timezone = zip_to_timezone.get(zip_code, "America/New_York")
    print(f"Timezone lookup for {zip_code}: {timezone}")
    return timezone

@task
def enrich_location_data(city: str, state: str, zip_code: str) -> Dict[str, Any]:
    """Enrich location with additional metadata."""
    # Mock location enrichment
    location_data = {
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "timezone": lookup_timezone(zip_code),
        "region": "North" if state in ["NY", "MA", "CT"] else "South",
        "enriched_at": datetime.now().isoformat()
    }

    print(f"Location enriched: {city}, {state}")
    return location_data

# Composite Tasks (Tasks that use other tasks)

@task
def validate_person_data(person: Dict[str, Any]) -> Dict[str, Any]:
    """Validate all aspects of person data."""
    validation_results = {
        "email_valid": validate_email(person.get("email", "")),
        "phone_valid": validate_phone(person.get("phone", "")),
        "age_valid": validate_age(person.get("age", 0)),
        "person_id": person.get("id"),
        "validated_at": datetime.now().isoformat()
    }

    is_valid = all([
        validation_results["email_valid"],
        validation_results["phone_valid"],
        validation_results["age_valid"]
    ])

    validation_results["overall_valid"] = is_valid
    print(f"Person {person.get('id')} validation: {'✓' if is_valid else '✗'}")

    return validation_results

@task
def transform_person_data(person: Dict[str, Any]) -> Dict[str, Any]:
    """Transform and normalize person data."""
    transformed = {
        "id": person.get("id"),
        "name": normalize_name(person.get("name", "")),
        "email": person.get("email", "").lower().strip(),
        "phone": format_phone(person.get("phone", "")),
        "age": person.get("age"),
        "age_group": calculate_age_group(person.get("age", 0)),
        "location": enrich_location_data(
            person.get("city", ""),
            person.get("state", ""),
            person.get("zip_code", "")
        ),
        "transformed_at": datetime.now().isoformat()
    }

    print(f"Person {person.get('id')} transformed")
    return transformed
```

## Step 2: Task Composition Patterns

Now let's create workflows that demonstrate different task composition patterns:

```python
# Add to reusable_tasks.py

# Pattern 1: Sequential Processing
@workflow
def process_single_person(person_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single person through validation and transformation."""
    print(f"Processing person: {person_data.get('name')}")

    # Step 1: Validate
    validation = validate_person_data(person_data)

    # Step 2: Transform (only if valid)
    if validation["overall_valid"]:
        transformed = transform_person_data(person_data)
        result = {
            "validation": validation,
            "transformed_data": transformed,
            "status": "success"
        }
    else:
        result = {
            "validation": validation,
            "status": "validation_failed",
            "errors": "Person data failed validation"
        }

    return result

# Pattern 2: Batch Processing
@workflow
def process_person_batch(people: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Process a batch of people."""
    print(f"Processing batch of {len(people)} people")

    results = []
    valid_count = 0
    invalid_count = 0

    for person in people:
        result = process_single_person(person)
        results.append(result)

        if result["status"] == "success":
            valid_count += 1
        else:
            invalid_count += 1

    batch_summary = {
        "total_processed": len(people),
        "valid_records": valid_count,
        "invalid_records": invalid_count,
        "success_rate": (valid_count / len(people)) * 100 if people else 0,
        "results": results,
        "processed_at": datetime.now().isoformat()
    }

    return batch_summary

# Pattern 3: Conditional Processing
@workflow
def smart_person_processing(person_data: Dict[str, Any], strict_mode: bool = True) -> Dict[str, Any]:
    """Intelligently process person based on data quality."""
    print(f"Smart processing for: {person_data.get('name')} (strict: {strict_mode})")

    # Always validate first
    validation = validate_person_data(person_data)

    if validation["overall_valid"]:
        # Full processing for valid data
        transformed = transform_person_data(person_data)
        return {
            "status": "fully_processed",
            "validation": validation,
            "data": transformed
        }
    elif not strict_mode:
        # Partial processing in non-strict mode
        # Only transform data that can be safely processed
        partial_transform = {
            "id": person_data.get("id"),
            "name": normalize_name(person_data.get("name", "")),
            "processing_mode": "partial",
            "processed_at": datetime.now().isoformat()
        }
        return {
            "status": "partially_processed",
            "validation": validation,
            "data": partial_transform
        }
    else:
        # Reject in strict mode
        return {
            "status": "rejected",
            "validation": validation,
            "reason": "Failed validation in strict mode"
        }
```

## Step 3: Testing Your Tasks

Create a test file to verify your tasks work correctly:

```python
# test_tasks.py
from reusable_tasks import *

# Test data
sample_people = [
    {
        "id": 1,
        "name": "john DOE",
        "email": "john.doe@example.com",
        "phone": "555-123-4567",
        "age": 30,
        "city": "New York",
        "state": "NY",
        "zip_code": "10001"
    },
    {
        "id": 2,
        "name": "jane smith",
        "email": "invalid-email",
        "phone": "555-987-6543",
        "age": 25,
        "city": "Los Angeles",
        "state": "CA",
        "zip_code": "90210"
    },
    {
        "id": 3,
        "name": "bob johnson",
        "email": "bob@company.org",
        "phone": "(312) 555-0123",
        "age": 150,  # Invalid age
        "city": "Chicago",
        "state": "IL",
        "zip_code": "60601"
    }
]

@workflow
def test_individual_tasks():
    """Test individual task functions."""
    print("=== Testing Individual Tasks ===")

    # Test validation tasks
    print("\n--- Validation Tests ---")
    email_test = validate_email("test@example.com")
    phone_test = validate_phone("(555) 123-4567")
    age_test = validate_age(25)

    # Test transformation tasks
    print("\n--- Transformation Tests ---")
    name_test = normalize_name("john DOE")
    phone_format_test = format_phone("5551234567")
    age_group_test = calculate_age_group(30)

    return {
        "validation_tests": {
            "email": email_test,
            "phone": phone_test,
            "age": age_test
        },
        "transformation_tests": {
            "name": name_test,
            "phone_format": phone_format_test,
            "age_group": age_group_test
        }
    }

@workflow
def test_composite_workflows():
    """Test composite workflows with sample data."""
    print("=== Testing Composite Workflows ===")

    # Test single person processing
    print("\n--- Single Person Processing ---")
    single_result = process_single_person(sample_people[0])

    # Test batch processing
    print("\n--- Batch Processing ---")
    batch_result = process_person_batch(sample_people)

    # Test smart processing
    print("\n--- Smart Processing (Strict) ---")
    smart_strict = smart_person_processing(sample_people[1], strict_mode=True)

    print("\n--- Smart Processing (Lenient) ---")
    smart_lenient = smart_person_processing(sample_people[1], strict_mode=False)

    return {
        "single_person": single_result,
        "batch_processing": batch_result,
        "smart_strict": smart_strict,
        "smart_lenient": smart_lenient
    }
```

## Step 4: Register and Run Your Tasks

Register your workflows:

```bash
flux workflow register reusable_tasks.py
flux workflow register test_tasks.py
```

Test individual tasks:

```bash
flux workflow run test_individual_tasks
```

Test composite workflows:

```bash
flux workflow run test_composite_workflows
```

## Step 5: Advanced Task Patterns

Here are some advanced patterns for working with tasks:

### Error-Resistant Tasks

```python
@task
def safe_division(a: float, b: float, default: float = 0.0) -> float:
    """Perform division with safe error handling."""
    try:
        if b == 0:
            print(f"Division by zero, returning default: {default}")
            return default
        result = a / b
        print(f"Division successful: {a}/{b} = {result}")
        return result
    except Exception as e:
        print(f"Division error: {e}, returning default: {default}")
        return default

@task
def safe_api_call(url: str, timeout: int = 30) -> Dict[str, Any]:
    """Make an API call with error handling."""
    try:
        # Simulate API call
        print(f"Making API call to: {url}")
        # In real implementation, use requests.get(url, timeout=timeout)
        return {
            "status": "success",
            "data": {"message": "API call successful"},
            "url": url
        }
    except Exception as e:
        print(f"API call failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "url": url
        }
```

### Configurable Tasks

```python
@task
def configurable_processor(
    data: List[Dict[str, Any]],
    config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Process data based on configuration."""

    batch_size = config.get("batch_size", 10)
    validate_email = config.get("validate_email", True)
    transform_names = config.get("transform_names", True)

    print(f"Processing {len(data)} records with config: {config}")

    processed = []
    for i in range(0, len(data), batch_size):
        batch = data[i:i + batch_size]

        for item in batch:
            processed_item = item.copy()

            if validate_email and "email" in item:
                processed_item["email_valid"] = validate_email(item["email"])

            if transform_names and "name" in item:
                processed_item["name"] = normalize_name(item["name"])

            processed.append(processed_item)

    return processed
```

## Best Practices for Task Design

### ✅ Do's

1. **Keep tasks focused**: Each task should have a single responsibility
2. **Use type hints**: Make inputs and outputs clear
3. **Add logging**: Include print statements for monitoring
4. **Handle errors gracefully**: Return sensible defaults when possible
5. **Make tasks testable**: Design for easy unit testing
6. **Use descriptive names**: Task names should clearly indicate purpose

### ❌ Don'ts

1. **Don't access global state**: Tasks should be self-contained
2. **Don't perform side effects**: Avoid file I/O, database calls in task logic when possible
3. **Don't make tasks too complex**: Break complex logic into multiple tasks
4. **Don't ignore error cases**: Always handle potential failures
5. **Don't hardcode values**: Use parameters for configuration

## Performance Tips

### Efficient Task Design

```python
@task
def efficient_data_processing(large_dataset: List[Dict], chunk_size: int = 1000) -> Dict:
    """Process large datasets efficiently."""
    total_records = len(large_dataset)
    processed_chunks = 0

    results = []

    # Process in chunks to manage memory
    for i in range(0, total_records, chunk_size):
        chunk = large_dataset[i:i + chunk_size]

        # Process chunk
        chunk_results = []
        for record in chunk:
            # Simulate processing
            processed_record = {
                "id": record.get("id"),
                "processed": True,
                "chunk": processed_chunks
            }
            chunk_results.append(processed_record)

        results.extend(chunk_results)
        processed_chunks += 1

        # Progress reporting
        progress = ((i + len(chunk)) / total_records) * 100
        print(f"Progress: {progress:.1f}% ({i + len(chunk)}/{total_records})")

    return {
        "total_processed": len(results),
        "chunks_processed": processed_chunks,
        "results": results
    }
```

## What You've Learned

✅ **Task Design Principles**: Single responsibility, stateless, composable
✅ **Task Composition**: Sequential, batch, and conditional patterns
✅ **Error Handling**: Safe task execution with graceful degradation
✅ **Testing Strategies**: How to test tasks and workflows
✅ **Performance Patterns**: Efficient data processing techniques
✅ **Best Practices**: Do's and don'ts for task development

## What's Next?

Now that you understand task design, explore these advanced topics:

1. **[Parallel Processing](parallel-processing.md)** - Speed up workflows with parallelism
2. **[Best Practices](best-practices.md)** - Production-ready workflow patterns
3. **[Troubleshooting Guide](troubleshooting.md)** - Solutions to common task issues

## Related Concepts

### Core Concepts
- **[Task System](../core-concepts/tasks.md)** - Deep dive into task architecture
- **[Error Handling](../core-concepts/error-handling.md)** - Advanced error handling patterns
- **[Basic Concepts: Tasks](../getting-started/basic_concepts.md#tasks)** - Fundamental task concepts

### CLI Commands
- **[Workflow Commands](../cli/workflow.md)** - Running workflows with tasks
- **[Secrets Management](../cli/secrets.md)** - Using secrets in tasks

## Troubleshooting

### Common Task Issues

**Problem**: Task execution fails silently
**Solution**: Add logging and error handling to identify issues
- See: [Error Handling](../core-concepts/error-handling.md#task-level-error-handling)

**Problem**: Tasks take too long to execute
**Solution**: Break complex tasks into smaller, focused tasks
- See: [Best Practices](best-practices.md#task-design-principles)

**Problem**: Data not flowing between tasks correctly
**Solution**: Check parameter types and return values match expectations
- See: [Basic Concepts: Tasks](../getting-started/basic_concepts.md#tasks)

### Getting Help

- **[Troubleshooting Guide](troubleshooting.md)** - Comprehensive troubleshooting
- **[Best Practices](best-practices.md)** - Task design best practices
- **[Core Concepts: Tasks](../core-concepts/tasks.md)** - Advanced task concepts
- **[FAQ](faq.md)** - Common questions about tasks

## See Also

- **[Your First Workflow](your-first-workflow.md)** - Previous tutorial
- **[Parallel Processing](parallel-processing.md)** - Next tutorial
- **[CLI: Workflow Commands](../cli/workflow.md)** - Commands for running task-based workflows
- **[Core Concepts: Task Composition](../core-concepts/tasks.md#task-composition)** - Advanced composition patterns

Excellent work! You now understand how to create robust, reusable tasks that form the foundation of reliable workflows. 🏗️
