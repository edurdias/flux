# CLI Overview

Flux provides a comprehensive command-line interface (CLI) for managing workflows, running services, and handling configuration. This guide covers the basic usage, installation, and configuration of the Flux CLI.

## What You'll Learn

This guide covers:
- CLI installation and setup
- Basic usage patterns
- Configuration options
- Common commands overview
- Environment-specific configurations

## Installation & Setup

### Installing the CLI

The Flux CLI is included when you install Flux:

```bash
# Install Flux (includes CLI)
pip install flux-core

# Verify installation
flux --version
```

### Basic CLI Structure

The Flux CLI follows a hierarchical command structure:

```bash
flux [GLOBAL_OPTIONS] COMMAND [COMMAND_OPTIONS] [ARGUMENTS]
```

Example commands:
```bash
flux --help                          # Show general help
flux workflow list                   # List workflows
flux start server                    # Start Flux server
flux secrets set api_key "secret"    # Set a secret
```

### Global Options

Available for all commands:

```bash
flux --help              # Show help information
flux --version           # Show version information
flux --config FILE       # Use specific configuration file
flux --verbose          # Enable verbose logging
flux --quiet            # Suppress non-essential output
```

## Configuration

### Configuration Files

Flux looks for configuration in these locations (in order):

1. `./flux.toml` (current directory)
2. `~/.config/flux/config.toml` (user config)
3. `/etc/flux/config.toml` (system config)
4. Environment variables

### Configuration File Format

Create a `flux.toml` configuration file:

```toml
[settings]
# Server configuration
server_host = "localhost"
server_port = 8000

# Worker configuration
worker_concurrency = 4
worker_timeout = 300

# Database configuration
database_url = "sqlite:///./flux.db"

# Logging configuration
log_level = "INFO"
log_format = "json"

[secrets]
# Secret manager configuration
provider = "file"
file_path = "./secrets.json"

[cache]
# Cache configuration
enabled = true
ttl = 3600
```

### Environment Variables

Override configuration with environment variables:

```bash
# Server configuration
export FLUX_SERVER_HOST=0.0.0.0
export FLUX_SERVER_PORT=8080

# Database configuration
export FLUX_DATABASE_URL=postgresql://user:pass@localhost/flux

# Logging
export FLUX_LOG_LEVEL=DEBUG
export FLUX_LOG_FORMAT=text

# Worker configuration
export FLUX_WORKER_CONCURRENCY=8
export FLUX_WORKER_TIMEOUT=600
```

### Configuration Priority

Configuration sources are applied in this order (later sources override earlier ones):

1. Default values
2. Configuration file
3. Environment variables
4. Command-line arguments

## Command Categories

The Flux CLI organizes commands into logical groups:

### Service Commands
```bash
flux start server    # Start the Flux server
flux start worker     # Start a Flux worker
flux start mcp        # Start MCP server
```

### Workflow Commands
```bash
flux workflow list           # List registered workflows
flux workflow register      # Register a workflow
flux workflow run           # Execute a workflow
flux workflow status        # Check execution status
```

### Secrets Commands
```bash
flux secrets list          # List secret names
flux secrets set           # Set a secret value
flux secrets get           # Retrieve a secret value
flux secrets remove        # Delete a secret
```

## Basic Usage Patterns

### Development Workflow

1. **Start the server** (in one terminal):
```bash
flux start server --host localhost --port 8000
```

2. **Register your workflow** (in another terminal):
```bash
flux workflow register my_workflow.py
```

3. **Run the workflow**:
```bash
flux workflow run my_workflow --input '{"name": "test"}'
```

4. **Check the status**:
```bash
flux workflow status <execution_id>
```

### Production Deployment

1. **Configure the environment**:
```bash
# Set production configuration
export FLUX_SERVER_HOST=0.0.0.0
export FLUX_SERVER_PORT=8000
export FLUX_DATABASE_URL=postgresql://user:pass@prod-db/flux
export FLUX_LOG_LEVEL=INFO
```

2. **Start the server**:
```bash
flux start server --daemon
```

3. **Start workers** (multiple instances):
```bash
flux start worker --concurrency 8
flux start worker --concurrency 8  # Second worker
```

4. **Deploy workflows**:
```bash
flux workflow register /app/workflows/
```

### Local Development

1. **Quick start**:
```bash
# Start server in development mode
flux start server --reload --debug

# In another terminal, run workflows directly
python my_workflow.py  # Local execution
```

2. **Test with the server**:
```bash
# Register and run through server
flux workflow register my_workflow.py
flux workflow run my_workflow --input-file test_data.json
```

## Configuration Examples

### Development Configuration

Create `flux.toml` for development:

```toml
[settings]
server_host = "localhost"
server_port = 8000
log_level = "DEBUG"
log_format = "text"
database_url = "sqlite:///./flux_dev.db"

[worker]
concurrency = 2
timeout = 60

[secrets]
provider = "file"
file_path = "./dev_secrets.json"
```

