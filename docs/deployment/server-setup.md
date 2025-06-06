# Server Setup

The Flux server is the central coordination hub that manages workflows, executions, and worker nodes in a distributed deployment. This guide covers server installation, configuration, startup, and management.

## Quick Start

### Basic Server Startup

Start a Flux server with default settings:

```bash
# Start server on default host (127.0.0.1) and port (8000)
flux start server

# Start server with custom host and port
flux start server --host 0.0.0.0 --port 8080

# Start server accessible from all interfaces
flux start server --host 0.0.0.0
```

### Verify Server is Running

```bash
# Check server health
curl http://localhost:8000/health
# Response: {"status": "healthy"}

# View API documentation
curl http://localhost:8000/docs
# Or open in browser: http://localhost:8000/docs
```

## Configuration

### Command Line Options

The Flux server supports various command-line configuration options:

```bash
flux start server --help
```

**Available Options**:
- `--host, -h`: Host to bind the server to (default: 127.0.0.1)
- `--port, -p`: Port to bind the server to (default: 8000)

### Environment Variables

Configure the server using environment variables:

```bash
# Server binding configuration
export FLUX_SERVER_HOST=0.0.0.0
export FLUX_SERVER_PORT=8000

# Logging configuration
export FLUX_LOG_LEVEL=INFO
export FLUX_LOG_FORMAT=json

# Database configuration
export FLUX_DATABASE_PATH=/data/flux.db

# Security configuration
export FLUX_WORKER_BOOTSTRAP_TOKEN=your_secure_token
```

### Configuration File

Create a `flux.toml` configuration file:

```toml
[server]
host = "0.0.0.0"
port = 8000

[database]
path = "/data/flux.db"

[logging]
level = "INFO"
format = "json"

[workers]
bootstrap_token = "your_secure_bootstrap_token"
default_timeout = 30.0

[security]
enable_cors = true
cors_origins = ["http://localhost:3000", "https://yourdomain.com"]
```

## Server Architecture

### Core Components

The Flux server consists of several key components:

1. **FastAPI Application**: RESTful HTTP API for workflow management
2. **Workflow Catalog**: Storage and management of workflow definitions
3. **Execution Manager**: Orchestrates workflow execution and state management
4. **Worker Registry**: Tracks available worker nodes and their capabilities
5. **Secret Manager**: Secure storage and retrieval of sensitive configuration

### API Endpoints

The server exposes the following API endpoints:

#### Workflow Management
```bash
# Upload workflow file
POST /workflows

# List all workflows
GET /workflows

# Get workflow details
GET /workflows/{workflow_name}

# Get workflow definition
GET /workflows/{workflow_name}/definition
```

#### Execution Control
```bash
# Execute workflow synchronously
POST /workflows/{workflow_name}/run/sync

# Execute workflow asynchronously
POST /workflows/{workflow_name}/run/async

# Execute workflow with streaming
POST /workflows/{workflow_name}/run/stream

# Get execution status
GET /workflows/{workflow_name}/status/{execution_id}

# Cancel execution
POST /workflows/{workflow_name}/cancel/{execution_id}
```

#### Worker Management
```bash
# List connected workers
GET /workers

# Get worker details
GET /workers/{worker_name}

# Worker connection endpoint (for workers)
GET /workers/{worker_name}/connect
```

#### Administration
```bash
# Health check
GET /health

# Server information
GET /info

# List secrets (names only)
GET /admin/secrets

# Set secret
POST /admin/secrets

# Get secret value
GET /admin/secrets/{secret_name}

# Delete secret
DELETE /admin/secrets/{secret_name}
```

## Production Deployment

### Systemd Service Configuration

Create a systemd service for production deployment:

