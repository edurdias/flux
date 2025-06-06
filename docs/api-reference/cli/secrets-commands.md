# Secrets Commands

This page covers CLI commands for managing secrets in Flux, including creation, retrieval, and lifecycle management.

## Overview

Flux provides secure secret management capabilities through the CLI:

- **Secret Storage**: Securely store sensitive configuration values
- **Access Control**: Control who can access which secrets
- **Lifecycle Management**: Create, update, rotate, and delete secrets
- **Integration**: Use secrets in workflows and tasks

## Secret Management Commands

### flux secret create

Create a new secret or update an existing one.

```bash
flux secret create [OPTIONS] SECRET_NAME
```

**Arguments:**
- `SECRET_NAME`: Name of the secret (must be unique)

**Options:**
- `--value TEXT`: Secret value (prompted if not provided)
- `--file PATH`: Read secret value from file
- `--description TEXT`: Description of the secret
- `--tags TEXT`: Comma-separated list of tags
- `--expires TEXT`: Expiration date (ISO format or relative)
- `--force`: Overwrite existing secret without confirmation

**Examples:**

```bash
# Create secret with prompted value
flux secret create database-password

# Create secret with inline value (not recommended for production)
flux secret create --value "secret123" api-key

# Create secret from file
flux secret create --file ./cert.pem ssl-certificate

# Create secret with metadata
flux secret create --description "Production DB password" \
                   --tags "database,production" \
                   --expires "2024-12-31" \
                   db-prod-password

# Force overwrite existing secret
flux secret create --force --value "new-value" existing-secret
```

### flux secret list

List all secrets (values are not displayed for security).

```bash
flux secret list [OPTIONS]
```

**Options:**
- `--tag TEXT`: Filter by tag
- `--format TEXT`: Output format (table, json, yaml)
- `--show-values`: Show secret values (requires elevated permissions)
- `--expired`: Show only expired secrets
- `--expiring TEXT`: Show secrets expiring within timeframe

**Examples:**

```bash
# List all secrets
flux secret list

# List secrets with specific tag
flux secret list --tag "production"

# List in JSON format
flux secret list --format json

# List expired secrets
flux secret list --expired

# List secrets expiring in next 30 days
flux secret list --expiring "30d"

# Show secret values (requires special permissions)
flux secret list --show-values
```

### flux secret show

Show detailed information about a specific secret.

```bash
flux secret show [OPTIONS] SECRET_NAME
```

**Arguments:**
- `SECRET_NAME`: Name of the secret

**Options:**
- `--format TEXT`: Output format (table, json, yaml)
- `--show-value`: Show the secret value
- `--show-history`: Show modification history
- `--show-usage`: Show where secret is used

**Examples:**

```bash
# Show secret metadata
flux secret show database-password

# Show secret with value
flux secret show --show-value api-key

# Show secret with history and usage
flux secret show --show-history \
                 --show-usage \
                 ssl-certificate

# Show in JSON format
flux secret show --format json database-config
```

### flux secret update

Update an existing secret's value or metadata.

```bash
flux secret update [OPTIONS] SECRET_NAME
```

**Arguments:**
- `SECRET_NAME`: Name of the secret to update

**Options:**
- `--value TEXT`: New secret value
- `--file PATH`: Read new value from file
- `--description TEXT`: Update description
- `--tags TEXT`: Update tags (replaces existing)
- `--add-tag TEXT`: Add a tag (keeps existing)
- `--remove-tag TEXT`: Remove a tag
- `--expires TEXT`: Update expiration date
- `--no-expire`: Remove expiration

**Examples:**

```bash
# Update secret value
flux secret update --value "new-password" database-password

# Update from file
flux secret update --file ./new-cert.pem ssl-certificate

# Update metadata only
flux secret update --description "Updated description" \
                   --add-tag "v2" \
                   api-key

# Extend expiration
flux secret update --expires "2025-12-31" temp-token

# Remove expiration
flux secret update --no-expire permanent-key
```

