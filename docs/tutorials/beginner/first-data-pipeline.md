# Building Your First Data Pipeline

In this tutorial, you'll learn how to build a complete data processing pipeline using Flux. We'll create a workflow that extracts data from a CSV file, validates and transforms it, then saves the results to a new file.

## What You'll Learn

- Creating robust data processing tasks
- Handling file I/O operations
- Implementing data validation
- Using parallel processing for performance
- Error handling in data pipelines
- Testing your workflows

## Prerequisites

Before starting this tutorial, ensure you have:
- Flux installed (`pip install flux-core`)
- Basic understanding of Python async/await
- Familiarity with pandas for data manipulation

## Project Setup

Create a new directory for your project:

```bash
mkdir flux-data-pipeline
cd flux-data-pipeline
```

Create a sample CSV file for testing:

```bash
cat > sample_data.csv << EOF
id,name,age,department,salary
1,Alice Johnson,28,Engineering,75000
2,Bob Smith,35,Marketing,65000
3,Carol Davis,42,Engineering,85000
4,David Wilson,29,Sales,55000
5,Eva Brown,31,Marketing,60000
EOF
```

## Building the Pipeline

### Step 1: Import Dependencies

Create a file called `data_pipeline.py`:

```python
from flux import task, workflow, ExecutionContext
from flux.tasks import parallel
import pandas as pd
import asyncio
from pathlib import Path
from typing import Dict, List
import json
```

### Step 2: Create Data Processing Tasks

```python
@task
async def load_csv_data(file_path: str) -> pd.DataFrame:
    """Load data from a CSV file."""
    try:
        data = pd.read_csv(file_path)
        print(f"Loaded {len(data)} records from {file_path}")
        return data
    except FileNotFoundError:
        raise ValueError(f"File not found: {file_path}")
    except Exception as e:
        raise ValueError(f"Error loading CSV: {str(e)}")

@task
async def validate_data(data: pd.DataFrame) -> Dict:
    """Validate the loaded data and return validation results."""
    validation_results = {
        "total_records": len(data),
        "missing_values": data.isnull().sum().to_dict(),
        "data_types": data.dtypes.to_dict(),
        "valid": True,
        "errors": []
    }

    # Check for required columns
    required_columns = ["id", "name", "age", "department", "salary"]
    missing_columns = [col for col in required_columns if col not in data.columns]

    if missing_columns:
        validation_results["valid"] = False
        validation_results["errors"].append(f"Missing columns: {missing_columns}")

    # Check for negative salaries
    if "salary" in data.columns and (data["salary"] < 0).any():
        validation_results["valid"] = False
        validation_results["errors"].append("Found negative salary values")

    # Check for unrealistic ages
    if "age" in data.columns and ((data["age"] < 16) | (data["age"] > 100)).any():
        validation_results["valid"] = False
        validation_results["errors"].append("Found unrealistic age values")

    print(f"Validation complete: {'PASSED' if validation_results['valid'] else 'FAILED'}")
    return validation_results

@task
async def clean_data(data: pd.DataFrame) -> pd.DataFrame:
    """Clean and normalize the data."""
    cleaned_data = data.copy()

    # Remove duplicates
    initial_count = len(cleaned_data)
    cleaned_data = cleaned_data.drop_duplicates()
    duplicates_removed = initial_count - len(cleaned_data)

    # Standardize department names
    if "department" in cleaned_data.columns:
        dept_mapping = {
            "eng": "Engineering",
            "engineering": "Engineering",
            "mkt": "Marketing",
            "marketing": "Marketing",
            "sales": "Sales"
        }
        cleaned_data["department"] = cleaned_data["department"].str.lower().map(
            lambda x: dept_mapping.get(x, x.title()) if pd.notna(x) else x
        )

    # Trim whitespace from string columns
    string_columns = cleaned_data.select_dtypes(include=["object"]).columns
    for col in string_columns:
        cleaned_data[col] = cleaned_data[col].str.strip()

    print(f"Data cleaning complete: removed {duplicates_removed} duplicates")
    return cleaned_data

@task
async def calculate_statistics(data: pd.DataFrame) -> Dict:
    """Calculate summary statistics for the dataset."""
    stats = {}

    if "salary" in data.columns:
        stats["salary_stats"] = {
            "mean": float(data["salary"].mean()),
            "median": float(data["salary"].median()),
            "min": float(data["salary"].min()),
            "max": float(data["salary"].max()),
            "std": float(data["salary"].std())
        }

    if "age" in data.columns:
        stats["age_stats"] = {
            "mean": float(data["age"].mean()),
            "median": float(data["age"].median()),
            "min": int(data["age"].min()),
            "max": int(data["age"].max())
        }

    if "department" in data.columns:
        stats["department_counts"] = data["department"].value_counts().to_dict()

    stats["total_records"] = len(data)

    print(f"Statistics calculated for {len(data)} records")
    return stats

@task
async def enrich_data(data: pd.DataFrame) -> pd.DataFrame:
    """Add calculated fields to enrich the dataset."""
    enriched_data = data.copy()

    # Add salary bands
    if "salary" in enriched_data.columns:
        enriched_data["salary_band"] = pd.cut(
            enriched_data["salary"],
            bins=[0, 50000, 70000, 90000, float("inf")],
            labels=["Entry", "Mid", "Senior", "Executive"]
        )

    # Add age groups
    if "age" in enriched_data.columns:
        enriched_data["age_group"] = pd.cut(
            enriched_data["age"],
            bins=[0, 25, 35, 45, 100],
            labels=["Young", "Mid-Career", "Experienced", "Senior"]
        )

    print(f"Data enriched with {len(enriched_data.columns) - len(data.columns)} new columns")
    return enriched_data

@task
async def save_results(data: pd.DataFrame, stats: Dict, output_dir: str) -> Dict:
    """Save processed data and statistics to files."""
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    # Save processed data
    data_file = output_path / "processed_data.csv"
    data.to_csv(data_file, index=False)

    # Save statistics
    stats_file = output_path / "statistics.json"
    with open(stats_file, "w") as f:
        json.dump(stats, f, indent=2)

    # Create summary report
    summary = {
        "processing_date": pd.Timestamp.now().isoformat(),
        "total_records": len(data),
        "output_files": {
            "data": str(data_file),
            "statistics": str(stats_file)
        },
        "columns": list(data.columns)
    }

    summary_file = output_path / "summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"Results saved to {output_dir}")
    return summary
```

