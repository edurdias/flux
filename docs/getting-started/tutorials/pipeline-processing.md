# Pipeline Processing

Pipeline processing is a powerful pattern for building workflows that transform data through a series of sequential steps. Each step in the pipeline receives the output of the previous step as input, creating a chain of transformations. This tutorial will show you how to build efficient data processing pipelines using Flux.

## What You'll Learn

By the end of this tutorial, you'll understand:
- How to create data processing pipelines using the `pipeline` built-in task
- When to use pipelines vs other workflow patterns
- How to handle errors and validation in pipeline stages
- Best practices for designing maintainable pipelines

## Prerequisites

- Complete the [Simple Workflow](simple-workflow.md) tutorial
- Understanding of data transformation concepts
- Basic knowledge of functional programming patterns

## Understanding Pipeline Processing

A pipeline transforms data through a series of stages:

```
Input → Stage 1 → Stage 2 → Stage 3 → Output
```

Each stage:
- Receives input from the previous stage
- Performs a specific transformation
- Passes its output to the next stage

## Basic Pipeline Example

Here's a simple data processing pipeline:

```python
from flux import ExecutionContext, task, workflow
from flux.tasks import pipeline
import re
import json

@task
async def extract_text(raw_data: str):
    """Extract and clean text from raw input."""
    # Remove HTML tags and extra whitespace
    cleaned = re.sub(r'<[^>]+>', '', raw_data)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

@task
async def tokenize_text(text: str):
    """Split text into tokens."""
    # Simple tokenization
    tokens = text.lower().split()
    # Remove punctuation
    tokens = [re.sub(r'[^\w]', '', token) for token in tokens]
    # Filter out empty tokens
    tokens = [token for token in tokens if token]
    return tokens

@task
async def count_words(tokens: list):
    """Count word frequency."""
    word_count = {}
    for token in tokens:
        word_count[token] = word_count.get(token, 0) + 1
    return word_count

@task
async def format_results(word_count: dict):
    """Format the final results."""
    total_words = sum(word_count.values())
    unique_words = len(word_count)

    # Get top 5 most common words
    top_words = sorted(word_count.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "total_words": total_words,
        "unique_words": unique_words,
        "top_words": top_words,
        "word_count": word_count
    }

@workflow
async def text_analysis_pipeline(ctx: ExecutionContext[str]):
    """Process text through a pipeline of transformations."""
    raw_text = ctx.input

    # Execute pipeline stages sequentially
    result = await pipeline(
        extract_text(raw_text),
        tokenize_text,
        count_words,
        format_results
    )

    return result

# Example usage
if __name__ == "__main__":
    sample_text = """
    <h1>Welcome to Flux</h1>
    <p>Flux is a powerful workflow orchestration engine.
    It makes building distributed applications easy and reliable.
    Flux provides excellent developer experience.</p>
    """

    result = text_analysis_pipeline.run(sample_text)
    print(json.dumps(result.output, indent=2))
```

## Advanced Pipeline with Validation

Add validation and error handling between pipeline stages:

