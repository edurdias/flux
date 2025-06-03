# Your First Workflow

In this tutorial, you'll create and run your first Flux workflow. By the end, you'll understand the basic concepts of workflows and tasks, and how to execute them using the Flux CLI.

## What You'll Build

You'll create a simple data processing workflow that:
1. Fetches sample data from a mock source
2. Processes and transforms the data
3. Generates a summary report

This example demonstrates the core workflow concepts while remaining simple and easy to understand.

## Prerequisites

- Flux installed ([Installation Guide](../getting-started/installation.md))
- Python 3.8+ with basic knowledge of functions and decorators
- A text editor or IDE

## Step 1: Start Flux Services

Before creating workflows, you need to start the Flux server and worker.

**Terminal 1 - Start the Server:**
```bash
flux start server
```

You should see output like:
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8080 (Press CTRL+C to quit)
```

**Terminal 2 - Start a Worker:**
```bash
flux start worker
```

You should see output like:
```
INFO:     Worker started and waiting for tasks...
INFO:     Connected to server at http://127.0.0.1:8080
```

Keep both terminals running throughout this tutorial.

## Step 2: Create Your First Workflow

Create a new file called `my_first_workflow.py`:

```python
# my_first_workflow.py
from flux import workflow, task
from datetime import datetime
import json

@task
def fetch_sample_data(source: str) -> dict:
    """Simulate fetching data from a source."""
    print(f"Fetching data from {source}...")

    # Simulate data fetching with sample data
    sample_data = {
        "customers": [
            {"id": 1, "name": "Alice", "orders": 5, "total_spent": 250.50},
            {"id": 2, "name": "Bob", "orders": 3, "total_spent": 180.75},
            {"id": 3, "name": "Charlie", "orders": 8, "total_spent": 420.00},
            {"id": 4, "name": "Diana", "orders": 2, "total_spent": 95.25},
        ],
        "source": source,
        "timestamp": datetime.now().isoformat()
    }

    print(f"Fetched {len(sample_data['customers'])} customer records")
    return sample_data

@task
def process_customer_data(data: dict) -> dict:
    """Process and enrich customer data."""
    print("Processing customer data...")

    customers = data["customers"]
    processed_customers = []

    for customer in customers:
        # Calculate average order value
        avg_order_value = customer["total_spent"] / customer["orders"]

        # Categorize customer
        if customer["total_spent"] > 300:
            category = "premium"
        elif customer["total_spent"] > 150:
            category = "standard"
        else:
            category = "basic"

        processed_customer = {
            **customer,
            "avg_order_value": round(avg_order_value, 2),
            "category": category
        }
        processed_customers.append(processed_customer)

    processed_data = {
        "customers": processed_customers,
        "source": data["source"],
        "processed_at": datetime.now().isoformat(),
        "original_timestamp": data["timestamp"]
    }

    print(f"Processed {len(processed_customers)} customers")
    return processed_data

@task
def generate_summary_report(processed_data: dict) -> dict:
    """Generate a summary report from processed data."""
    print("Generating summary report...")

    customers = processed_data["customers"]

    # Calculate summary statistics
    total_customers = len(customers)
    total_orders = sum(c["orders"] for c in customers)
    total_revenue = sum(c["total_spent"] for c in customers)
    avg_order_value = total_revenue / total_orders if total_orders > 0 else 0

    # Categorize customers
    categories = {"premium": 0, "standard": 0, "basic": 0}
    for customer in customers:
        categories[customer["category"]] += 1

    # Find top customer
    top_customer = max(customers, key=lambda c: c["total_spent"])

    report = {
        "summary": {
            "total_customers": total_customers,
            "total_orders": total_orders,
            "total_revenue": round(total_revenue, 2),
            "avg_order_value": round(avg_order_value, 2)
        },
        "customer_categories": categories,
        "top_customer": {
            "name": top_customer["name"],
            "total_spent": top_customer["total_spent"],
            "orders": top_customer["orders"]
        },
        "report_metadata": {
            "source": processed_data["source"],
            "data_timestamp": processed_data["original_timestamp"],
            "processed_at": processed_data["processed_at"],
            "report_generated_at": datetime.now().isoformat()
        }
    }

    print("Summary report generated successfully")
    return report

@workflow
def customer_analysis_pipeline(data_source: str = "sample_db"):
    """A complete customer analysis workflow."""
    print(f"Starting customer analysis pipeline for source: {data_source}")

    # Step 1: Fetch the data
    raw_data = fetch_sample_data(data_source)

    # Step 2: Process the data
    processed_data = process_customer_data(raw_data)

    # Step 3: Generate report
    final_report = generate_summary_report(processed_data)

    print("Customer analysis pipeline completed!")
    return final_report
