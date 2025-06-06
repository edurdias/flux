# Workflow Commands

This page covers CLI commands for workflow management, execution, and monitoring in Flux.

## Overview

Flux provides a comprehensive set of CLI commands for workflow operations:

- **Workflow Management**: Upload, list, and inspect workflows
- **Execution Control**: Start, stop, and monitor workflow executions
- **Status Monitoring**: Check execution status and retrieve results
- **Debugging**: Access logs and execution details

## Workflow Management Commands

### flux workflow upload

Upload a workflow file to the Flux server.

```bash
flux workflow upload [OPTIONS] WORKFLOW_FILE
```

**Arguments:**
- `WORKFLOW_FILE`: Path to the Python workflow file

**Options:**
- `--name TEXT`: Override workflow name (defaults to filename)
- `--description TEXT`: Workflow description
- `--tags TEXT`: Comma-separated list of tags
- `--force`: Overwrite existing workflow
- `--validate-only`: Validate workflow without uploading
- `--dry-run`: Show what would be uploaded

**Examples:**

```bash
# Upload a workflow file
flux workflow upload my_workflow.py

# Upload with custom name and description
flux workflow upload --name "data-pipeline" \
                    --description "Daily ETL pipeline" \
                    data_processing.py

# Upload with tags for organization
flux workflow upload --tags "etl,daily,production" \
                    data_pipeline.py

# Validate workflow without uploading
flux workflow upload --validate-only workflow.py

# Force overwrite existing workflow
flux workflow upload --force updated_workflow.py
```

### flux workflow list

List all workflows on the server.

```bash
flux workflow list [OPTIONS]
```

**Options:**
- `--tag TEXT`: Filter by tag
- `--status TEXT`: Filter by status (active, inactive, error)
- `--format TEXT`: Output format (table, json, yaml)
- `--limit INT`: Maximum number of results
- `--offset INT`: Skip first N results

**Examples:**

```bash
# List all workflows
flux workflow list

# List workflows with specific tag
flux workflow list --tag "production"

# List in JSON format
flux workflow list --format json

# List with pagination
flux workflow list --limit 10 --offset 20
```

### flux workflow show

Show detailed information about a specific workflow.

```bash
flux workflow show [OPTIONS] WORKFLOW_NAME
```

**Arguments:**
- `WORKFLOW_NAME`: Name of the workflow

**Options:**
- `--format TEXT`: Output format (table, json, yaml)
- `--show-code`: Include workflow source code
- `--show-executions INT`: Show last N executions

**Examples:**

```bash
# Show workflow details
flux workflow show my-workflow

# Show with source code
flux workflow show --show-code my-workflow

# Show in JSON format with recent executions
flux workflow show --format json \
                  --show-executions 5 \
                  my-workflow
```

### flux workflow delete

Delete a workflow from the server.

```bash
flux workflow delete [OPTIONS] WORKFLOW_NAME
```

**Arguments:**
- `WORKFLOW_NAME`: Name of the workflow to delete

**Options:**
- `--force`: Skip confirmation prompt
- `--cascade`: Delete all associated executions

**Examples:**

```bash
# Delete workflow with confirmation
flux workflow delete old-workflow

# Force delete without confirmation
flux workflow delete --force temp-workflow

# Delete workflow and all executions
flux workflow delete --cascade --force deprecated-workflow
```

## Execution Commands

### flux execute

Execute a workflow synchronously or asynchronously.

```bash
flux execute [OPTIONS] WORKFLOW_NAME
```

**Arguments:**
- `WORKFLOW_NAME`: Name of the workflow to execute

**Options:**
- `--input TEXT`: JSON input data
- `--input-file PATH`: File containing input data
- `--async`: Execute asynchronously
- `--timeout INT`: Execution timeout in seconds
- `--priority INT`: Execution priority (1-10)
- `--tags TEXT`: Comma-separated execution tags

**Examples:**

```bash
# Execute workflow synchronously
flux execute my-workflow

# Execute with JSON input
flux execute --input '{"param1": "value1"}' my-workflow

# Execute with input from file
flux execute --input-file input.json my-workflow

# Execute asynchronously
flux execute --async my-workflow

# Execute with timeout and priority
flux execute --timeout 300 \
            --priority 5 \
            my-workflow
```

### flux execution list

List workflow executions.

```bash
flux execution list [OPTIONS]
```

**Options:**
- `--workflow TEXT`: Filter by workflow name
- `--status TEXT`: Filter by status (running, completed, failed, cancelled)
- `--since TEXT`: Show executions since date/time
- `--limit INT`: Maximum number of results
- `--format TEXT`: Output format (table, json, yaml)

**Examples:**

