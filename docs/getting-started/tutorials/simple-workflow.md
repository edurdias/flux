# Building Your First Meaningful Workflow

Now that you've learned the basics with your first "Hello World" workflow, it's time to build something more meaningful and real-world. In this tutorial, we'll create a **data processing workflow** that demonstrates core Flux concepts while solving a practical problem.

## What You'll Learn

By the end of this tutorial, you'll understand:
- How to design workflows that solve real problems
- Working with file I/O and data processing
- Using task configuration options like retry and timeout
- Handling errors gracefully with fallback mechanisms
- Combining multiple tasks into a cohesive workflow

## The Problem: Processing User Data

Imagine you run a small e-commerce website and need to process customer data files that are uploaded daily. Each file contains user information that needs to be:
1. Validated for correct format
2. Cleaned and normalized
3. Enriched with additional information
4. Saved to a processed format

Let's build a workflow that automates this entire process!

## Setting Up

Create a new file called `user_data_processor.py`. We'll build a complete workflow that includes sample data creation, so you can run everything from a single file.

## Building the Workflow

Now let's build our complete data processing workflow in `user_data_processor.py`:

```python
from flux import ExecutionContext, task, workflow
from pathlib import Path
import csv
import json
from datetime import datetime
from typing import List, Dict, Any

@task.with_options(retry=3, timeout=30, fallback=validation_fallback)
async def validate_file(file_path: str) -> Dict[str, Any]:
    """Validate that the input file exists and has the correct format."""
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not file_path.endswith('.csv'):
        raise ValueError(f"Expected CSV file, got: {file_path}")

    # Check if file has content
    with open(file_path, 'r') as f:
        reader = csv.reader(f)
        rows = list(reader)

    if len(rows) < 2:  # Header + at least one data row
        raise ValueError("File appears to be empty or missing data")

    return {
        "file_path": file_path,
        "total_rows": len(rows) - 1,  # Excluding header
        "columns": rows[0],
        "validation_time": datetime.now().isoformat()
    }

@task.with_options(retry_max_attempts=2, fallback=processing_fallback)
async def load_and_clean_data(validation_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Load the CSV data and perform basic cleaning."""
    file_path = validation_result["file_path"]

    cleaned_users = []

    with open(file_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Clean and normalize the data
            cleaned_user = {
                "name": row["name"].strip().title(),
                "email": row["email"].strip().lower(),
                "age": int(row["age"]),
                "city": row["city"].strip().title(),
                "processed_at": datetime.now().isoformat()
            }
            cleaned_users.append(cleaned_user)

    return cleaned_users

@task
async def enrich_user_data(users: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrich user data with additional information."""
    # Simulate enrichment with demographic data
    city_demographics = {
        "New York": {"state": "NY", "population": 8_400_000, "timezone": "EST"},
        "Los Angeles": {"state": "CA", "population": 4_000_000, "timezone": "PST"},
        "Chicago": {"state": "IL", "population": 2_700_000, "timezone": "CST"},
        "Houston": {"state": "TX", "population": 2_300_000, "timezone": "CST"},
        "Phoenix": {"state": "AZ", "population": 1_700_000, "timezone": "MST"}
    }

    enriched_users = []
    for user in users:
        enriched_user = user.copy()
        city_info = city_demographics.get(user["city"], {})
        enriched_user.update({
            "state": city_info.get("state", "Unknown"),
            "city_population": city_info.get("population", 0),
            "timezone": city_info.get("timezone", "Unknown"),
            "age_group": "young" if user["age"] < 30 else "middle-aged" if user["age"] < 50 else "senior"
        })
        enriched_users.append(enriched_user)

    return enriched_users

@task.with_options(timeout=60, fallback=reporting_fallback)
async def generate_report(users: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate a summary report of the processed data."""
    total_users = len(users)
    avg_age = sum(user["age"] for user in users) / total_users if total_users > 0 else 0

    cities = {}
    age_groups = {"young": 0, "middle-aged": 0, "senior": 0}

    for user in users:
        # Count by city
        city = user["city"]
        cities[city] = cities.get(city, 0) + 1

        # Count by age group
        age_groups[user["age_group"]] += 1

    return {
        "total_users": total_users,
        "average_age": round(avg_age, 1),
        "cities": cities,
        "age_groups": age_groups,
        "report_generated_at": datetime.now().isoformat()
    }

@task.with_options(fallback=save_fallback)
async def save_results(users: List[Dict[str, Any]], report: Dict[str, Any], output_dir: str = "data/processed") -> Dict[str, str]:
    """Save the processed data and report to files."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save processed users
    users_file = f"{output_dir}/users_processed_{timestamp}.json"
    with open(users_file, 'w') as f:
        json.dump(users, f, indent=2)

    # Save report
    report_file = f"{output_dir}/report_{timestamp}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)

    return {
        "users_file": users_file,
        "report_file": report_file,
        "status": "success"
    }

# Fallback handlers for error handling
async def validation_fallback(file_path: str) -> Dict[str, Any]:
    """Fallback for file validation failures."""
    return {
        "file_path": file_path,
        "total_rows": 0,
        "columns": [],
        "validation_time": datetime.now().isoformat(),
        "status": "validation_failed"
    }

async def processing_fallback(validation_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fallback for data processing failures."""
    return []

async def reporting_fallback(users: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fallback for report generation failures."""
    return {
        "total_users": 0,
        "average_age": 0,
        "cities": {},
        "age_groups": {"young": 0, "middle-aged": 0, "senior": 0},
        "report_generated_at": datetime.now().isoformat(),
        "status": "report_failed"
    }

async def save_fallback(users: List[Dict[str, Any]], report: Dict[str, Any], output_dir: str = "data/processed") -> Dict[str, str]:
    """Fallback for save failures - log error instead."""
    Path("data/errors").mkdir(parents=True, exist_ok=True)
    error_file = f"data/errors/save_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    error_log = {
        "error": "Failed to save results",
        "timestamp": datetime.now().isoformat(),
        "user_count": len(users),
        "report_summary": report.get("total_users", 0)
    }

    with open(error_file, 'w') as f:
        json.dump(error_log, f, indent=2)

    return {
        "users_file": "",
        "report_file": "",
        "error_file": error_file,
        "status": "save_failed"
    }

@workflow
async def process_user_data(ctx: ExecutionContext[str]) -> Dict[str, Any]:
    """Main workflow for processing user data files."""
    file_path = ctx.input

    # Step 1: Validate the input file
    validation_result = await validate_file(file_path)
    print(f"✅ File validated: {validation_result['total_rows']} rows found")

    # Step 2: Load and clean the data
    cleaned_users = await load_and_clean_data(validation_result)
    print(f"✅ Data cleaned: {len(cleaned_users)} users processed")

    # Step 3: Enrich the data
    enriched_users = await enrich_user_data(cleaned_users)
    print(f"✅ Data enriched with demographic information")

    # Step 4: Generate summary report
    report = await generate_report(enriched_users)
    print(f"✅ Report generated: {report['total_users']} total users")

    # Step 5: Save results
    save_result = await save_results(enriched_users, report)

    if save_result.get("status") == "save_failed":
        print(f"⚠️ Save failed, error logged to {save_result['error_file']}")
    else:
        print(f"✅ Results saved to {save_result['users_file']}")

    return {
        "status": "completed",
        "processed_users": len(enriched_users),
        "files_created": save_result,
        "summary": report
    }

# Create sample data when script is run
def create_sample_data():
    data = [
        ['John Doe', 'john.doe@email.com', '25', 'new york'],
        ['Jane Smith', 'jane.smith@email.com', '30', 'los angeles'],
        ['Bob Johnson', 'bob.johnson@email.com', '35', 'chicago'],
        ['Alice Brown', 'alice.brown@email.com', '28', 'houston'],
        ['Charlie Wilson', 'charlie.wilson@email.com', '42', 'phoenix']
    ]

    Path('data').mkdir(exist_ok=True)
    with open('data/users_raw.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'email', 'age', 'city'])
        writer.writerows(data)

if __name__ == "__main__":
    # Create sample data if it doesn't exist
    if not Path('data/users_raw.csv').exists():
        create_sample_data()
        print("📁 Sample data created")

    # Run the workflow
    print("🚀 Starting user data processing workflow...")
    result = process_user_data.run("data/users_raw.csv")

    print("\n📊 Final Result:")
    print(json.dumps(result.output, indent=2))
```