```ini
# /etc/systemd/system/flux-server.service
[Unit]
Description=Flux Workflow Server
After=network.target

[Service]
Type=simple
User=flux
Group=flux
WorkingDirectory=/opt/flux
ExecStart=/opt/flux/venv/bin/flux start server --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3
Environment=FLUX_LOG_LEVEL=INFO
Environment=FLUX_DATABASE_PATH=/var/lib/flux/flux.db
Environment=FLUX_WORKER_BOOTSTRAP_TOKEN=your_production_token

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable flux-server
sudo systemctl start flux-server
sudo systemctl status flux-server
```

### Docker Deployment

#### Using Docker Directly

```bash
# Pull the official image
docker pull edurdias/flux:latest

# Run server with volume mounts
docker run -d \
  --name flux-server \
  -p 8000:8000 \
  -v /host/data:/data \
  -e FLUX_SERVER_HOST=0.0.0.0 \
  -e FLUX_DATABASE_PATH=/data/flux.db \
  -e FLUX_WORKER_BOOTSTRAP_TOKEN=your_token \
  edurdias/flux:latest server
```

#### Docker Compose

```yaml
# docker-compose.yml
version: '3.8'

services:
  flux-server:
    image: edurdias/flux:latest
    command: server
    ports:
      - "8000:8000"
    environment:
      - FLUX_SERVER_HOST=0.0.0.0
      - FLUX_DATABASE_PATH=/data/flux.db
      - FLUX_WORKER_BOOTSTRAP_TOKEN=your_secure_token
      - FLUX_LOG_LEVEL=INFO
    volumes:
      - flux_data:/data
      - ./workflows:/workflows
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  flux_data:
```

Start with Docker Compose:

```bash
docker-compose up -d
docker-compose logs -f flux-server
```

### Kubernetes Deployment

#### Basic Deployment

```yaml
# flux-server-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: flux-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app: flux-server
  template:
    metadata:
      labels:
        app: flux-server
    spec:
      containers:
      - name: flux-server
        image: edurdias/flux:latest
        command: ["flux", "start", "server"]
        args: ["--host", "0.0.0.0", "--port", "8000"]
        ports:
        - containerPort: 8000
        env:
        - name: FLUX_DATABASE_PATH
          value: "/data/flux.db"
        - name: FLUX_WORKER_BOOTSTRAP_TOKEN
          valueFrom:
            secretKeyRef:
              name: flux-secrets
              key: bootstrap-token
        volumeMounts:
        - name: flux-data
          mountPath: /data
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
      volumes:
      - name: flux-data
        persistentVolumeClaim:
          claimName: flux-data-pvc

---
apiVersion: v1
kind: Service
metadata:
  name: flux-server
spec:
  selector:
    app: flux-server
  ports:
  - port: 8000
    targetPort: 8000
  type: LoadBalancer

---
apiVersion: v1
kind: Secret
metadata:
  name: flux-secrets
type: Opaque
data:
  bootstrap-token: eW91cl9zZWN1cmVfdG9rZW4=  # base64 encoded token

---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: flux-data-pvc
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
```

Deploy to Kubernetes:

```bash
kubectl apply -f flux-server-deployment.yaml
kubectl get pods -l app=flux-server
kubectl logs -l app=flux-server
```

## Security Configuration

### Worker Authentication

Configure secure communication between server and workers:

```bash
# Generate a secure bootstrap token
export FLUX_WORKER_BOOTSTRAP_TOKEN=$(openssl rand -hex 32)

# Start server with authentication
flux start server --host 0.0.0.0
```

### CORS Configuration

For web-based frontends, configure CORS:

```toml
# flux.toml
[security]
enable_cors = true
cors_origins = [
    "http://localhost:3000",
    "https://yourdomain.com",
    "https://*.yourdomain.com"
]
cors_methods = ["GET", "POST", "PUT", "DELETE"]
cors_headers = ["*"]
cors_credentials = true
```

### Reverse Proxy Setup

#### Nginx Configuration