```python
from flux import ExecutionContext, task, workflow
from flux.tasks import pipeline
from typing import Dict, List, Any
import asyncio

@task
async def validate_input(data: Any):
    """Validate input data structure."""
    if not isinstance(data, dict):
        raise ValueError("Input must be a dictionary")

    required_fields = ["name", "email", "age"]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")

    return data

@task
async def normalize_email(user_data: Dict[str, Any]):
    """Normalize email address."""
    email = user_data["email"].lower().strip()

    # Basic email validation
    if "@" not in email or "." not in email.split("@")[1]:
        raise ValueError(f"Invalid email format: {email}")

    user_data["email"] = email
    user_data["email_domain"] = email.split("@")[1]
    return user_data

@task
async def categorize_age(user_data: Dict[str, Any]):
    """Add age category based on age."""
    age = user_data["age"]

    if not isinstance(age, int) or age < 0:
        raise ValueError(f"Invalid age: {age}")

    if age < 18:
        category = "minor"
    elif age < 65:
        category = "adult"
    else:
        category = "senior"

    user_data["age_category"] = category
    return user_data

@task
async def enrich_profile(user_data: Dict[str, Any]):
    """Enrich user profile with additional data."""
    # Simulate external API call
    await asyncio.sleep(0.5)

    domain = user_data["email_domain"]

    # Add company info based on email domain
    company_map = {
        "gmail.com": "Personal",
        "company.com": "ACME Corp",
        "university.edu": "Education"
    }

    user_data["company"] = company_map.get(domain, "Unknown")
    user_data["profile_complete"] = True
    user_data["processed_at"] = "2025-06-04T10:00:00Z"

    return user_data

@task
async def generate_summary(user_data: Dict[str, Any]):
    """Generate a user profile summary."""
    name = user_data["name"]
    age_category = user_data["age_category"]
    company = user_data["company"]

    summary = f"{name} is a {age_category} associated with {company}"

    return {
        "user_data": user_data,
        "summary": summary
    }

@workflow
async def user_profile_pipeline(ctx: ExecutionContext[dict]):
    """Process user data through validation and enrichment pipeline."""
    raw_user_data = ctx.input

    try:
        result = await pipeline(
            validate_input(raw_user_data),
            normalize_email,
            categorize_age,
            enrich_profile,
            generate_summary
        )

        return {
            "success": True,
            "result": result
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "input": raw_user_data
        }

# Example usage
if __name__ == "__main__":
    test_users = [
        {"name": "John Doe", "email": "JOHN@COMPANY.COM", "age": 30},
        {"name": "Jane Smith", "email": "jane@gmail.com", "age": 25},
        {"name": "Bob Wilson", "email": "bob@university.edu", "age": 45},
        {"name": "Invalid User", "email": "invalid-email", "age": -5}  # This will fail
    ]

    for user in test_users:
        print(f"\nProcessing: {user['name']}")
        result = user_profile_pipeline.run(user)
        if result.output["success"]:
            print(f"Success: {result.output['result']['summary']}")
        else:
            print(f"Error: {result.output['error']}")
```

## Conditional Pipeline Processing

Sometimes you need to modify the pipeline flow based on data:

```python
from flux import ExecutionContext, task, workflow
from flux.tasks import pipeline

@task
async def analyze_file_type(file_info: dict):
    """Determine file type and processing strategy."""
    filename = file_info["filename"]

    if filename.endswith(('.jpg', '.png', '.gif')):
        file_type = "image"
    elif filename.endswith(('.mp4', '.avi', '.mov')):
        file_type = "video"
    elif filename.endswith(('.txt', '.md', '.doc')):
        file_type = "text"
    else:
        file_type = "unknown"

    file_info["type"] = file_type
    return file_info

@task
async def process_image(file_info: dict):
    """Process image files."""
    if file_info["type"] != "image":
        return file_info  # Skip processing

    # Simulate image processing
    await asyncio.sleep(1)
    file_info["processed"] = True
    file_info["thumbnail_created"] = True
    file_info["dimensions"] = "1920x1080"
    return file_info

@task
async def process_video(file_info: dict):
    """Process video files."""
    if file_info["type"] != "video":
        return file_info  # Skip processing

    # Simulate video processing
    await asyncio.sleep(2)
    file_info["processed"] = True
    file_info["compressed"] = True
    file_info["duration"] = "00:02:30"
    return file_info

@task
async def process_text(file_info: dict):
    """Process text files."""
    if file_info["type"] != "text":
        return file_info  # Skip processing

    # Simulate text processing
    await asyncio.sleep(0.5)
    file_info["processed"] = True
    file_info["word_count"] = 150
    file_info["indexed"] = True
    return file_info

@task
async def finalize_processing(file_info: dict):
    """Finalize file processing."""
    file_info["processing_complete"] = True
    file_info["processed_at"] = await now()

    if not file_info.get("processed", False):
        file_info["status"] = "unsupported_type"
    else:
        file_info["status"] = "completed"

    return file_info

@workflow
async def file_processing_pipeline(ctx: ExecutionContext[dict]):
    """Process files through a conditional pipeline."""
    file_info = ctx.input

    # All files go through the same pipeline, but processing is conditional
    result = await pipeline(
        analyze_file_type(file_info),
        process_image,
        process_video,
        process_text,
        finalize_processing
    )

    return result
```