## Understanding the Workflow

Let's break down what makes this workflow powerful using Flux's built-in error handling:

### 1. **Built-in Task Configuration with Fallbacks**
```python
@task.with_options(retry=3, timeout=30, fallback=validation_fallback)
async def validate_file(file_path: str):
```
- `retry=3`: Automatically retry the task up to 3 times if it fails
- `timeout=30`: Fail the task if it takes longer than 30 seconds
- `fallback=validation_fallback`: If all retries fail, call the fallback function instead of crashing

### 2. **Graceful Error Handling with Fallbacks**
Instead of using try/catch blocks, each task has its own fallback function that provides sensible defaults:

```python
async def validation_fallback(file_path: str) -> Dict[str, Any]:
    """Fallback for file validation failures."""
    return {
        "file_path": file_path,
        "total_rows": 0,
        "columns": [],
        "validation_time": datetime.now().isoformat(),
        "status": "validation_failed"
    }

async def save_fallback(users: List[Dict[str, Any]], report: Dict[str, Any], output_dir: str = "data/processed") -> Dict[str, str]:
    """Fallback for save failures - log error instead."""
    Path("data/errors").mkdir(parents=True, exist_ok=True)
    error_file = f"data/errors/save_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    error_log = {
        "error": "Failed to save results",
        "timestamp": datetime.now().isoformat(),
        "user_count": len(users)
    }

    with open(error_file, 'w') as f:
        json.dump(error_log, f, indent=2)

    return {"status": "save_failed", "error_file": error_file}
```

- **`validation_fallback`**: Returns empty data structure when file validation fails
- **`processing_fallback`**: Returns empty list when data processing fails
- **`reporting_fallback`**: Returns zero-filled report when report generation fails
- **`save_fallback`**: Logs error and returns failure status when saving fails

