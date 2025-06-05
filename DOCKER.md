# Docker Usage

This document explains how to use Flux with Docker, using the official `flux-core` package from PyPI.

## Building the Image

```bash
# Build with latest flux-core version
docker build -t flux .

# Build with specific flux-core version
docker build --build-arg FLUX_VERSION=0.4.0 -t flux:0.4.0 .

# Build with extra packages
docker build --build-arg EXTRA_PACKAGES="pandas numpy" -t flux-data .
```

## Running the Server

```bash
# Run server on default port 8000
docker run -p 8000:8000 flux

# Run server with custom configuration
docker run -p 8080:8080 \
  -e FLUX_HOST=0.0.0.0 \
  -e FLUX_PORT=8080 \
  -v ./flux.toml:/app/flux.toml \
  flux
```

## Running Workers

```bash
# Run worker connecting to server
docker run \
  -e FLUX_MODE=worker \
  -e FLUX_SERVER_URL=http://flux-server:8000 \
  --network flux-network \
  flux

# Run worker with custom name
docker run \
  -e FLUX_MODE=worker \
  -e FLUX_WORKER_NAME=my-worker \
  -e FLUX_SERVER_URL=http://localhost:8000 \
  flux
```

## Using Docker Compose

For a complete setup with server and multiple workers:

```bash
# Start the entire stack
docker-compose up -d

# Scale workers
docker-compose up -d --scale flux-worker-1=3

# View logs
docker-compose logs -f flux-server
docker-compose logs -f flux-worker-1

# Stop the stack
docker-compose down
```

## Environment Variables

- `FLUX_MODE`: Set to `server` (default) or `worker`
- `FLUX_HOST`: Server host (default: 0.0.0.0)
- `FLUX_PORT`: Server port (default: 8000)
- `FLUX_WORKER_NAME`: Worker name (auto-generated if not set)
- `FLUX_SERVER_URL`: Server URL for workers to connect to

## Volume Mounts

- `/app/flux.toml`: Configuration file
- `/app/.flux`: Flux data directory (databases, cache, etc.)
- `/app/.flux/.workflows`: Workflow storage directory

## Build Arguments

- `PYTHON_IMAGE_VERSION`: Python version (default: 3.12)
- `FLUX_VERSION`: flux-core package version (default: latest)
- `EXTRA_PACKAGES`: Additional pip packages to install

## Troubleshooting

### GPUtil and Python 3.12 Compatibility

If you encounter an error like `ModuleNotFoundError: No module named 'distutils'` when running workers, this is due to GPUtil's dependency on the `distutils` module, which was removed in Python 3.12. The Dockerfile automatically installs `setuptools` to provide this compatibility.

### Common Issues

- **Worker connection errors**: Ensure the `FLUX_SERVER_URL` points to a running Flux server
- **Network connectivity**: When using Docker Compose, make sure services are on the same network
- **Port conflicts**: Check that the server port (default 8000) is not already in use