### flux secret delete

Delete a secret from the system.

```bash
flux secret delete [OPTIONS] SECRET_NAME
```

**Arguments:**
- `SECRET_NAME`: Name of the secret to delete

**Options:**
- `--force`: Skip confirmation prompt
- `--cascade`: Delete even if used by workflows

**Examples:**

```bash
# Delete with confirmation
flux secret delete old-password

# Force delete without confirmation
flux secret delete --force temp-secret

# Delete secret even if in use
flux secret delete --cascade --force deprecated-key
```

### flux secret rotate

Rotate a secret by generating a new value.

```bash
flux secret rotate [OPTIONS] SECRET_NAME
```

**Arguments:**
- `SECRET_NAME`: Name of the secret to rotate

**Options:**
- `--type TEXT`: Secret type (password, token, key)
- `--length INT`: Length for generated secrets
- `--charset TEXT`: Character set for generation
- `--custom-value TEXT`: Use custom value instead of generating
- `--keep-history`: Keep previous value in history

**Examples:**

```bash
# Rotate with auto-generated password
flux secret rotate database-password

# Rotate with specific length and type
flux secret rotate --type token \
                   --length 32 \
                   api-token

# Rotate with custom character set
flux secret rotate --charset "a-zA-Z0-9" \
                   --length 16 \
                   session-key

# Rotate with custom value
flux secret rotate --custom-value "new-secret-value" \
                   manual-secret
```

## Secret Access Commands

### flux secret get

Retrieve a secret value for use in scripts or automation.

```bash
flux secret get [OPTIONS] SECRET_NAME
```

**Arguments:**
- `SECRET_NAME`: Name of the secret

**Options:**
- `--output TEXT`: Output format (value, json, env)
- `--quiet`: Only output the value

**Examples:**

```bash
# Get secret value
flux secret get database-password

# Get as environment variable format
flux secret get --output env DATABASE_URL

# Get quietly for scripts
SECRET=$(flux secret get --quiet api-key)

# Get as JSON
flux secret get --output json ssl-config
```

### flux secret export

Export secrets for backup or migration.

```bash
flux secret export [OPTIONS]
```

**Options:**
- `--tag TEXT`: Export secrets with specific tag
- `--pattern TEXT`: Export secrets matching pattern
- `--format TEXT`: Export format (json, yaml, env)
- `--output PATH`: Output file path
- `--encrypt`: Encrypt exported data
- `--password TEXT`: Encryption password

**Examples:**

```bash
# Export all secrets
flux secret export --output secrets-backup.json

# Export production secrets
flux secret export --tag "production" \
                   --output prod-secrets.yaml \
                   --format yaml

# Export with encryption
flux secret export --encrypt \
                   --password "backup-password" \
                   --output encrypted-secrets.json

# Export secrets matching pattern
flux secret export --pattern "api-*" \
                   --output api-secrets.env \
                   --format env
```

### flux secret import

Import secrets from a backup or migration file.

```bash
flux secret import [OPTIONS] FILE_PATH
```

**Arguments:**
- `FILE_PATH`: Path to the import file

**Options:**
- `--format TEXT`: Import format (json, yaml, env)
- `--decrypt`: Decrypt imported data
- `--password TEXT`: Decryption password
- `--dry-run`: Show what would be imported
- `--overwrite`: Overwrite existing secrets
- `--prefix TEXT`: Add prefix to imported secret names

**Examples:**

```bash
# Import from JSON backup
flux secret import secrets-backup.json

# Import with decryption
flux secret import --decrypt \
                   --password "backup-password" \
                   encrypted-secrets.json

# Dry run to preview import
flux secret import --dry-run migration-secrets.yaml

# Import with prefix to avoid conflicts
flux secret import --prefix "imported-" \
                   --overwrite \
                   external-secrets.json
```

## Secret Validation Commands

### flux secret validate