### 3. **Automatic Error Recovery**
Flux automatically handles the error recovery flow:

```python
# When a task fails, Flux automatically:
# 1. First: Try the task normally
await validate_file(file_path)  # Initial attempt

# 2. If it fails: Retry up to the specified number of times
# (retry=3 means up to 3 additional attempts)

# 3. If retries fail: Call the fallback function automatically
validation_result = await validation_fallback(file_path)

# 4. Continue: Workflow continues with fallback result
cleaned_users = await load_and_clean_data(validation_result)
```

1. **First**: Try the task normally
2. **If it fails**: Retry up to the specified number of times
3. **If retries fail**: Call the fallback function automatically
4. **Continue**: Workflow continues with fallback result instead of crashing

### 4. **Clean Workflow Logic**
The main workflow is now much cleaner without try/catch blocks:

```python
@workflow
async def process_user_data(ctx: ExecutionContext[str]) -> Dict[str, Any]:
    """Main workflow for processing user data files."""
    file_path = ctx.input

    # Clean, linear flow - no try/catch needed!
    validation_result = await validate_file(file_path)  # Handles errors with fallback
    cleaned_users = await load_and_clean_data(validation_result)  # Handles errors with fallback
    enriched_users = await enrich_user_data(cleaned_users)
    report = await generate_report(enriched_users)  # Handles errors with fallback
    save_result = await save_results(enriched_users, report)  # Handles errors with fallback

    return {
        "status": "completed",
        "processed_users": len(enriched_users),
        "files_created": save_result,
        "summary": report
    }
```

- Each task handles its own errors through fallbacks
- Workflow focuses on the happy path
- Error handling is declarative, not imperative

## Running the Workflow

### Method 1: Direct Python Execution

Save the file and run it:

```bash
python user_data_processor.py
```

You should see output like:
```
📁 Sample data created
🚀 Starting user data processing workflow...
✅ File validated: 5 rows found
✅ Data cleaned: 5 users processed
✅ Data enriched with demographic information
✅ Report generated: 5 total users
✅ Results saved to data/processed/users_processed_20250603_143021.json

📊 Final Result:
{
  "status": "success",
  "processed_users": 5,
  "files_created": {
    "users_file": "data/processed/users_processed_20250603_143021.json",
    "report_file": "data/processed/report_20250603_143021.json",
    "status": "success"
  },
  "summary": {
    "total_users": 5,
    "average_age": 32.0,
    "cities": {
      "New York": 1,
      "Los Angeles": 1,
      "Chicago": 1,
      "Houston": 1,
      "Phoenix": 1
    },
    "age_groups": {
      "young": 2,
      "middle-aged": 3,
      "senior": 0
    },
    "report_generated_at": "2025-06-03T14:30:21.123456"
  }
}
```

### Method 2: Using Flux CLI for Distributed Processing

For production workloads, use the Flux server:

```bash
# Start server and worker
flux start server &
flux start worker &

# Register and run the workflow
flux workflow register user_data_processor.py
flux workflow run process_user_data '"data/users_raw.csv"' --mode sync
```

## Testing Error Handling

Let's test the error handling by trying to process a non-existent file:

```python
# Test error handling
error_result = process_user_data.run("nonexistent.csv")
print("Error handling result:")
print(json.dumps(error_result.output, indent=2))
```

This should gracefully handle the error and create an error log file.

## Extending the Workflow

Now that you have a working data processing workflow, try these extensions:

### 1. **Add Email Validation**
```python
@task
async def validate_emails(users: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    import re
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

    for user in users:
        user['email_valid'] = bool(re.match(email_pattern, user['email']))

    return users
```

### 2. **Add Data Export Options**
```python
@task
async def export_to_csv(users: List[Dict[str, Any]], output_file: str):
    import pandas as pd
    df = pd.DataFrame(users)
    df.to_csv(output_file, index=False)
    return output_file
```

### 3. **Add Configuration Support**
```python
@task
async def load_config(config_file: str = "config.json") -> Dict[str, Any]:
    if Path(config_file).exists():
        with open(config_file) as f:
            return json.load(f)
    return {"output_format": "json", "max_age": 100}
```

## Key Takeaways

This workflow demonstrates several important Flux concepts:

1. **Modularity**: Each task has a single responsibility
2. **Reliability**: Tasks use retry and timeout configurations
3. **Error Handling**: Graceful failure with fallback mechanisms
4. **Real-world Processing**: Practical data transformation and enrichment
5. **Observability**: Progress reporting and comprehensive logging
6. **Flexibility**: Easy to extend with additional processing steps

## Next Steps

You've now built a meaningful workflow that solves a real problem! To continue learning:

1. **[Adding Error Handling](error-handling.md)** - Learn more advanced error handling patterns
2. **[Parallel Execution](parallel-execution.md)** - Process multiple files simultaneously
3. **[Pipeline Processing](pipeline-processing.md)** - Build more complex data pipelines
4. **[Task Configuration](../task-options.md)** - Explore all available task options

Try adapting this workflow to process your own data or solve problems in your domain!
