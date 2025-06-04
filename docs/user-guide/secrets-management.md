# Secrets Management

Flux provides comprehensive secrets management capabilities to securely handle sensitive data like API keys, passwords, tokens, and other confidential information in your workflows. This guide covers CLI operations, HTTP API usage, task-level secret requests, and security best practices.

## Overview

Flux's secrets management system provides:

- **Secure Storage**: Encrypted storage of sensitive data
- **Multiple Access Methods**: CLI, HTTP API, and task-level access
- **Fine-grained Access Control**: Control which workflows and tasks can access specific secrets
- **Audit Logging**: Track secret access and modifications
- **Environment Integration**: Easy integration with existing secret management systems

## CLI Secrets Operations

### Setting Secrets

Store secrets securely using the Flux CLI:

```bash
# Set a simple string secret
flux secrets set API_KEY "your-api-key-here"

# Set a secret from file
flux secrets set SSL_CERT --from-file /path/to/certificate.pem

# Set a secret from stdin (useful for scripts)
echo "secret-value" | flux secrets set DB_PASSWORD --from-stdin

# Set a JSON secret
flux secrets set CONFIG '{"host": "db.example.com", "port": 5432}'

# Set a secret with description
flux secrets set WEBHOOK_URL "https://hooks.example.com/webhook" --description "External webhook endpoint"
```

### Retrieving Secrets

Access secrets through the CLI:

```bash
# Get a secret value
flux secrets get API_KEY

# Get secret with metadata
flux secrets get API_KEY --show-metadata

# Get multiple secrets
flux secrets get API_KEY DB_PASSWORD WEBHOOK_URL

# Export secrets to environment variables
eval $(flux secrets export API_KEY DB_PASSWORD)
```

### Listing Secrets

View available secrets:

```bash
# List all secrets (names only)
flux secrets list

# List secrets with metadata
flux secrets list --show-metadata

# Filter secrets by pattern
flux secrets list --pattern "API_*"

# List secrets with usage information
flux secrets list --show-usage
```

### Removing Secrets

Delete secrets when no longer needed:

```bash
# Remove a single secret
flux secrets remove API_KEY

# Remove multiple secrets
flux secrets remove API_KEY DB_PASSWORD

# Remove with confirmation prompt
flux secrets remove PROD_SECRET --confirm

# Force removal without confirmation
flux secrets remove TEMP_SECRET --force
```

### Secret Rotation

Rotate secrets for security:

```bash
# Rotate a secret (generates new value)
flux secrets rotate API_KEY

# Rotate with custom value
flux secrets rotate API_KEY --value "new-api-key-value"

# Schedule rotation (if supported by secret type)
flux secrets rotate API_KEY --schedule "0 2 * * 0"  # Weekly on Sunday at 2 AM
```

## HTTP API for Secrets

### API Endpoints

Flux provides RESTful endpoints for secret management:

```python
import httpx
import json

class FluxSecretsAPI:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient()

    async def create_secret(self, name: str, value: str, description: str = None):
        """Create a new secret"""
        payload = {"name": name, "value": value}
        if description:
            payload["description"] = description

        response = await self.client.post(
            f"{self.base_url}/api/v1/secrets",
            json=payload
        )
        return response.json()

    async def get_secret(self, name: str):
        """Retrieve a secret value"""
        response = await self.client.get(
            f"{self.base_url}/api/v1/secrets/{name}"
        )
        return response.json()

    async def list_secrets(self, pattern: str = None):
        """List available secrets"""
        params = {}
        if pattern:
            params["pattern"] = pattern

        response = await self.client.get(
            f"{self.base_url}/api/v1/secrets",
            params=params
        )
        return response.json()

    async def update_secret(self, name: str, value: str):
        """Update an existing secret"""
        response = await self.client.put(
            f"{self.base_url}/api/v1/secrets/{name}",
            json={"value": value}
        )
        return response.json()

    async def delete_secret(self, name: str):
        """Delete a secret"""
        response = await self.client.delete(
            f"{self.base_url}/api/v1/secrets/{name}"
        )
        return response.json()

# Usage example
async def manage_secrets_via_api():
    api = FluxSecretsAPI()

    # Create a secret
    await api.create_secret(
        name="DATABASE_URL",
        value="postgresql://user:pass@host:5432/db",
        description="Production database connection string"
    )

    # List secrets
    secrets = await api.list_secrets()
    print(f"Available secrets: {[s['name'] for s in secrets['secrets']]}")

    # Get secret value
    secret = await api.get_secret("DATABASE_URL")
    print(f"Secret value: {secret['value']}")
```

