# Secrets Management

Flux provides a comprehensive secrets management system to securely handle sensitive data like API keys, passwords, database credentials, and other confidential information during workflow execution.

## Overview

The secrets management system in Flux allows you to:

- **Secure Storage**: Store sensitive data encrypted in the database
- **Task Integration**: Request secrets in tasks with automatic injection
- **Access Control**: Manage which workflows and tasks can access specific secrets
- **CLI Management**: Create, update, and manage secrets via command line
- **API Management**: Programmatic secrets management through HTTP API
- **Multiple Data Types**: Support for strings, complex objects, and JSON data

## Basic Usage

### Requesting Secrets in Tasks

Tasks can request specific secrets using the `secret_requests` parameter:

```python
from flux import task, workflow, ExecutionContext
from typing import Any

@task.with_options(secret_requests=["API_KEY"])
async def api_call_task(secrets: dict[str, Any] = {}):
    api_key = secrets["API_KEY"]
    # Use the API key securely
    headers = {"Authorization": f"Bearer {api_key}"}
    # Make API call
    return response_data

@workflow
async def secure_workflow(ctx: ExecutionContext):
    result = await api_call_task()
    return result
```

### Multiple Secrets

Request multiple secrets in a single task:

```python
@task.with_options(secret_requests=["DATABASE_URL", "API_KEY", "JWT_SECRET"])
async def database_task(secrets: dict[str, Any] = {}):
    db_url = secrets["DATABASE_URL"]
    api_key = secrets["API_KEY"]
    jwt_secret = secrets["JWT_SECRET"]

    # Use all secrets as needed
    return processed_data
```

### Workflow-Level Secrets

Configure secrets at the workflow level:

```python
@workflow.with_options(secret_requests=["MASTER_KEY", "CONFIG_TOKEN"])
async def workflow_with_secrets(ctx: ExecutionContext):
    # Secrets are available to all tasks in this workflow
    result1 = await task_one()  # Has access to workflow secrets
    result2 = await task_two()  # Also has access to workflow secrets
    return [result1, result2]
```

## Managing Secrets

### Command Line Interface

Flux provides a comprehensive CLI for secrets management:

#### List All Secrets
```bash
# Shows only secret names, not values
flux secrets list
```

#### Set/Create Secrets
```bash
# Create or update a secret
flux secrets set API_KEY "your-api-key-value"
flux secrets set DATABASE_URL "postgresql://user:pass@host/db"

# Set complex data as JSON
flux secrets set CONFIG '{"host": "api.example.com", "timeout": 30}'
```

#### Retrieve Secret Values
```bash
# Get a secret value (use cautiously - displays in terminal)
flux secrets get API_KEY

# Confirmation prompt for security
# Are you sure you want to display the secret 'API_KEY'? [y/N]: y
# Secret 'API_KEY': your-api-key-value
```

#### Remove Secrets
```bash
# Delete a secret permanently
flux secrets remove API_KEY
```

### HTTP API Management

When running the Flux server, you can manage secrets via REST API:

#### List Secrets (Names Only)
```bash
curl -X GET 'http://localhost:8000/admin/secrets'
```

#### Create/Update Secret
```bash
curl -X POST 'http://localhost:8000/admin/secrets' \
     -H 'Content-Type: application/json' \
     -d '{"name": "API_KEY", "value": "your-api-key-value"}'
```

#### Retrieve Secret Value
```bash
curl -X GET 'http://localhost:8000/admin/secrets/API_KEY'
```

#### Delete Secret
```bash
curl -X DELETE 'http://localhost:8000/admin/secrets/API_KEY'
```

### Programmatic Management

Use the SecretManager directly in your code:

```python
from flux.secret_managers import SecretManager

# Get the current secret manager
secret_manager = SecretManager.current()

# Save secrets
secret_manager.save("API_KEY", "your-api-key")
secret_manager.save("CONFIG", {"host": "api.com", "port": 443})

# Retrieve secrets
secrets = secret_manager.get(["API_KEY", "CONFIG"])
api_key = secrets["API_KEY"]
config = secrets["CONFIG"]

# List all secret names
all_secrets = secret_manager.all()
print(f"Available secrets: {all_secrets}")

# Remove secrets
secret_manager.remove("OLD_SECRET")
```