Validate secret configuration and usage.

```bash
flux secret validate [OPTIONS]
```

**Options:**
- `--secret TEXT`: Validate specific secret
- `--check-expiry`: Check for expired/expiring secrets
- `--check-usage`: Validate secret usage in workflows
- `--fix`: Attempt to fix validation issues

**Examples:**

```bash
# Validate all secrets
flux secret validate

# Validate specific secret
flux secret validate --secret database-password

# Check for expiry issues
flux secret validate --check-expiry

# Validate and fix issues
flux secret validate --check-usage --fix
```

### flux secret audit

Audit secret access and modifications.

```bash
flux secret audit [OPTIONS]
```

**Options:**
- `--secret TEXT`: Audit specific secret
- `--user TEXT`: Filter by user
- `--action TEXT`: Filter by action (create, read, update, delete)
- `--since TEXT`: Show audit logs since timestamp
- `--format TEXT`: Output format (table, json, csv)

**Examples:**

```bash
# Show all audit logs
flux secret audit

# Audit specific secret
flux secret audit --secret "api-key"

# Show access by specific user
flux secret audit --user "john.doe"

# Show modifications in last 24 hours
flux secret audit --action "update" \
                  --since "24h ago"

# Export audit log as CSV
flux secret audit --format csv \
                  --since "7d ago" > audit.csv
```

## Secret Templates

### flux secret template

Manage secret templates for consistent secret creation.

```bash
flux secret template [OPTIONS] COMMAND
```

**Commands:**
- `create`: Create a new template
- `list`: List available templates
- `show`: Show template details
- `apply`: Apply template to create secrets

**Examples:**

```bash
# Create database secret template
flux secret template create database \
  --fields "host,port,username,password,database" \
  --tags "database" \
  --description "Database connection secrets"

# List templates
flux secret template list

# Apply template
flux secret template apply database \
  --name "prod-db" \
  --host "db.example.com" \
  --port "5432" \
  --username "app_user" \
  --password "secure_password" \
  --database "production"
```

## Global Options

All secret commands support these global options:

- `--server TEXT`: Flux server URL
- `--token TEXT`: Authentication token
- `--config PATH`: Configuration file path
- `--vault-backend TEXT`: Secret storage backend
- `--encryption-key TEXT`: Local encryption key
- `--verbose`: Enable verbose output
- `--quiet`: Suppress non-essential output

## Security Considerations

### Best Practices

1. **Never pass secrets as command line arguments** in production
2. **Use files or environment variables** for secret values
3. **Enable audit logging** for compliance
4. **Rotate secrets regularly** using scheduled workflows
5. **Use least-privilege access** for secret permissions

### Environment Variables

- `FLUX_SECRET_VAULT`: Default vault backend
- `FLUX_SECRET_KEY`: Default encryption key
- `FLUX_SECRET_TIMEOUT`: Operation timeout

### Vault Backends

Flux supports multiple secret storage backends:

- **Local**: File-based storage (development only)
- **HashiCorp Vault**: Enterprise secret management
- **AWS Secrets Manager**: AWS-native secret storage
- **Azure Key Vault**: Azure-native secret storage
- **Google Secret Manager**: GCP-native secret storage

## Error Handling

Common error scenarios and solutions:

### Permission Denied
```bash
# Error: Permission denied
# Solution: Check authentication and permissions
flux secret list --verbose
```

### Secret Not Found
```bash
# Error: Secret 'api-key' not found
# Solution: Verify secret name and check listing
flux secret list | grep api-key
```

### Encryption Issues
```bash
# Error: Failed to decrypt secret
# Solution: Verify encryption key and backend configuration
flux secret validate --secret encrypted-data
```

## See Also

- [Workflow Commands](workflow-commands.md) - Workflow management
- [Service Commands](service-commands.md) - Server management
- [Configuration Reference](../../reference/configuration/) - Configuration options
- [Security Guide](../../user-guide/security/) - Security best practices