### Batch Operations

Perform bulk secret operations:

```python
async def bulk_secret_operations(api: FluxSecretsAPI):
    """Perform bulk secret operations"""

    # Bulk create secrets
    secrets_to_create = [
        {"name": "API_KEY_1", "value": "key1", "description": "Service A API key"},
        {"name": "API_KEY_2", "value": "key2", "description": "Service B API key"},
        {"name": "API_KEY_3", "value": "key3", "description": "Service C API key"}
    ]

    for secret in secrets_to_create:
        await api.create_secret(**secret)

    # Bulk retrieve secrets
    secret_names = ["API_KEY_1", "API_KEY_2", "API_KEY_3"]
    secrets = {}

    for name in secret_names:
        secret = await api.get_secret(name)
        secrets[name] = secret["value"]

    return secrets
```

## Task-Level Secret Requests

### Basic Secret Access

Access secrets directly within tasks:

```python
from flux import task, workflow, ExecutionContext
from flux.secret_managers import get_secret

@task
async def database_task(data: dict):
    """Task that accesses database using secret"""
    # Retrieve database connection string
    db_url = await get_secret("DATABASE_URL")

    # Use the secret to connect to database
    connection = await create_database_connection(db_url)

    try:
        # Perform database operations
        result = await connection.execute("SELECT * FROM users WHERE id = $1", data["user_id"])
        return result
    finally:
        await connection.close()

@task
async def api_integration_task(endpoint: str, payload: dict):
    """Task that calls external API with authentication"""
    # Retrieve API key
    api_key = await get_secret("EXTERNAL_API_KEY")

    # Make authenticated request
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient() as client:
        response = await client.post(endpoint, json=payload, headers=headers)
        return response.json()
```

### Conditional Secret Access

Access secrets based on runtime conditions:

```python
@task
async def conditional_secret_task(ctx: ExecutionContext, service_name: str):
    """Task that accesses different secrets based on conditions"""

    # Determine which secret to use based on execution context
    if ctx.environment == "production":
        secret_name = f"PROD_{service_name.upper()}_API_KEY"
    elif ctx.environment == "staging":
        secret_name = f"STAGING_{service_name.upper()}_API_KEY"
    else:
        secret_name = f"DEV_{service_name.upper()}_API_KEY"

    try:
        api_key = await get_secret(secret_name)
        return await call_service(service_name, api_key)
    except SecretNotFoundError:
        # Fall back to default secret
        default_secret = await get_secret("DEFAULT_API_KEY")
        return await call_service(service_name, default_secret)
```

### Multiple Secret Access

Handle tasks that require multiple secrets:

```python
@task
async def multi_secret_task(operation_type: str):
    """Task that uses multiple secrets"""

    # Define required secrets for different operations
    secret_requirements = {
        "database_backup": ["DB_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"],
        "email_notification": ["SMTP_PASSWORD", "EMAIL_API_KEY"],
        "full_sync": ["DB_PASSWORD", "REDIS_PASSWORD", "QUEUE_TOKEN"]
    }

    required_secrets = secret_requirements.get(operation_type, [])

    # Retrieve all required secrets
    secrets = {}
    for secret_name in required_secrets:
        secrets[secret_name] = await get_secret(secret_name)

    # Perform operation with all required secrets
    if operation_type == "database_backup":
        return await perform_database_backup(
            db_password=secrets["DB_PASSWORD"],
            s3_access_key=secrets["S3_ACCESS_KEY"],
            s3_secret_key=secrets["S3_SECRET_KEY"]
        )
    elif operation_type == "email_notification":
        return await send_email_notification(
            smtp_password=secrets["SMTP_PASSWORD"],
            api_key=secrets["EMAIL_API_KEY"]
        )
    # ... other operations
```

