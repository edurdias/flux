# Secrets Commands

The `flux secrets` command group provides secure management of sensitive data like API keys, passwords, and configuration values that workflows need during execution.

> 🔐 **Security Note:** Learn about [security best practices](../tutorials/best-practices.md#security-considerations) for managing secrets in production workflows.

## Command Overview

| Command | Description |
|---------|-------------|
| [`list`](#flux-secrets-list) | List all available secret names |
| [`set`](#flux-secrets-set) | Create or update a secret value |
| [`get`](#flux-secrets-get) | Retrieve a secret value (with confirmation) |
| [`remove`](#flux-secrets-remove) | Permanently delete a secret |

---

## `flux secrets list`

List all available secrets, showing only the secret names (not the actual values) for security.

### Usage

```bash
flux secrets list
```

### Examples

**List all secrets:**
```bash
flux secrets list
```

Output:
```
Available secrets:
  - api_key
  - database_password
  - webhook_token
  - smtp_credentials
```

**No secrets available:**
```bash
flux secrets list
```

Output:
```
No secrets found.
```

### Security Features

- Only secret **names** are displayed, never values
- No options to accidentally expose secret content
- Safe for use in logs and shared environments

---

## `flux secrets set`

Create a new secret or update an existing secret with a new value.

### Usage

```bash
flux secrets set NAME VALUE
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `NAME` | string | Yes | Name identifier for the secret |
| `VALUE` | string | Yes | Secret value to store |

### Examples

**Set a new API key:**
```bash
flux secrets set api_key "sk-1234567890abcdef"
```

Output:
```
Secret 'api_key' has been set successfully.
```

**Update an existing secret:**
```bash
flux secrets set database_password "new_secure_password_123"
```

Output:
```
Secret 'database_password' has been set successfully.
```

**Set complex JSON credentials:**
```bash
flux secrets set aws_credentials '{"access_key": "AKIA...", "secret_key": "wJalr..."}'
```

**Set multiline secrets:**
```bash
flux secrets set ssl_certificate "-----BEGIN CERTIFICATE-----
MIIEpDCCAowCCQDUr1...
-----END CERTIFICATE-----"
```

### Best Practices

**Secret Naming:**
- Use descriptive, hierarchical names: `api_keys/openai`, `db/production/password`
- Avoid spaces; use underscores or hyphens: `webhook_token`, `smtp-password`
- Include environment context: `prod_api_key`, `staging_db_url`

**Value Security:**
- Set secrets in secure environments only
- Use single quotes to prevent shell interpretation
- Avoid storing secrets in shell history (see [Security Considerations](#security-considerations))

---

## `flux secrets get`

Retrieve and display a secret value. This command includes a safety confirmation prompt since it exposes sensitive data.

### Usage

```bash
flux secrets get NAME
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `NAME` | string | Yes | Name of the secret to retrieve |

### Examples

**Get a secret value:**
```bash
flux secrets get api_key
```

Output:
```
Are you sure you want to display the secret 'api_key'? [y/N]: y
Secret 'api_key': sk-1234567890abcdef
```

**Cancel secret retrieval:**
```bash
flux secrets get database_password
```

Output:
```
Are you sure you want to display the secret 'database_password'? [y/N]: n
Operation cancelled.
```

**Secret not found:**
```bash
flux secrets get nonexistent_key
```

Output:
```
Are you sure you want to display the secret 'nonexistent_key'? [y/N]: y
Secret not found: Secret 'nonexistent_key' not found in database
```

### Security Features

- **Confirmation prompt** prevents accidental exposure
- **Clear warning** about displaying sensitive data
- **Secure terminal** recommendation for usage
- **Exit on cancellation** to prevent data leaks

### Use Cases

- **Development debugging** when secrets aren't working
- **Migration** and backup operations
- **Testing** secret retrieval in secure environments
- **Troubleshooting** workflow authentication issues

---

## `flux secrets remove`

Permanently delete a secret from the secure storage. This action cannot be undone.

### Usage

```bash
flux secrets remove NAME
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `NAME` | string | Yes | Name of the secret to delete |

### Examples

**Remove a secret:**
```bash
flux secrets remove old_api_key
```

Output:
```
Secret 'old_api_key' has been removed successfully.
```

**Remove a non-existent secret:**
```bash
flux secrets remove missing_secret
```

Output:
```
Error removing secret: Secret 'missing_secret' not found
```

### Important Notes

- **Permanent deletion**: Removed secrets cannot be recovered
- **No confirmation prompt**: Command executes immediately
- **Workflow impact**: Active workflows using the secret will fail
- **Clean removal**: No trace of the secret remains in storage

---

## Secret Storage

### Storage Backend

Flux uses a secure storage backend for secrets:

- **Encryption**: All secrets are encrypted at rest
- **Access control**: Only authorized Flux processes can access secrets
- **Isolation**: Secrets are isolated from regular workflow data
- **Persistence**: Secrets survive server restarts and updates

### Storage Location

Default secret storage location (configurable):
```
~/.flux/secrets.db
```

### Database Security

- **SQLite encryption**: Database file is encrypted
- **File permissions**: Restricted to owner only (600)
- **No plain text**: Secret values never stored in plain text
- **Secure deletion**: Removed secrets are cryptographically wiped

## Using Secrets in Workflows

### Accessing Secrets

Secrets are accessed in workflows through the secret manager:

```python
from flux import workflow, task
from flux.secret_managers import SecretManager

@task
def api_call_task():
    # Get secret in task
    secret_manager = SecretManager.current()
    secrets = secret_manager.get(["api_key"])
    api_key = secrets["api_key"]

    # Use secret for API call
    response = requests.get(
        "https://api.example.com/data",
        headers={"Authorization": f"Bearer {api_key}"}
    )
    return response.json()

@workflow
def data_processing_workflow():
    result = api_call_task()
    return result
```

### Secret Injection

Workflows can request secrets by name:

```python
@task
def database_task():
    secret_manager = SecretManager.current()
    db_secrets = secret_manager.get([
        "database_host",
        "database_password",
        "database_user"
    ])

    connection = create_connection(
        host=db_secrets["database_host"],
        user=db_secrets["database_user"],
        password=db_secrets["database_password"]
    )
    return connection.execute("SELECT * FROM users")
```

### Multiple Secrets

Request multiple secrets efficiently:

```python
@task
def email_notification_task():
    secret_manager = SecretManager.current()
    email_secrets = secret_manager.get([
        "smtp_host",
        "smtp_port",
        "smtp_username",
        "smtp_password"
    ])

    send_email(
        host=email_secrets["smtp_host"],
        port=int(email_secrets["smtp_port"]),
        username=email_secrets["smtp_username"],
        password=email_secrets["smtp_password"],
        message="Workflow completed successfully"
    )
```

## Security Considerations

### Command Line Security

**Avoid shell history exposure:**
```bash
# Use space prefix to avoid history (in bash with HISTCONTROL=ignorespace)
 flux secrets set api_key "secret_value"

# Or clear history after setting secrets
flux secrets set api_key "secret_value"
history -d $((HISTCMD-1))

# Use environment variables
export SECRET_VALUE="my_secret"
flux secrets set api_key "$SECRET_VALUE"
unset SECRET_VALUE
```

**Secure input methods:**
```bash
# Read from file
flux secrets set ssl_cert "$(cat /secure/path/certificate.pem)"

# Read from secure prompt (requires additional tooling)
read -s -p "Enter API key: " API_KEY
flux secrets set api_key "$API_KEY"
unset API_KEY
```

### Access Control

**File permissions:**
```bash
# Ensure secret database is secure
chmod 600 ~/.flux/secrets.db
ls -la ~/.flux/secrets.db
# Should show: -rw------- (owner read/write only)
```

**User isolation:**
```bash
# Run Flux as dedicated user
sudo -u flux flux secrets set production_api_key "value"

# Restrict access to Flux user only
sudo chown flux:flux ~/.flux/secrets.db
sudo chmod 600 ~/.flux/secrets.db
```

### Production Security

**Environment separation:**
```bash
# Use different secret namespaces for environments
flux secrets set prod_database_url "postgresql://prod-server/db"
flux secrets set staging_database_url "postgresql://staging-server/db"
flux secrets set dev_database_url "postgresql://localhost/dev_db"
```

**Backup and recovery:**
```bash
# Backup encrypted secrets database
cp ~/.flux/secrets.db ~/.flux/backups/secrets-$(date +%Y%m%d).db

# Secure backup storage
chmod 600 ~/.flux/backups/secrets-*.db
```

**Audit and monitoring:**
```bash
# Monitor secret access
tail -f ~/.flux/logs/secrets.log

# Track secret usage in workflows
grep "secret_manager.get" ~/.flux/logs/workflow.log
```

## Secret Management Patterns

### Development vs Production

**Development secrets:**
```bash
flux secrets set dev_api_key "test_key_123"
flux secrets set dev_database_url "sqlite:///dev.db"
```

**Production secrets:**
```bash
flux secrets set prod_api_key "sk-live-production-key"
flux secrets set prod_database_url "postgresql://secure-host/prod_db"
```

### Secret Rotation

**Rotating API keys:**
```bash
# Set new key
flux secrets set api_key_new "new_api_key_value"

# Update workflow to use new key
# Deploy workflow changes

# Remove old key after verification
flux secrets remove api_key_old
```

### Hierarchical Secrets

**Organize by service:**
```bash
flux secrets set aws/access_key "AKIA..."
flux secrets set aws/secret_key "wJal..."
flux secrets set database/prod/url "postgresql://..."
flux secrets set database/prod/password "secure_pass"
flux secrets set email/smtp/host "smtp.example.com"
flux secrets set email/smtp/password "email_pass"
```

## Troubleshooting

### Common Issues

**Secret not found during workflow execution:**
```bash
# Verify secret exists
flux secrets list

# Check exact secret name
flux secrets get secret_name
```

**Permission denied errors:**
```bash
# Check database permissions
ls -la ~/.flux/secrets.db

# Fix permissions
chmod 600 ~/.flux/secrets.db
```

**Database corruption:**
```bash
# Restore from backup
cp ~/.flux/backups/secrets-backup.db ~/.flux/secrets.db

# Verify integrity
sqlite3 ~/.flux/secrets.db "PRAGMA integrity_check;"
```

### Debugging Secret Access

**Enable secret manager logging:**
```python
import logging
logging.getLogger('flux.secret_managers').setLevel(logging.DEBUG)
```

**Test secret retrieval:**
```python
from flux.secret_managers import SecretManager

# Test in Python REPL
secret_manager = SecretManager.current()
try:
    secrets = secret_manager.get(["test_secret"])
    print("Secret retrieved successfully")
except Exception as e:
    print(f"Error: {e}")
```

## Migration and Backup

### Export Secrets

**Backup secret names:**
```bash
flux secrets list > secrets_backup.txt
```

**Manual migration:**
```bash
# Export and import individual secrets
OLD_VALUE=$(flux secrets get old_secret)
flux secrets set new_secret "$OLD_VALUE"
flux secrets remove old_secret
```

### Database Migration

**Copy to new system:**
```bash
# On source system
tar -czf flux_secrets.tar.gz ~/.flux/secrets.db

# On target system
tar -xzf flux_secrets.tar.gz -C ~/
chmod 600 ~/.flux/secrets.db
```

## See Also

### Learning Resources
- **[Working with Tasks Tutorial](../tutorials/working-with-tasks.md)** - Learn how to use secrets in tasks
- **[Best Practices](../tutorials/best-practices.md#security-considerations)** - Security best practices for secrets
- **[FAQ](../tutorials/faq.md#security-and-secrets)** - Common questions about secrets

### Core Concepts
- **[Basic Concepts](../getting-started/basic_concepts.md#secrets)** - Understanding secrets in workflows
- **[Error Handling](../core-concepts/error-handling.md)** - Handling secret-related errors
- **[Execution Context](../core-concepts/execution-model.md#execution-context)** - How secrets are passed to tasks

### Related Commands
- **[Workflow Commands](workflow.md)** - Running workflows that use secrets
- **[Service Commands](start.md)** - Starting services that secure secrets

### Examples
- **[Your First Workflow Tutorial](../tutorials/your-first-workflow.md)** - Basic workflow without secrets
- **[Troubleshooting Guide](../tutorials/troubleshooting.md#secrets-troubleshooting)** - Common secret issues