### Production Configuration

Create production configuration:

```toml
[settings]
server_host = "0.0.0.0"
server_port = 8000
log_level = "INFO"
log_format = "json"
database_url = "${DATABASE_URL}"  # From environment

[worker]
concurrency = 8
timeout = 300
max_retries = 3

[secrets]
provider = "aws_secrets_manager"
region = "us-west-2"

[monitoring]
metrics_enabled = true
health_check_interval = 30
```

### Docker Configuration

Configuration for containerized deployment:

```toml
[settings]
server_host = "0.0.0.0"
server_port = 8000
database_url = "sqlite:///data/flux.db"
log_level = "${LOG_LEVEL:-INFO}"

[worker]
concurrency = "${WORKER_CONCURRENCY:-4}"
timeout = 300

[secrets]
provider = "environment"
```

## Common CLI Patterns

### Batch Operations

```bash
# Register multiple workflows
find ./workflows -name "*.py" -exec flux workflow register {} \;

# Run multiple workflow instances
for i in {1..10}; do
  flux workflow run my_workflow --input "{\"id\": $i}"
done
```

### Monitoring and Health Checks

```bash
# Check server health
curl http://localhost:8000/health

# Monitor workflow status
flux workflow status --follow <execution_id>

# List all executions
flux workflow list --status running
```

### Debugging

```bash
# Start server with debug logging
flux start server --log-level DEBUG

# Run workflow with verbose output
flux workflow run my_workflow --input '{}' --verbose

# Get detailed execution information
flux workflow status <execution_id> --detailed
```

## Shell Completion

Enable shell completion for better CLI experience:

### Bash

Add to your `.bashrc`:

```bash
eval "$(_FLUX_COMPLETE=bash_source flux)"
```

### Zsh

Add to your `.zshrc`:

```bash
eval "$(_FLUX_COMPLETE=zsh_source flux)"
```

### Fish

Add to Fish config:

```fish
eval (env _FLUX_COMPLETE=fish_source flux)
```

## Troubleshooting

### Common Issues

1. **Command not found**:
```bash
# Check if Flux is installed
pip show flux-core

# Check PATH
which flux
```

2. **Configuration not found**:
```bash
# Check configuration search paths
flux --config /path/to/config.toml start server

# Use environment variables instead
export FLUX_SERVER_PORT=8080
flux start server
```

3. **Permission errors**:
```bash
# Check file permissions
ls -la flux.toml

# Use alternative paths
flux --config ~/.flux.toml start server
```

4. **Connection issues**:
```bash
# Test server connectivity
curl http://localhost:8000/health

# Check if port is in use
netstat -tulpn | grep 8000
```

### Debugging CLI Issues

Enable verbose output:

```bash
# Verbose mode
flux --verbose workflow run my_workflow

# Debug configuration
flux --config /dev/null --verbose start server
```

Check logs:

```bash
# Server logs (default location)
tail -f ~/.local/share/flux/logs/server.log

# Custom log location
flux start server --log-file ./custom.log
tail -f ./custom.log
```

## Best Practices

### 1. Use Configuration Files

Create environment-specific configuration files:

```bash
# Development
flux --config flux-dev.toml start server

# Testing
flux --config flux-test.toml start server

# Production
flux --config flux-prod.toml start server
```

### 2. Environment Separation

Use different configurations for different environments:

```bash
# Development
export FLUX_ENV=development
export FLUX_DATABASE_URL=sqlite:///dev.db

# Production
export FLUX_ENV=production
export FLUX_DATABASE_URL=postgresql://...
```

### 3. Security

Protect sensitive configuration:

```bash
# Use environment variables for secrets
export FLUX_DATABASE_PASSWORD=secret
export FLUX_API_KEY=secret

# Secure configuration files
chmod 600 flux.toml
```

### 4. Monitoring

Set up monitoring and alerting:

```bash
# Health check script
#!/bin/bash
if ! curl -f http://localhost:8000/health > /dev/null 2>&1; then
  echo "Flux server is down!"
  exit 1
fi
```

## Next Steps

Now that you understand the CLI basics:

1. Explore [Service Commands](service-commands.md) for server and worker management
2. Learn about [Workflow Commands](workflow-commands.md) for workflow operations
3. Check out [Secrets Management](secrets-commands.md) for handling sensitive data

## Summary

The Flux CLI provides:

- **Comprehensive Management**: All Flux operations available through command line
- **Flexible Configuration**: Multiple configuration sources and formats
- **Environment Support**: Easy switching between development, testing, and production
- **Shell Integration**: Tab completion and standard Unix conventions
- **Debugging Tools**: Verbose output and detailed error reporting

The CLI is designed to be both powerful for production use and convenient for development workflows.
