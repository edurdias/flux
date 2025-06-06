# Service Commands

The Flux CLI provides commands to start and manage various Flux services including the main server, workers, and Model Context Protocol (MCP) server. These commands are essential for setting up your Flux infrastructure.

## Overview

Service commands in Flux:
- `flux start server` - Start the main Flux orchestration server
- `flux start worker` - Start a workflow execution worker
- `flux start mcp` - Start the Model Context Protocol server

All service commands support common options for configuration, logging, and process management.

## Start Server

Start the main Flux orchestration server that coordinates workflow execution and provides the HTTP API.

### Command
```bash
flux start server [OPTIONS]
```

### Options

#### Network Configuration
- `--host, -h HOST` - Server host address (default: `localhost`)
- `--port, -p PORT` - Server port number (default: `8080`)
- `--workers WORKERS` - Number of server worker processes (default: `4`)

#### Database Configuration
- `--db-url URL` - Database connection URL
- `--db-pool-size SIZE` - Database connection pool size (default: `20`)
- `--db-max-overflow SIZE` - Maximum database connection overflow (default: `30`)

#### Process Management
- `--daemon, -d` - Run server as daemon (background process)
- `--pid-file FILE` - Write process ID to file
- `--log-file FILE` - Log output to file instead of stdout

#### Security Options
- `--auth-enabled` - Enable authentication (default: `true`)
- `--auth-secret SECRET` - Secret key for session management
- `--cors-origins ORIGINS` - Allowed CORS origins (comma-separated)

#### Performance Tuning
- `--max-connections CONNECTIONS` - Maximum concurrent connections (default: `1000`)
- `--timeout SECONDS` - Default request timeout (default: `30`)
- `--keepalive SECONDS` - Connection keepalive timeout (default: `5`)

### Examples

#### Basic Server Start
```bash
# Start server on default host and port
flux start server

# Start on specific host and port
flux start server --host 0.0.0.0 --port 9090
```

#### Production Configuration
```bash
# Start server for production with custom database
flux start server \
  --host 0.0.0.0 \
  --port 8080 \
  --workers 8 \
  --db-url postgresql://user:pass@db.example.com:5432/flux \
  --daemon \
  --pid-file /var/run/flux-server.pid \
  --log-file /var/log/flux-server.log
```

#### Development Configuration
```bash
# Start server for development with debug logging
flux start server \
  --host localhost \
  --port 8080 \
  --workers 2 \
  --log-level DEBUG
```

### Configuration File

Server configuration can also be specified in a configuration file:

```yaml
# flux-server.yaml
server:
  host: "0.0.0.0"
  port: 8080
  workers: 4
  max_connections: 1000

database:
  url: "postgresql://localhost:5432/flux"
  pool_size: 20
  max_overflow: 30

logging:
  level: "INFO"
  file: "/var/log/flux-server.log"

security:
  auth_enabled: true
  cors_origins: ["http://localhost:3000"]
```

Use the configuration file:
```bash
flux start server --config flux-server.yaml
```

## Start Worker

Start a worker process that executes workflow tasks. Workers can be distributed across multiple machines for scalability.

### Command
```bash
flux start worker [OPTIONS]
```

### Options

#### Server Connection
- `--server-host HOST` - Flux server host (default: `localhost`)
- `--server-port PORT` - Flux server port (default: `8080`)
- `--server-url URL` - Full server URL (alternative to host/port)

#### Worker Configuration
- `--worker-id ID` - Unique worker identifier (auto-generated if not provided)
- `--name NAME` - Human-readable worker name
- `--max-concurrent-tasks TASKS` - Maximum concurrent tasks (default: `10`)
- `--heartbeat-interval SECONDS` - Heartbeat interval (default: `30`)

#### Capabilities
- `--capabilities CAPS` - Worker capabilities (comma-separated, e.g., `python,data-processing`)
- `--exclude-capabilities CAPS` - Capabilities to exclude
- `--tags TAGS` - Worker tags for organization (comma-separated)

#### Process Management
- `--daemon, -d` - Run worker as daemon
- `--pid-file FILE` - Write process ID to file
- `--log-file FILE` - Log output to file

#### Resource Limits
- `--max-memory MEMORY` - Maximum memory usage (e.g., `2GB`, `512MB`)
- `--max-cpu-percent PERCENT` - Maximum CPU usage percentage
- `--temp-dir DIR` - Temporary directory for task execution

### Examples

#### Basic Worker Start
```bash
# Start worker connecting to local server
flux start worker

# Start worker with specific configuration
flux start worker --max-concurrent-tasks 5 --worker-id worker-001
```

#### Production Worker
```bash
# Start production worker with resource limits
flux start worker \
  --server-host production.example.com \
  --server-port 8080 \
  --worker-id prod-worker-01 \
  --name "Production Worker 1" \
  --max-concurrent-tasks 20 \
  --max-memory 4GB \
  --max-cpu-percent 80 \
  --capabilities python,data-processing,ml \
  --tags production,high-capacity \
  --daemon \
  --pid-file /var/run/flux-worker.pid \
  --log-file /var/log/flux-worker.log
```

#### Development Worker
```bash
# Start development worker with debug settings
flux start worker \
  --server-host localhost \
  --worker-id dev-worker \
  --max-concurrent-tasks 2 \
  --log-level DEBUG \
  --temp-dir /tmp/flux-dev
```

#### Specialized Workers
```bash
# GPU-enabled worker for ML tasks
flux start worker \
  --worker-id gpu-worker-01 \
  --capabilities python,ml,gpu \
  --max-concurrent-tasks 4 \
  --tags gpu,ml-processing

# Data processing worker
flux start worker \
  --worker-id data-worker-01 \
  --capabilities python,data-processing,etl \
  --max-concurrent-tasks 15 \
  --max-memory 8GB \
  --tags data-processing,high-memory
```