## Advanced Usage

### Complex Data Types

Secrets support various data types beyond simple strings:

```python
# JSON-serializable objects
config_data = {
    "database": {
        "host": "db.example.com",
        "port": 5432,
        "credentials": {
            "username": "app_user",
            "password": "secure_password"
        }
    },
    "api": {
        "endpoints": ["api1.com", "api2.com"],
        "timeout": 30,
        "retries": 3
    }
}

secret_manager.save("APP_CONFIG", config_data)

# Use in tasks
@task.with_options(secret_requests=["APP_CONFIG"])
async def configure_app(secrets: dict[str, Any] = {}):
    config = secrets["APP_CONFIG"]
    db_host = config["database"]["host"]
    api_endpoints = config["api"]["endpoints"]
    return setup_application(config)
```

### Conditional Secret Access

Handle optional secrets gracefully:

```python
@task.with_options(secret_requests=["API_KEY", "OPTIONAL_TOKEN"])
async def flexible_task(secrets: dict[str, Any] = {}):
    api_key = secrets["API_KEY"]  # Required

    # Optional secret with fallback
    optional_token = secrets.get("OPTIONAL_TOKEN")
    if optional_token:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Optional-Token": optional_token
        }
    else:
        headers = {"Authorization": f"Bearer {api_key}"}

    return make_request(headers)
```

### Secret Validation

Validate secrets before use:

```python
@task.with_options(secret_requests=["DATABASE_URL"])
async def validated_database_task(secrets: dict[str, Any] = {}):
    db_url = secrets["DATABASE_URL"]

    # Validate secret format
    if not db_url.startswith(("postgresql://", "mysql://", "sqlite://")):
        raise ValueError("Invalid database URL format")

    # Use validated secret
    connection = create_database_connection(db_url)
    return query_database(connection)
```

## Security Best Practices

### Secret Naming Conventions

```python
# Use clear, descriptive names
secret_manager.save("STRIPE_API_KEY", "sk_live_...")
secret_manager.save("AWS_ACCESS_KEY_ID", "AKIA...")
secret_manager.save("POSTGRES_CONNECTION_STRING", "postgresql://...")

# Environment-specific naming
secret_manager.save("PROD_DATABASE_URL", "...")
secret_manager.save("DEV_API_ENDPOINT", "...")
```

### Minimal Secret Exposure

```python
@task.with_options(secret_requests=["API_KEY"])
async def secure_api_task(data: str, secrets: dict[str, Any] = {}):
    api_key = secrets["API_KEY"]

    # Use secret immediately and don't store in variables longer than needed
    response = make_api_request(data, api_key)

    # Don't log or return secrets
    # ❌ DON'T: return {"api_key": api_key, "response": response}
    # ✅ DO: return {"response": response}

    return response
```

### Error Handling with Secrets

```python
@task.with_options(secret_requests=["SENSITIVE_API_KEY"])
async def safe_secret_task(secrets: dict[str, Any] = {}):
    try:
        api_key = secrets["SENSITIVE_API_KEY"]
        return perform_sensitive_operation(api_key)
    except KeyError:
        # Secret not found
        raise ValueError("Required secret 'SENSITIVE_API_KEY' not configured")
    except Exception as e:
        # Don't leak secret information in error messages
        # ❌ DON'T: raise Exception(f"Failed with key {api_key}: {e}")
        # ✅ DO: raise Exception(f"Authentication failed: {type(e).__name__}")
        raise Exception(f"Operation failed: {type(e).__name__}")
```

## Integration Examples

### Database Connections

```python
@task.with_options(secret_requests=["DATABASE_URL"])
async def database_operation(query: str, secrets: dict[str, Any] = {}):
    import asyncpg

    db_url = secrets["DATABASE_URL"]

    conn = await asyncpg.connect(db_url)
    try:
        result = await conn.fetch(query)
        return [dict(row) for row in result]
    finally:
        await conn.close()

@workflow
async def database_workflow(ctx: ExecutionContext[str]):
    results = await database_operation(ctx.input)
    return results
```