```nginx
# /etc/nginx/sites-available/flux
server {
    listen 80;
    server_name flux.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support for streaming
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

#### Apache Configuration

```apache
# /etc/apache2/sites-available/flux.conf
<VirtualHost *:80>
    ServerName flux.yourdomain.com

    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    # WebSocket support
    ProxyPass /ws/ ws://127.0.0.1:8000/ws/
    ProxyPassReverse /ws/ ws://127.0.0.1:8000/ws/
</VirtualHost>
```

## Monitoring and Logging

### Structured Logging

Configure structured logging for production:

```bash
export FLUX_LOG_LEVEL=INFO
export FLUX_LOG_FORMAT=json
```

Example log output:
```json
{
    "timestamp": "2024-01-15T10:30:00Z",
    "level": "INFO",
    "logger": "flux.server",
    "message": "Flux server started successfully",
    "host": "0.0.0.0",
    "port": 8000,
    "version": "1.0.0"
}
```

### Health Monitoring

Set up health check monitoring:

```bash
#!/bin/bash
# health-check.sh
HEALTH_URL="http://localhost:8000/health"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" $HEALTH_URL)

if [ $RESPONSE -eq 200 ]; then
    echo "Server healthy"
    exit 0
else
    echo "Server unhealthy (HTTP $RESPONSE)"
    exit 1
fi
```

### Prometheus Metrics

Flux server exposes metrics for monitoring:

```bash
# Access metrics endpoint
curl http://localhost:8000/metrics

# Example metrics
flux_workflows_total{status="completed"} 42
flux_workflows_total{status="failed"} 3
flux_workers_connected 5
flux_executions_duration_seconds_bucket{le="1.0"} 123
```

## Database Management

### SQLite Configuration (Default)

The server uses SQLite by default for simplicity:

```bash
# Default database location
.data/flux.db

# Custom database path
export FLUX_DATABASE_PATH=/var/lib/flux/flux.db
```

### Database Backup

```bash
#!/bin/bash
# backup-db.sh
DB_PATH="/var/lib/flux/flux.db"
BACKUP_DIR="/backups/flux"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR
sqlite3 $DB_PATH ".backup $BACKUP_DIR/flux_backup_$DATE.db"
echo "Database backed up to $BACKUP_DIR/flux_backup_$DATE.db"
```

### Database Migration

When upgrading Flux versions:

```bash
# Stop the server
sudo systemctl stop flux-server

# Backup current database
./backup-db.sh

# Upgrade Flux
pip install --upgrade flux-core

# Restart server (migrations run automatically)
sudo systemctl start flux-server
```

## Troubleshooting

### Common Issues

1. **Server Won't Start**
   ```bash
   # Check if port is already in use
   netstat -tulpn | grep :8000

   # Check configuration
   flux start server --help
   ```

2. **Workers Can't Connect**
   ```bash
   # Verify server is accessible
   curl http://server-host:8000/health

   # Check worker bootstrap token
   echo $FLUX_WORKER_BOOTSTRAP_TOKEN
   ```

3. **Database Issues**
   ```bash
   # Check database permissions
   ls -la .data/flux.db

   # Verify database path
   echo $FLUX_DATABASE_PATH
   ```

### Log Analysis

```bash
# View server logs
journalctl -u flux-server -f

# Filter for errors
journalctl -u flux-server | grep ERROR

# View recent logs
journalctl -u flux-server --since "1 hour ago"
```

### Performance Tuning

```bash
# Increase worker connection timeout
export FLUX_WORKER_DEFAULT_TIMEOUT=60.0

# Configure database performance
export FLUX_DATABASE_POOL_SIZE=20
export FLUX_DATABASE_TIMEOUT=30.0
```

## Next Steps

- **[Worker Management](worker-management.md)** - Deploy and scale worker nodes
- **[Network Configuration](network-configuration.md)** - Configure host, port, and connectivity
- **[High Availability](high-availability.md)** - Set up redundancy and failover
- **[Monitoring and Observability](monitoring-observability.md)** - Track workflow execution and server health