### Secret Caching and Optimization

Optimize secret access for performance:

```python
from functools import lru_cache
import asyncio

class SecretCache:
    def __init__(self, ttl_seconds: int = 300):  # 5-minute TTL
        self.cache = {}
        self.ttl = ttl_seconds

    async def get_secret(self, name: str):
        """Get secret with caching"""
        current_time = asyncio.get_event_loop().time()

        # Check if secret is in cache and not expired
        if name in self.cache:
            secret_data, timestamp = self.cache[name]
            if current_time - timestamp < self.ttl:
                return secret_data

        # Fetch secret and cache it
        secret_value = await get_secret(name)
        self.cache[name] = (secret_value, current_time)

        return secret_value

    def invalidate(self, name: str = None):
        """Invalidate cache entry or entire cache"""
        if name:
            self.cache.pop(name, None)
        else:
            self.cache.clear()

# Global secret cache instance
secret_cache = SecretCache()

@task
async def optimized_secret_task(service_name: str):
    """Task with optimized secret access"""
    # Use cached secret retrieval
    api_key = await secret_cache.get_secret(f"{service_name.upper()}_API_KEY")

    return await call_external_service(service_name, api_key)
```

## Best Practices

### Secret Naming Conventions

Establish consistent naming patterns:

```python
# Environment-based naming
PROD_DATABASE_PASSWORD
STAGING_DATABASE_PASSWORD
DEV_DATABASE_PASSWORD

# Service-based naming
PAYMENT_SERVICE_API_KEY
NOTIFICATION_SERVICE_TOKEN
ANALYTICS_SERVICE_SECRET

# Purpose-based naming
ENCRYPTION_KEY_PRIMARY
ENCRYPTION_KEY_BACKUP
SIGNING_KEY_JWT
WEBHOOK_SECRET_GITHUB
```

### Secret Rotation Strategy

Implement regular secret rotation:

```python
@task
async def secret_rotation_task(secret_name: str):
    """Task for automated secret rotation"""

    # Get current secret
    current_secret = await get_secret(secret_name)

    # Generate new secret based on type
    if secret_name.endswith("_API_KEY"):
        new_secret = await generate_api_key()
    elif secret_name.endswith("_PASSWORD"):
        new_secret = await generate_secure_password()
    elif secret_name.endswith("_TOKEN"):
        new_secret = await generate_token()
    else:
        raise ValueError(f"Unknown secret type for {secret_name}")

    # Update external service with new secret
    await update_external_service_secret(secret_name, new_secret)

    # Update Flux secret store
    await update_secret(secret_name, new_secret)

    # Verify new secret works
    await verify_secret_functionality(secret_name, new_secret)

    return {
        "secret_name": secret_name,
        "rotation_timestamp": datetime.utcnow().isoformat(),
        "status": "success"
    }

@workflow
async def weekly_secret_rotation(ctx: ExecutionContext):
    """Weekly secret rotation workflow"""

    secrets_to_rotate = [
        "EXTERNAL_API_KEY",
        "WEBHOOK_SECRET",
        "INTEGRATION_TOKEN"
    ]

    results = []
    for secret_name in secrets_to_rotate:
        try:
            result = await secret_rotation_task(secret_name)
            results.append(result)
        except Exception as e:
            results.append({
                "secret_name": secret_name,
                "status": "failed",
                "error": str(e)
            })

    return results
```

### Access Control and Permissions

Implement fine-grained access control:

```python
from flux.security import require_permission, SecretPermission

@task
@require_permission(SecretPermission.READ, "DATABASE_SECRETS")
async def database_access_task(query: str):
    """Task with database secret access permissions"""
    db_password = await get_secret("DATABASE_PASSWORD")
    # ... database operations

@task
@require_permission(SecretPermission.WRITE, "API_KEYS")
async def api_key_management_task(service_name: str, new_key: str):
    """Task with API key management permissions"""
    secret_name = f"{service_name.upper()}_API_KEY"
    await update_secret(secret_name, new_key)
```