### External API Integration

```python
@task.with_options(secret_requests=["GITHUB_TOKEN"])
async def github_api_task(repo: str, secrets: dict[str, Any] = {}):
    import httpx

    token = secrets["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{repo}",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

@workflow
async def github_workflow(ctx: ExecutionContext[str]):
    repo_info = await github_api_task(ctx.input)
    return {
        "name": repo_info["name"],
        "stars": repo_info["stargazers_count"],
        "language": repo_info["language"]
    }
```

### Multi-Service Authentication

```python
@task.with_options(secret_requests=["AWS_CREDENTIALS", "SLACK_WEBHOOK"])
async def multi_service_task(data: dict, secrets: dict[str, Any] = {}):
    aws_creds = secrets["AWS_CREDENTIALS"]
    slack_webhook = secrets["SLACK_WEBHOOK"]

    # AWS operation
    import boto3
    s3_client = boto3.client(
        's3',
        aws_access_key_id=aws_creds["access_key"],
        aws_secret_access_key=aws_creds["secret_key"]
    )
    s3_result = s3_client.list_objects_v2(Bucket=data["bucket"])

    # Slack notification
    import httpx
    async with httpx.AsyncClient() as client:
        await client.post(slack_webhook, json={
            "text": f"Processed {len(s3_result.get('Contents', []))} objects"
        })

    return s3_result
```

## Storage and Encryption

### Database Storage

Secrets are stored in the SQLite database with the following characteristics:

- **Encrypted at Rest**: Values are encrypted before storage
- **Indexed Names**: Secret names are indexed for fast lookup
- **Atomic Operations**: All secret operations are transactional
- **Concurrent Safe**: Multiple processes can safely access secrets

### Storage Location

```bash
# Default database location
~/.flux/flux.db

# Custom location via environment variable
export FLUX_DATABASE_URL="sqlite:///path/to/custom.db"
```

## Troubleshooting

### Common Issues

**Secret Not Found Error**
```python
# Error: The following secrets were not found: ['MISSING_SECRET']
# Solution: Ensure secret is created before use
flux secrets set MISSING_SECRET "value"
```

**Permission Errors**
```bash
# Ensure database directory is writable
chmod 755 ~/.flux/
chmod 644 ~/.flux/flux.db
```

**Secret Value Type Issues**
```python
# For complex objects, ensure they're JSON-serializable
# ❌ Problem: Storing non-serializable objects
secret_manager.save("BAD_SECRET", lambda x: x)  # Functions can't be stored

# ✅ Solution: Store JSON-serializable data
secret_manager.save("GOOD_SECRET", {"key": "value", "list": [1, 2, 3]})
```

### Debugging Secret Access

```python
@task.with_options(secret_requests=["DEBUG_SECRET"])
async def debug_secrets_task(secrets: dict[str, Any] = {}):
    # Check if secret was properly injected
    print(f"Available secrets: {list(secrets.keys())}")

    if "DEBUG_SECRET" not in secrets:
        available = SecretManager.current().all()
        print(f"All secrets in database: {available}")
        raise ValueError("Secret not properly injected")

    return secrets["DEBUG_SECRET"]
```

## Migration and Backup

### Exporting Secrets

```python
# Export all secrets (be careful with security)
def export_secrets():
    secret_manager = SecretManager.current()
    all_names = secret_manager.all()
    all_secrets = secret_manager.get(all_names)

    # Write to secure file
    import json
    with open("secrets_backup.json", "w") as f:
        json.dump(all_secrets, f, indent=2)
```

### Importing Secrets

```python
# Import secrets from backup
def import_secrets(backup_file: str):
    import json
    secret_manager = SecretManager.current()

    with open(backup_file, "r") as f:
        secrets = json.load(f)

    for name, value in secrets.items():
        secret_manager.save(name, value)
```

For more information about integrating secrets with workflows and tasks, see the [Task System](../core-concepts/tasks.md) and [Workflow Management](../core-concepts/workflow-management.md) documentation.