## Start MCP Server

Start the Model Context Protocol (MCP) server for integration with language models and AI agents.

### Command
```bash
flux start mcp [OPTIONS]
```

### Options

#### Server Configuration
- `--host HOST` - MCP server host (default: `localhost`)
- `--port PORT` - MCP server port (default: `8081`)
- `--transport TRANSPORT` - Transport protocol: `stdio`, `http`, `ws` (default: `stdio`)

#### Flux Integration
- `--flux-server-host HOST` - Flux server host (default: `localhost`)
- `--flux-server-port PORT` - Flux server port (default: `8080`)
- `--flux-auth-token TOKEN` - Authentication token for Flux server

#### MCP Configuration
- `--server-name NAME` - MCP server name (default: `flux-mcp`)
- `--server-version VERSION` - MCP server version
- `--capabilities CAPS` - MCP capabilities (comma-separated)

#### Process Management
- `--daemon, -d` - Run as daemon
- `--pid-file FILE` - Process ID file
- `--log-file FILE` - Log file

### Examples

#### Basic MCP Server
```bash
# Start MCP server with stdio transport (for direct integration)
flux start mcp

# Start MCP server with HTTP transport
flux start mcp --transport http --port 8081
```

#### Production MCP Server
```bash
# Start production MCP server
flux start mcp \
  --transport http \
  --host 0.0.0.0 \
  --port 8081 \
  --flux-server-host production.example.com \
  --flux-auth-token "${FLUX_AUTH_TOKEN}" \
  --server-name "Production Flux MCP" \
  --daemon \
  --pid-file /var/run/flux-mcp.pid \
  --log-file /var/log/flux-mcp.log
```

#### WebSocket MCP Server
```bash
# Start MCP server with WebSocket transport
flux start mcp \
  --transport ws \
  --host localhost \
  --port 8082 \
  --capabilities workflow-execution,status-monitoring
```

## Common Options

All service commands support these common options:

### Logging Options
- `--log-level LEVEL` - Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`)
- `--log-format FORMAT` - Log format: `text`, `json` (default: `text`)
- `--log-file FILE` - Log to file instead of stdout
- `--quiet, -q` - Suppress non-error output
- `--verbose, -v` - Enable verbose output

### Configuration Options
- `--config, -c FILE` - Configuration file path
- `--env-file FILE` - Environment variables file
- `--dry-run` - Show configuration without starting service

### Health Check Options
- `--health-check-port PORT` - Health check endpoint port
- `--health-check-path PATH` - Health check endpoint path (default: `/health`)

## Process Management

### Running as Daemon

All services can run as background daemons:

```bash
# Start server as daemon
flux start server --daemon --pid-file /var/run/flux-server.pid

# Check if service is running
if [ -f /var/run/flux-server.pid ]; then
    PID=$(cat /var/run/flux-server.pid)
    if ps -p $PID > /dev/null; then
        echo "Flux server is running (PID: $PID)"
    else
        echo "Flux server is not running"
    fi
fi

# Stop daemon
kill $(cat /var/run/flux-server.pid)
```

### Service Scripts

Create systemd service files for production deployment:

```ini
# /etc/systemd/system/flux-server.service
[Unit]
Description=Flux Workflow Server
After=network.target

[Service]
Type=forking
User=flux
Group=flux
ExecStart=/usr/local/bin/flux start server --daemon --pid-file /var/run/flux-server.pid
PIDFile=/var/run/flux-server.pid
ExecReload=/bin/kill -HUP $MAINPID
KillMode=process
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl enable flux-server
sudo systemctl start flux-server
sudo systemctl status flux-server
```

## Monitoring Services

### Health Checks

All services provide health check endpoints:

```bash
# Check server health
curl http://localhost:8080/health

# Check worker health (if health endpoint enabled)
curl http://localhost:8081/health

# Check MCP server health
curl http://localhost:8082/health
```

### Log Monitoring

Monitor service logs:

```bash
# Follow server logs
tail -f /var/log/flux-server.log

# Follow worker logs with filtering
tail -f /var/log/flux-worker.log | grep ERROR

# Check logs with journalctl (for systemd services)
journalctl -u flux-server -f
```

### Resource Usage

Monitor resource usage:

```bash
# Check process resource usage
ps aux | grep flux

# Monitor with htop
htop -p $(pgrep -f "flux start")

# Check memory usage
free -h
df -h  # Disk usage
```

## Troubleshooting

### Common Issues

#### Port Already in Use
```bash
# Check what's using the port
sudo netstat -tlnp | grep :8080
sudo lsof -i :8080

# Kill process using the port
sudo kill $(sudo lsof -t -i:8080)
```

#### Permission Denied
```bash
# Check file permissions
ls -la /var/run/flux-server.pid
ls -la /var/log/

# Fix permissions
sudo chown flux:flux /var/run/flux-server.pid
sudo chmod 644 /var/log/flux-server.log
```

#### Database Connection Issues
```bash
# Test database connectivity
psql postgresql://user:pass@localhost:5432/flux -c "SELECT 1;"

# Check database logs
sudo tail -f /var/log/postgresql/postgresql.log
```

#### Worker Connection Issues
```bash
# Test server connectivity from worker
curl http://server-host:8080/api/v1/health

# Check network connectivity
ping server-host
telnet server-host 8080
```

## Next Steps

- Learn about [Workflow Commands](workflow-commands.md) for managing workflows
- Explore [Secrets Management](secrets-commands.md) for handling sensitive data
- Check out [Server Configuration](../../reference/configuration/server-configuration.md) for detailed configuration options
- Review [Deployment Strategies](../../deployment/deployment-strategies.md) for production setup