```

## Step 3: Register Your Workflow

Register your workflow with the Flux server:

```bash
flux workflow register my_first_workflow.py
```

You should see output like:
```
Successfully registered workflow: customer_analysis_pipeline
```

Verify the registration by listing workflows:

```bash
flux workflow list
```

You should see:
```
- customer_analysis_pipeline (version 1.0)
```

## Step 4: Run Your Workflow

Now run your workflow:

```bash
flux workflow run customer_analysis_pipeline
```

You'll see the workflow execution progress:
```json
{
  "execution_id": "exec_12345",
  "status": "running"
}
```

## Step 5: Check Workflow Status

Check the status of your running workflow:

```bash
flux workflow status customer_analysis_pipeline exec_12345
```

Once completed, you'll see output like:
```json
{
  "execution_id": "exec_12345",
  "status": "completed",
  "result": {
    "summary": {
      "total_customers": 4,
      "total_orders": 18,
      "total_revenue": 946.5,
      "avg_order_value": 52.58
    },
    "customer_categories": {
      "premium": 1,
      "standard": 2,
      "basic": 1
    },
    "top_customer": {
      "name": "Charlie",
      "total_spent": 420.0,
      "orders": 8
    }
  }
}
```

## Step 6: Run with Custom Input

You can also run the workflow with custom input:

```bash
flux workflow run customer_analysis_pipeline --input '{"data_source": "production_db"}'
```

## Understanding What Happened

Let's break down what you just created:

### 1. **Tasks** (`@task` decorator)
Each function decorated with `@task` becomes a Flux task:
- `fetch_sample_data`: Simulates data fetching
- `process_customer_data`: Transforms raw data
- `generate_summary_report`: Creates final output

### 2. **Workflow** (`@workflow` decorator)
The function decorated with `@workflow` orchestrates the tasks:
- Defines the execution order
- Passes data between tasks
- Returns the final result

### 3. **Data Flow**
Data flows naturally through function calls:
```
raw_data → processed_data → final_report
```

### 4. **Execution**
Flux handles:
- Task scheduling and execution
- State management
- Result storage and retrieval

## Key Concepts Learned

✅ **Workflows are Python functions** decorated with `@workflow`
✅ **Tasks are Python functions** decorated with `@task`
✅ **Data flows** through regular function calls and return values
✅ **Registration** makes workflows available to the Flux server
✅ **Execution** is managed by Flux workers
✅ **Status tracking** lets you monitor workflow progress

## What's Next?

Now that you've created your first workflow, you're ready for more advanced concepts:

1. **[Working with Tasks](working-with-tasks.md)** - Learn task composition and reusability
2. **[Parallel Processing](parallel-processing.md)** - Speed up workflows with parallel execution
3. **[Best Practices](best-practices.md)** - Production-ready workflow patterns

## Understanding What You Built

### Core Concepts Covered
- **[Workflows](../getting-started/basic_concepts.md#workflows)** - The `@workflow` decorator and execution context
- **[Tasks](../getting-started/basic_concepts.md#tasks)** - The `@task` decorator and task functions
- **[CLI Usage](../cli/workflow.md)** - Registering and running workflows

### Deep Dive References
- **[Workflow Management](../core-concepts/workflow-management.md)** - Advanced workflow patterns
- **[Task System](../core-concepts/tasks.md)** - Task composition and error handling
- **[Execution Model](../core-concepts/execution-model.md)** - How Flux executes workflows

## Troubleshooting

### Common Issues

**Problem**: Workflow registration fails
**Solution**: Make sure the Flux server is running and your Python file has no syntax errors
- See: [CLI: Start Server](../cli/start.md#flux-start-server)

**Problem**: Workflow execution hangs
**Solution**: Verify a worker is running and connected to the server
- See: [CLI: Start Worker](../cli/start.md#flux-start-worker)

**Problem**: Import errors in your workflow file
**Solution**: Make sure all required packages are installed in your Python environment
- See: [Installation Guide](../getting-started/installation.md)

### Getting Help

If you encounter issues:
1. Check the [Troubleshooting Guide](troubleshooting.md) for detailed solutions
2. Review the [FAQ](faq.md) for common questions
3. Look at the [CLI Reference](../cli/workflow.md) for command details
4. Understand [Basic Concepts](../getting-started/basic_concepts.md) if terminology is unclear

## See Also

- **[CLI Workflow Commands](../cli/workflow.md)** - Complete workflow command reference
- **[CLI Service Commands](../cli/start.md)** - Starting Flux services
- **[Working with Tasks Tutorial](working-with-tasks.md)** - Next tutorial in the series
- **[Core Concepts: Workflows](../core-concepts/workflow-management.md)** - Advanced workflow concepts

Great job completing your first workflow! You've taken the first step into the world of Flux workflow orchestration. 🎉