### Error Handling for Secrets

Handle secret-related errors gracefully:

```python
from flux.errors import SecretNotFoundError, SecretAccessDeniedError

@task
async def robust_secret_task(service_name: str):
    """Task with robust secret error handling"""

    try:
        # Try primary secret
        primary_secret = await get_secret(f"{service_name}_PRIMARY_KEY")
        return await call_service_with_key(service_name, primary_secret)

    except SecretNotFoundError:
        # Fall back to secondary secret
        try:
            secondary_secret = await get_secret(f"{service_name}_SECONDARY_KEY")
            return await call_service_with_key(service_name, secondary_secret)
        except SecretNotFoundError:
            # Use default/public endpoint
            return await call_service_without_auth(service_name)

    except SecretAccessDeniedError as e:
        # Log access denial and use alternative approach
        print(f"Access denied for secret: {e}")
        return await use_alternative_authentication(service_name)

    except Exception as e:
        # Handle other secret-related errors
        print(f"Secret operation failed: {e}")
        return {"error": "secret_operation_failed", "service": service_name}
```

### Secret Validation and Testing

Validate secrets before use:

```python
@task
async def validated_secret_task(secret_name: str):
    """Task that validates secrets before use"""

    # Retrieve secret
    secret_value = await get_secret(secret_name)

    # Validate secret format
    if not validate_secret_format(secret_name, secret_value):
        raise ValueError(f"Invalid format for secret {secret_name}")

    # Test secret functionality
    if not await test_secret_connectivity(secret_name, secret_value):
        raise ValueError(f"Secret {secret_name} failed connectivity test")

    # Use validated secret
    return await perform_operation_with_secret(secret_name, secret_value)

async def validate_secret_format(secret_name: str, secret_value: str) -> bool:
    """Validate secret format based on naming convention"""

    if secret_name.endswith("_API_KEY"):
        # API keys should be alphanumeric and at least 20 characters
        return secret_value.isalnum() and len(secret_value) >= 20
    elif secret_name.endswith("_URL"):
        # URLs should start with http/https
        return secret_value.startswith(("http://", "https://"))
    elif secret_name.endswith("_EMAIL"):
        # Email format validation
        return "@" in secret_value and "." in secret_value

    return True  # Default to valid for unknown types

async def test_secret_connectivity(secret_name: str, secret_value: str) -> bool:
    """Test that secret works with external service"""

    try:
        if secret_name.endswith("_API_KEY"):
            # Test API key with simple endpoint
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.service.com/test",
                    headers={"Authorization": f"Bearer {secret_value}"},
                    timeout=5.0
                )
                return response.status_code == 200
        elif secret_name.endswith("_DATABASE_URL"):
            # Test database connection
            connection = await create_database_connection(secret_value)
            await connection.execute("SELECT 1")
            await connection.close()
            return True
    except Exception:
        return False

    return True  # Default to valid for unknown types
```

## Security Considerations

### Encryption and Storage

- **At-Rest Encryption**: All secrets are encrypted using AES-256 encryption
- **Key Management**: Encryption keys are managed separately from secret data
- **Secure Storage**: Secrets are stored in secure, access-controlled storage systems

### Network Security

- **TLS/SSL**: All secret transmissions use TLS encryption
- **Certificate Validation**: Strict certificate validation for all HTTPS connections
- **Network Isolation**: Secret management services operate in isolated network segments

### Access Auditing

- **Access Logging**: All secret access is logged with timestamps and user identification
- **Audit Trails**: Comprehensive audit trails for secret modifications and rotations
- **Monitoring**: Real-time monitoring for unusual secret access patterns

### Integration Security

- **Service Authentication**: All API access requires proper authentication
- **Rate Limiting**: API endpoints are rate-limited to prevent abuse
- **IP Restrictions**: Optional IP-based access restrictions for sensitive secrets