## Parallel Processing within Pipelines

Combine pipelines with parallel processing for complex workflows:

```python
from flux import ExecutionContext, task, workflow
from flux.tasks import pipeline, parallel

@task
async def split_data(dataset: dict):
    """Split dataset into chunks for parallel processing."""
    data = dataset["data"]
    chunk_size = dataset.get("chunk_size", 100)

    chunks = []
    for i in range(0, len(data), chunk_size):
        chunk = {
            "id": i // chunk_size,
            "data": data[i:i + chunk_size],
            "total_chunks": (len(data) + chunk_size - 1) // chunk_size
        }
        chunks.append(chunk)

    return chunks

@task
async def process_chunk(chunk: dict):
    """Process a single data chunk."""
    chunk_id = chunk["id"]
    data = chunk["data"]

    # Simulate processing
    await asyncio.sleep(0.5)

    processed_data = [item * 2 for item in data]  # Simple transformation

    return {
        "chunk_id": chunk_id,
        "original_size": len(data),
        "processed_data": processed_data
    }

@task
async def combine_results(processed_chunks: list):
    """Combine processed chunks back into a single result."""
    # Sort chunks by ID to maintain order
    sorted_chunks = sorted(processed_chunks, key=lambda x: x["chunk_id"])

    combined_data = []
    for chunk in sorted_chunks:
        combined_data.extend(chunk["processed_data"])

    return {
        "total_items": len(combined_data),
        "chunks_processed": len(sorted_chunks),
        "data": combined_data
    }

@task
async def generate_report(final_data: dict):
    """Generate processing report."""
    data = final_data["data"]

    report = {
        "total_items": len(data),
        "min_value": min(data) if data else 0,
        "max_value": max(data) if data else 0,
        "average": sum(data) / len(data) if data else 0,
        "chunks_processed": final_data["chunks_processed"]
    }

    return report

@workflow
async def parallel_pipeline_workflow(ctx: ExecutionContext[dict]):
    """Process large dataset using parallel processing within a pipeline."""
    dataset = ctx.input

    result = await pipeline(
        # Stage 1: Split data into chunks
        split_data(dataset),

        # Stage 2: Process chunks in parallel
        lambda chunks: parallel(*[process_chunk(chunk) for chunk in chunks]),

        # Stage 3: Combine results
        combine_results,

        # Stage 4: Generate final report
        generate_report
    )

    return result

# Example usage
if __name__ == "__main__":
    large_dataset = {
        "data": list(range(1, 251)),  # 250 items
        "chunk_size": 50  # Process in chunks of 50
    }

    result = parallel_pipeline_workflow.run(large_dataset)
    print(f"Processed {result.output['total_items']} items")
    print(f"Average value: {result.output['average']:.2f}")
```

## Error Handling in Pipelines

Implement robust error handling throughout your pipeline:

```python
@task.with_options(retry_max_attempts=2)
async def risky_transformation(data: any):
    """A transformation that might fail."""
    if random.random() < 0.3:  # 30% chance of failure
        raise Exception("Random failure in transformation")

    return {"transformed": data, "status": "success"}

@task
async def error_recovery(data: any):
    """Fallback transformation for error recovery."""
    return {"transformed": data, "status": "recovered", "note": "Used fallback"}

@task.with_options(fallback=error_recovery)
async def safe_transformation(data: any):
    """A transformation with fallback handling."""
    result = await risky_transformation(data)
    return result

@workflow
async def resilient_pipeline(ctx: ExecutionContext[any]):
    """A pipeline with error handling."""
    try:
        result = await pipeline(
            validate_input(ctx.input),
            safe_transformation,
            finalize_processing
        )
        return {"success": True, "result": result}

    except Exception as e:
        return {"success": False, "error": str(e)}
```

## Best Practices for Pipeline Design

### 1. Keep Stages Small and Focused
Each stage should have a single responsibility:

```python
# Good: Single responsibility
@task
async def extract_email(user_data: dict):
    return user_data["email"]

@task
async def validate_email(email: str):
    if "@" not in email:
        raise ValueError("Invalid email")
    return email

# Avoid: Multiple responsibilities
@task
async def extract_and_validate_email(user_data: dict):
    email = user_data["email"]  # Extraction
    if "@" not in email:        # Validation
        raise ValueError("Invalid email")
    return email
```

### 2. Make Stages Pure Functions
Avoid side effects when possible:

```python
# Good: Pure function
@task
async def calculate_tax(amount: float, rate: float = 0.1):
    return amount * rate

# Avoid: Side effects
@task
async def calculate_and_log_tax(amount: float):
    tax = amount * 0.1
    print(f"Tax calculated: {tax}")  # Side effect
    return tax
```

### 3. Handle Data Types Consistently
Ensure type consistency between stages:

```python
@task
async def parse_numbers(text: str) -> List[int]:
    """Always return a list of integers."""
    return [int(x) for x in text.split() if x.isdigit()]

@task
async def sum_numbers(numbers: List[int]) -> int:
    """Expects a list of integers."""
    return sum(numbers)
```

### 4. Use Descriptive Names
Make pipeline flow clear through naming:

```python
@workflow
async def user_onboarding_pipeline(ctx: ExecutionContext[dict]):
    result = await pipeline(
        validate_registration_data(ctx.input),
        create_user_account,
        send_welcome_email,
        setup_default_preferences,
        generate_onboarding_report
    )
    return result
```

## Testing Pipelines

Test each stage individually and the pipeline as a whole:

```python
import pytest

@pytest.mark.asyncio
async def test_individual_stage():
    """Test a single pipeline stage."""
    test_data = {"name": "Test User", "email": "test@example.com", "age": 25}
    result = await normalize_email(test_data)
    assert result["email"] == "test@example.com"
    assert result["email_domain"] == "example.com"

@pytest.mark.asyncio
async def test_complete_pipeline():
    """Test the entire pipeline."""
    test_input = {"name": "John Doe", "email": "JOHN@COMPANY.COM", "age": 30}
    result = user_profile_pipeline.run(test_input)

    assert result.output["success"] is True
    assert "summary" in result.output["result"]
    assert result.output["result"]["user_data"]["email"] == "john@company.com"

@pytest.mark.asyncio
async def test_pipeline_error_handling():
    """Test pipeline error handling."""
    invalid_input = {"name": "Test", "age": "invalid"}  # Missing email
    result = user_profile_pipeline.run(invalid_input)

    assert result.output["success"] is False
    assert "error" in result.output
```

## Performance Optimization

### 1. Minimize Data Copying
Pass references when possible:

```python
@task
async def add_metadata(data: dict):
    """Add metadata without copying the entire data structure."""
    data["processed_at"] = await now()
    data["version"] = "1.0"
    return data  # Same object, minimal copying
```

### 2. Use Streaming for Large Data
For large datasets, consider streaming:

```python
@task
async def process_large_dataset(data: dict):
    """Process large datasets in chunks."""
    if data.get("size", 0) > 10000:
        # Use chunked processing for large datasets
        return await process_in_chunks(data)
    else:
        # Regular processing for smaller datasets
        return await process_normally(data)
```

## Next Steps

Now that you understand pipeline processing in Flux:

1. Explore the [User Guide](../../user-guide/workflow-patterns.md) for advanced pipeline patterns
2. Learn about [Data Flow and State Management](../../user-guide/data-flow.md)
3. Check out [Task Configuration](../../user-guide/task-configuration.md) for advanced task options

## Summary

In this tutorial, you learned how to:

- **Build Sequential Pipelines**: Transform data through a series of stages using the `pipeline` built-in task
- **Add Validation and Error Handling**: Make pipelines robust with proper error handling
- **Implement Conditional Processing**: Create flexible pipelines that adapt to different data types
- **Combine with Parallel Processing**: Use both sequential and parallel patterns effectively
- **Follow Best Practices**: Design maintainable and efficient pipeline workflows

Pipeline processing is essential for building data transformation workflows, ETL processes, and any application that needs to process data through multiple sequential steps.