### Step 3: Create the Main Pipeline Workflow

```python
@workflow
async def data_processing_pipeline(ctx: ExecutionContext[Dict]) -> Dict:
    """
    Main data processing pipeline workflow.

    Input: {
        "input_file": "path/to/input.csv",
        "output_dir": "path/to/output"
    }
    """
    config = ctx.input
    input_file = config.get("input_file")
    output_dir = config.get("output_dir", "output")

    # Step 1: Load the data
    raw_data = await load_csv_data(input_file)

    # Step 2: Validate the data
    validation_results = await validate_data(raw_data)

    # If validation fails, return early with error details
    if not validation_results["valid"]:
        return {
            "status": "failed",
            "validation_errors": validation_results["errors"],
            "message": "Data validation failed"
        }

    # Step 3: Process data in parallel
    processing_results = await parallel(
        clean_data(raw_data),
        calculate_statistics(raw_data)
    )

    cleaned_data = processing_results[0]
    statistics = processing_results[1]

    # Step 4: Enrich the cleaned data
    enriched_data = await enrich_data(cleaned_data)

    # Step 5: Save results
    summary = await save_results(enriched_data, statistics, output_dir)

    return {
        "status": "completed",
        "validation": validation_results,
        "statistics": statistics,
        "summary": summary,
        "message": f"Successfully processed {len(enriched_data)} records"
    }
```

### Step 4: Add Error Handling with Retries

Let's enhance our tasks with robust error handling:

```python
# Enhanced load task with retries
@task.with_options(
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    fallback=lambda file_path: pd.DataFrame()  # Return empty DataFrame on failure
)
async def load_csv_data_robust(file_path: str) -> pd.DataFrame:
    """Load data from a CSV file with retry logic."""
    try:
        # Simulate potential network/file system delays
        await asyncio.sleep(0.1)
        data = pd.read_csv(file_path)
        print(f"Loaded {len(data)} records from {file_path}")
        return data
    except Exception as e:
        print(f"Error loading CSV (will retry): {str(e)}")
        raise
```

## Running the Pipeline

### Method 1: Local Execution

Create a runner script `run_pipeline.py`:

```python
from data_pipeline import data_processing_pipeline

if __name__ == "__main__":
    # Configure the pipeline
    config = {
        "input_file": "sample_data.csv",
        "output_dir": "pipeline_output"
    }

    # Execute the workflow
    ctx = data_processing_pipeline.run(config)

    # Display results
    if ctx.succeeded:
        print("✅ Pipeline completed successfully!")
        print(f"Result: {ctx.output}")
    else:
        print("❌ Pipeline failed!")
        print(f"Error: {ctx.output}")
```

Run the pipeline:

```bash
python run_pipeline.py
```

### Method 2: Distributed Execution

For production workloads, use the Flux server:

```bash
# Start server and worker
flux start server &
flux start worker &

# Register the workflow
flux workflow register data_pipeline.py

# Run the pipeline
flux workflow run data_processing_pipeline '{"input_file": "sample_data.csv", "output_dir": "distributed_output"}' --mode sync
```

## Testing Your Pipeline

Create a test file `test_pipeline.py`:

```python
import pytest
import pandas as pd
import tempfile
import json
from pathlib import Path
from data_pipeline import (
    load_csv_data, validate_data, clean_data,
    calculate_statistics, data_processing_pipeline
)

@pytest.fixture
def sample_csv():
    """Create a temporary CSV file for testing."""
    data = {
        "id": [1, 2, 3, 4],
        "name": ["Alice", "Bob", "Carol", "David"],
        "age": [25, 30, 35, 40],
        "department": ["Engineering", "Marketing", "Sales", "Engineering"],
        "salary": [70000, 60000, 65000, 80000]
    }
    df = pd.DataFrame(data)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        df.to_csv(f.name, index=False)
        return f.name

@pytest.mark.asyncio
async def test_load_csv_data(sample_csv):
    """Test CSV loading functionality."""
    data = await load_csv_data(sample_csv)
    assert len(data) == 4
    assert "name" in data.columns
    assert "salary" in data.columns

@pytest.mark.asyncio
async def test_validate_data():
    """Test data validation."""
    # Valid data
    valid_data = pd.DataFrame({
        "id": [1, 2], "name": ["Alice", "Bob"],
        "age": [25, 30], "department": ["Eng", "Sales"],
        "salary": [70000, 60000]
    })

    validation = await validate_data(valid_data)
    assert validation["valid"] is True

    # Invalid data (negative salary)
    invalid_data = pd.DataFrame({
        "id": [1], "name": ["Alice"],
        "age": [25], "department": ["Eng"],
        "salary": [-1000]
    })

    validation = await validate_data(invalid_data)
    assert validation["valid"] is False

@pytest.mark.asyncio
async def test_complete_pipeline(sample_csv):
    """Test the complete pipeline end-to-end."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config = {
            "input_file": sample_csv,
            "output_dir": temp_dir
        }

        ctx = data_processing_pipeline.run(config)

        assert ctx.succeeded
        assert ctx.output["status"] == "completed"

        # Check output files exist
        output_path = Path(temp_dir)
        assert (output_path / "processed_data.csv").exists()
        assert (output_path / "statistics.json").exists()
        assert (output_path / "summary.json").exists()

if __name__ == "__main__":
    pytest.main([__file__])
```

Run the tests:

```bash
pip install pytest
python test_pipeline.py
```

## Monitoring and Observability

Add logging and monitoring to your pipeline:

```python
import logging
from flux import task

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@task
async def load_csv_data_with_monitoring(file_path: str) -> pd.DataFrame:
    """Load data with monitoring and logging."""
    logger.info(f"Starting to load data from {file_path}")

    try:
        start_time = time.time()
        data = pd.read_csv(file_path)
        load_time = time.time() - start_time

        logger.info(f"Successfully loaded {len(data)} records in {load_time:.2f}s")

        # Log data profile
        logger.info(f"Data shape: {data.shape}")
        logger.info(f"Memory usage: {data.memory_usage().sum() / 1024:.2f} KB")

        return data
    except Exception as e:
        logger.error(f"Failed to load data from {file_path}: {str(e)}")
        raise
```

## Production Considerations

### 1. Configuration Management

Use environment variables or configuration files:

```python
import os
from dataclasses import dataclass

@dataclass
class PipelineConfig:
    max_file_size_mb: int = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "10000"))
    output_format: str = os.getenv("OUTPUT_FORMAT", "csv")
    enable_backup: bool = os.getenv("ENABLE_BACKUP", "true").lower() == "true"

# Use in your workflow
config = PipelineConfig()
```

### 2. Memory Management for Large Files

Handle large datasets efficiently:

```python
@task
async def process_large_csv(file_path: str, chunk_size: int = 10000) -> List[str]:
    """Process large CSV files in chunks."""
    output_files = []

    for i, chunk in enumerate(pd.read_csv(file_path, chunksize=chunk_size)):
        # Process each chunk
        processed_chunk = await clean_data(chunk)

        # Save chunk to temporary file
        chunk_file = f"temp_chunk_{i}.csv"
        processed_chunk.to_csv(chunk_file, index=False)
        output_files.append(chunk_file)

    return output_files
```

### 3. Data Quality Monitoring

Implement comprehensive data quality checks:

```python
@task
async def advanced_data_quality_check(data: pd.DataFrame) -> Dict:
    """Perform advanced data quality validation."""
    quality_report = {
        "timestamp": pd.Timestamp.now().isoformat(),
        "record_count": len(data),
        "quality_score": 0.0,
        "issues": []
    }

    # Check completeness
    null_percentage = (data.isnull().sum() / len(data)) * 100
    for col, pct in null_percentage.items():
        if pct > 5:  # More than 5% missing
            quality_report["issues"].append(f"High missing values in {col}: {pct:.1f}%")

    # Check data distribution anomalies
    if "salary" in data.columns:
        q1, q3 = data["salary"].quantile([0.25, 0.75])
        iqr = q3 - q1
        outliers = ((data["salary"] < (q1 - 1.5 * iqr)) |
                   (data["salary"] > (q3 + 1.5 * iqr))).sum()
        if outliers > len(data) * 0.1:  # More than 10% outliers
            quality_report["issues"].append(f"High number of salary outliers: {outliers}")

    # Calculate quality score
    quality_report["quality_score"] = max(0, 100 - len(quality_report["issues"]) * 10)

    return quality_report
```

## Next Steps

Congratulations! You've built a robust data processing pipeline with Flux. Here are some ways to extend it:

1. **Add More Data Sources**: Extend the pipeline to handle different data formats (JSON, Parquet, databases)
2. **Implement Data Validation Rules**: Create configurable validation rules
3. **Add Data Lineage Tracking**: Track data transformations and dependencies
4. **Create a Scheduler**: Set up automated pipeline execution
5. **Add Alerting**: Implement notifications for pipeline failures or data quality issues

## Related Tutorials

- [Adding Resilience to Workflows](adding-resilience.md) - Learn advanced error handling
- [Working with External APIs](external-apis.md) - Integrate external data sources
- [Multi-Step Data Processing](../intermediate/multi-step-processing.md) - Complex pipeline patterns

## See Also

- [Task Configuration](../../user-guide/task-configuration.md) - Advanced task options
- [Error Management](../../user-guide/error-management.md) - Comprehensive error handling
- [Built-in Tasks](../../getting-started/built-in-tasks.md) - Available utility tasks
