# CLI Reference

The Flux CLI provides a comprehensive command-line interface for managing workflows, secrets, and running Flux services. This section covers all available commands and their usage.

## Quick Reference

| Command Group | Description |
|---------------|-------------|
| [`flux workflow`](workflow.md) | Manage and execute workflows |
| [`flux start`](start.md) | Start Flux services (server, worker, MCP) |
| [`flux secrets`](secrets.md) | Manage secure secrets for tasks |

## Installation

The Flux CLI is automatically available when you install Flux:

```bash
pip install flux-workflow
```

## Basic Usage

All Flux CLI commands follow the pattern:

```bash
flux <command_group> <command> [options] [arguments]
```

### Global Options

Most commands that interact with the Flux server support these common options:

- `--server-url`, `-cp-url`: Override the default server URL
- `--help`: Show command-specific help information

### Getting Help

Get help for any command or command group:

```bash
# General help
flux --help

# Command group help
flux workflow --help
flux start --help
flux secrets --help

# Specific command help
flux workflow run --help
flux start server --help
```

## Configuration

The CLI uses the Flux configuration system. Server connection details are automatically read from your configuration file, but can be overridden using command-line options.

Default server connection:
- Host: `localhost`
- Port: `8000`
- URL: `http://localhost:8000`

## Output Formats

Many commands support different output formats:

- **Simple**: Human-readable text format (default)
- **JSON**: Machine-readable JSON format (use `-f json`)

## Error Handling

The CLI provides clear error messages and uses standard exit codes:

- `0`: Success
- `1`: General error
- Network errors include connection timeouts and HTTP status information

## Next Steps

- [Workflow Commands](workflow.md) - Complete reference for workflow management
- [Service Commands](start.md) - Starting servers, workers, and MCP services
- [Secrets Commands](secrets.md) - Managing secure credentials
- [Configuration Guide](../getting-started/installation.md) - Setting up Flux configuration
