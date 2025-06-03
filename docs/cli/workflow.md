# Workflow Commands

The `flux workflow` command group provides comprehensive workflow management capabilities including listing, registering, running, and monitoring workflows.

> 💡 **New to workflows?** Start with our [Your First Workflow Tutorial](../tutorials/your-first-workflow.md) to learn the basics before using these commands.

## Command Overview

| Command | Description |
|---------|-------------|
| [`list`](#flux-workflow-list) | List all registered workflows |
| [`register`](#flux-workflow-register) | Register workflows from a Python file |
| [`show`](#flux-workflow-show) | Show detailed information about a workflow |
| [`run`](#flux-workflow-run) | Execute a workflow with specified input |
| [`status`](#flux-workflow-status) | Check the status of a running workflow execution |

---

## `flux workflow list`

List all workflows currently registered with the Flux server.

### Usage

```bash
flux workflow list [OPTIONS]
```

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--format` | `-f` | choice | `simple` | Output format: `simple` or `json` |
| `--server-url` | `-surl` | string | auto | Server URL to connect to |

### Examples

**List workflows in simple format:**
```bash
flux workflow list
```

Output:
```
- hello_world (version 1.0)
- data_processing (version 2.1)
- notification_sender (version 1.5)
```

**List workflows in JSON format:**
```bash
flux workflow list --format json
```

Output:
```json
[
  {
    "name": "hello_world",
    "version": "1.0",
    "description": "A simple greeting workflow"
  },
  {
    "name": "data_processing",
    "version": "2.1",
    "description": "Process and transform data files"
  }
]
```

**Connect to custom server:**
```bash
flux workflow list --server-url http://production-server:8080
```

---

## `flux workflow register`

Register one or more workflows from a Python file containing workflow definitions.

### Usage

```bash
flux workflow register FILENAME [OPTIONS]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `FILENAME` | string | Yes | Path to Python file containing workflow definitions |

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--server-url` | `-surl` | string | auto | Server URL to connect to |

### Examples

**Register workflows from a file:**
```bash
flux workflow register my_workflows.py
```

Output:
```
Successfully registered 2 workflow(s) from 'my_workflows.py'.
  - data_pipeline (version 1.0)
  - error_handler (version 1.2)
```

**Register to custom server:**
```bash
flux workflow register workflows.py --server-url http://staging:8080
```

### Workflow File Format

The Python file should contain workflow definitions using Flux decorators:

```python
from flux import workflow, task

@task
def process_data(data: str) -> str:
    return data.upper()

@workflow
def simple_workflow(input_data: str):
    result = process_data(input_data)
    return {"processed": result}
```

---

## `flux workflow show`

Display detailed information about a specific registered workflow including its definition, version, and metadata.

### Usage

```bash
flux workflow show WORKFLOW_NAME [OPTIONS]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `WORKFLOW_NAME` | string | Yes | Name of the workflow to display |

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--server-url` | `-surl` | string | auto | Server URL to connect to |

### Examples

**Show workflow details:**
```bash
flux workflow show data_pipeline
```

Output:
```
Workflow: data_pipeline
Version: 1.0
Description: Process CSV files and generate reports

Details:
--------------------------------------------------
{
  "name": "data_pipeline",
  "version": "1.0",
  "description": "Process CSV files and generate reports",
  "tasks": [
    {
      "name": "load_data",
      "type": "python_function"
    },
    {
      "name": "transform_data",
      "type": "python_function"
    }
  ],
  "created_at": "2024-01-15T10:30:00Z"
}
```

**Show workflow from custom server:**
```bash
flux workflow show my_workflow --server-url http://remote:8080
```

---

## `flux workflow run`

Execute a workflow with specified input data and execution options.

### Usage

```bash
flux workflow run WORKFLOW_NAME INPUT [OPTIONS]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `WORKFLOW_NAME` | string | Yes | Name of the workflow to execute |
| `INPUT` | string | Yes | Input data (JSON string or simple value) |

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--mode` | `-m` | choice | `async` | Execution mode: `sync`, `async`, or `stream` |
| `--detailed` | `-d` | flag | False | Show detailed execution information |
| `--server-url` | `-surl` | string | auto | Server URL to connect to |

### Execution Modes

**Synchronous (`sync`)**: Waits for workflow completion and returns final result
**Asynchronous (`async`)**: Starts workflow and returns execution ID immediately
**Streaming (`stream`)**: Real-time streaming of execution events and progress

### Examples

**Run workflow asynchronously (default):**
```bash
flux workflow run hello_world "John Doe"
```

Output:
```json
{
  "execution_id": "exec_12345",
  "status": "running",
  "workflow_name": "hello_world",
  "started_at": "2024-01-15T14:30:00Z"
}
```

**Run workflow synchronously:**
```bash
flux workflow run data_pipeline '{"file": "data.csv"}' --mode sync
```

Output:
```json
{
  "execution_id": "exec_67890",
  "status": "completed",
  "result": {
    "processed_rows": 1000,
    "output_file": "processed_data.csv"
  },
  "duration": "00:02:15"
}
```

**Run with streaming output:**
```bash
flux workflow run long_process '{"iterations": 100}' --mode stream
```

Output:
```
Streaming execution...
{"event": "task_started", "task": "initialization", "timestamp": "2024-01-15T14:30:01Z"}
{"event": "progress", "progress": 25, "message": "Processing batch 1/4"}
{"event": "task_completed", "task": "initialization", "result": "success"}
...
```

**Run with detailed information:**
```bash
flux workflow run my_workflow '{"debug": true}' --detailed
```

**Complex JSON input:**
```bash
flux workflow run data_pipeline '{
  "source": "database",
  "tables": ["users", "orders"],
  "filters": {"active": true}
}'
```

### Input Format

The CLI automatically parses input values:

- **Simple strings**: `"hello world"`
- **Numbers**: `42` or `3.14`
- **Booleans**: `true` or `false`
- **JSON objects**: `'{"key": "value"}'`
- **JSON arrays**: `'[1, 2, 3]'`

---

## `flux workflow status`

Check the current status and progress of a workflow execution.

### Usage

```bash
flux workflow status WORKFLOW_NAME EXECUTION_ID [OPTIONS]
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `WORKFLOW_NAME` | string | Yes | Name of the workflow |
| `EXECUTION_ID` | string | Yes | Execution ID returned from workflow run |

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--detailed` | `-d` | flag | False | Show detailed execution information including task history |
| `--server-url` | `-surl` | string | auto | Server URL to connect to |

### Examples

**Check basic workflow status:**
```bash
flux workflow status data_pipeline exec_12345
```

Output:
```json
{
  "execution_id": "exec_12345",
  "workflow_name": "data_pipeline",
  "status": "running",
  "progress": 60,
  "current_task": "transform_data",
  "started_at": "2024-01-15T14:30:00Z"
}
```

**Check detailed status with task history:**
```bash
flux workflow status data_pipeline exec_12345 --detailed
```

Output:
```json
{
  "execution_id": "exec_12345",
  "workflow_name": "data_pipeline",
  "status": "running",
  "progress": 60,
  "current_task": "transform_data",
  "started_at": "2024-01-15T14:30:00Z",
  "tasks": [
    {
      "name": "load_data",
      "status": "completed",
      "started_at": "2024-01-15T14:30:01Z",
      "completed_at": "2024-01-15T14:31:30Z",
      "result": {"rows_loaded": 1000}
    },
    {
      "name": "transform_data",
      "status": "running",
      "started_at": "2024-01-15T14:31:30Z",
      "progress": 45
    }
  ],
  "events": [
    {
      "timestamp": "2024-01-15T14:30:01Z",
      "event": "workflow_started"
    },
    {
      "timestamp": "2024-01-15T14:31:30Z",
      "event": "task_completed",
      "task": "load_data"
    }
  ]
}
```

### Status Values

| Status | Description |
|--------|-------------|
| `pending` | Workflow is queued but not yet started |
| `running` | Workflow is currently executing |
| `completed` | Workflow finished successfully |
| `failed` | Workflow execution failed |
| `cancelled` | Workflow was cancelled by user |
| `timeout` | Workflow exceeded time limit |

---

## Common Patterns

### Workflow Development Cycle

1. **Develop workflow** in Python file
2. **Register** workflow: `flux workflow register my_workflows.py`
3. **Test execution**: `flux workflow run my_workflow '{"test": true}' --mode sync`
4. **Monitor progress**: `flux workflow status my_workflow <execution_id> --detailed`

### Production Deployment

```bash
# Register workflows to production server
flux workflow register production_workflows.py --server-url http://prod:8080

# Run critical workflow with monitoring
flux workflow run critical_process '{"env": "production"}' --mode async --detailed

# Check status periodically
flux workflow status critical_process exec_abc123 --server-url http://prod:8080
```

### Debugging Failed Workflows

```bash
# Run with detailed output
flux workflow run problematic_workflow '{"debug": true}' --mode sync --detailed

# Check detailed status for error information
flux workflow status problematic_workflow exec_failed --detailed
```

## Error Handling

Common error scenarios and solutions:

| Error | Cause | Solution |
|-------|-------|----------|
| `Workflow 'name' not found` | Workflow not registered | Use `flux workflow register` first |
| `Connection refused` | Server not running | Start server with `flux start server` |
| `Invalid JSON input` | Malformed input data | Validate JSON syntax |
| `Execution 'id' not found` | Invalid execution ID | Check ID from workflow run output |

## See Also

### Learning Resources
- **[Your First Workflow Tutorial](../tutorials/your-first-workflow.md)** - Step-by-step guide to creating workflows
- **[Working with Tasks Tutorial](../tutorials/working-with-tasks.md)** - Learn task composition patterns
- **[Parallel Processing Tutorial](../tutorials/parallel-processing.md)** - Optimize workflow performance

### Core Concepts
- **[Basic Concepts: Workflows](../getting-started/basic_concepts.md)** - Understanding workflow fundamentals
- **[Core Concepts: Workflow Management](../core-concepts/workflow-management.md)** - Advanced workflow patterns
- **[Execution Model](../core-concepts/execution-model.md)** - How workflows execute

### Related CLI Commands
- **[Service Commands](start.md)** - Start Flux server and workers
- **[Secrets Management](secrets.md)** - Manage workflow secrets

### Reference
- **[Getting Started Guide](../getting-started/quick-start-guide.md)** - Quick start with workflows
- **[Troubleshooting Guide](../tutorials/troubleshooting.md)** - Common workflow issues
- **[Best Practices](../tutorials/best-practices.md)** - Production workflow guidelines