```bash
# List all executions
flux execution list

# List executions for specific workflow
flux execution list --workflow my-workflow

# List running executions
flux execution list --status running

# List executions from last 24 hours
flux execution list --since "24h ago"

# List in JSON format with limit
flux execution list --format json --limit 50
```

### flux execution show

Show detailed information about a specific execution.

```bash
flux execution show [OPTIONS] EXECUTION_ID
```

**Arguments:**
- `EXECUTION_ID`: ID of the execution

**Options:**
- `--format TEXT`: Output format (table, json, yaml)
- `--show-logs`: Include execution logs
- `--show-events`: Include execution events
- `--follow`: Follow execution in real-time

**Examples:**

```bash
# Show execution details
flux execution show abc123

# Show with logs and events
flux execution show --show-logs \
                   --show-events \
                   abc123

# Follow execution in real-time
flux execution show --follow abc123
```

### flux execution cancel

Cancel a running execution.

```bash
flux execution cancel [OPTIONS] EXECUTION_ID
```

**Arguments:**
- `EXECUTION_ID`: ID of the execution to cancel

**Options:**
- `--force`: Force cancellation without graceful shutdown
- `--reason TEXT`: Cancellation reason

**Examples:**

```bash
# Cancel execution gracefully
flux execution cancel abc123

# Force cancel with reason
flux execution cancel --force \
                     --reason "Manual intervention required" \
                     abc123
```

### flux execution retry

Retry a failed execution.

```bash
flux execution retry [OPTIONS] EXECUTION_ID
```

**Arguments:**
- `EXECUTION_ID`: ID of the execution to retry

**Options:**
- `--from-task TEXT`: Retry from specific task
- `--reset-state`: Reset execution state
- `--new-input TEXT`: Override input data

**Examples:**

```bash
# Retry from beginning
flux execution retry abc123

# Retry from specific task
flux execution retry --from-task "process_data" abc123

# Retry with reset state and new input
flux execution retry --reset-state \
                    --new-input '{"retry": true}' \
                    abc123
```

## Monitoring Commands

### flux logs

View execution logs.

```bash
flux logs [OPTIONS] EXECUTION_ID
```

**Arguments:**
- `EXECUTION_ID`: ID of the execution

**Options:**
- `--task TEXT`: Filter logs by task name
- `--level TEXT`: Filter by log level (DEBUG, INFO, WARNING, ERROR)
- `--follow`: Follow logs in real-time
- `--tail INT`: Show last N lines
- `--since TEXT`: Show logs since timestamp

**Examples:**

```bash
# View all logs
flux logs abc123

# Follow logs in real-time
flux logs --follow abc123

# View last 100 lines for specific task
flux logs --task "data_processing" \
         --tail 100 \
         abc123

# View error logs from last hour
flux logs --level ERROR \
         --since "1h ago" \
         abc123
```

### flux status

Show system and execution status.

```bash
flux status [OPTIONS]
```

**Options:**
- `--execution-id TEXT`: Show status for specific execution
- `--verbose`: Show detailed status information
- `--format TEXT`: Output format (table, json, yaml)

**Examples:**

```bash
# Show system status
flux status

# Show detailed system status
flux status --verbose

# Show specific execution status
flux status --execution-id abc123

# Show status in JSON format
flux status --format json
```

## Global Options

All commands support these global options:

- `--server TEXT`: Flux server URL (default: localhost:8080)
- `--token TEXT`: Authentication token
- `--config PATH`: Configuration file path
- `--verbose`: Enable verbose output
- `--quiet`: Suppress non-essential output
- `--help`: Show help message

**Examples:**

```bash
# Use different server
flux --server https://flux.example.com workflow list

# Use authentication token
flux --token abc123xyz workflow execute my-workflow

# Use custom config file
flux --config /path/to/config.yaml status

# Enable verbose output
flux --verbose execution show abc123
```

## Configuration File

The CLI can use a configuration file to set default values:

```yaml
# ~/.flux/config.yaml
server: https://flux.example.com
token: your-auth-token
format: json
timeout: 300
```

## Environment Variables

The CLI respects these environment variables:

- `FLUX_SERVER`: Default server URL
- `FLUX_TOKEN`: Authentication token
- `FLUX_CONFIG`: Configuration file path
- `FLUX_TIMEOUT`: Default timeout

## Exit Codes

The CLI uses standard exit codes:

- `0`: Success
- `1`: General error
- `2`: Invalid arguments
- `3`: Server connection error
- `4`: Authentication error
- `5`: Resource not found

## See Also

- [Service Commands](service-commands.md) - Server and worker management
- [Secrets Commands](secrets-commands.md) - Secrets management
- [HTTP API](../http-api/) - REST API reference
- [Configuration Reference](../../reference/configuration/) - Configuration options
