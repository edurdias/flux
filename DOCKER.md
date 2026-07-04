# Docker Usage

This document explains how to use Flux with Docker, using the official `flux-core` package from PyPI.

One image covers every role. An explicit command always wins over `FLUX_MODE`,
so the same image serves as:

- **server** — `docker run <image>` (the default mode)
- **worker** — `docker run -e FLUX_MODE=worker <image>`
- **MCP server** — `docker run -e FLUX_MODE=mcp <image>`
- **docker-runner execution child** — what a worker's `docker` runner
  launches per execution: `docker run -i <image> python -m flux.runners.child`
- **ad-hoc CLI / agent admin** — `docker run <image> flux workflow list`,
  `docker run <image> flux agent create …`, or any other command

## Building the Image

```bash
# Build with latest flux-core version
docker build -t flux .

# Build with specific flux-core version
docker build --build-arg FLUX_VERSION=0.4.0 -t flux:0.4.0 .

# Build with flux extras baked in (the published image ships
# postgresql,observability,ai so it works for every role out of the box)
docker build --build-arg FLUX_EXTRAS=postgresql,observability,ai -t flux .

# Build with extra packages (your workflows' dependencies)
docker build --build-arg EXTRA_PACKAGES="pandas numpy" -t flux-data .
```

The build precompiles all Python bytecode into the image. This matters for
the docker runner: containers are ephemeral, so without baked `.pyc` files
every execution recompiles imports from source — measured to double the
runner's per-execution latency. If you build your own workflow image from a
different base, keep a
`RUN python -m compileall -q <site-packages>` step.

## Using the image with the docker runner

Workers with the `docker` runner enabled launch one container per execution
from `docker_image`. The official image is directly usable — pin its tag to
the flux-core version the worker runs so the wire protocol matches:

```toml
[flux.workers]
runners = ["inprocess", "subprocess", "docker"]
docker_image = "<dockerhub-user>/flux:0.52.0"   # match the worker's flux-core version
```

Add your workflows' dependencies by building on top of it:

```dockerfile
FROM <dockerhub-user>/flux:0.52.0
RUN pip install --no-cache-dir pandas numpy && \
    python -m compileall -q "$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
```

The execution child holds no worker or fleet credentials and needs no
configuration — checkpoints, secrets, configs, and approval-gate
operations flow through the parent worker over the container's stdio. The
only credential inside the container is the short-lived, single-execution
token used for `call()` hops back into the server.

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

- `PYTHON_IMAGE_VERSION`: Python image tag (default: 3.12-slim; supports digest pinning, see below)
- `FLUX_VERSION`: flux-core package version (default: latest)
- `EXTRA_PACKAGES`: Additional pip packages to install

## Production hardening

The image and compose file follow these conventions; keep them in mind when deploying:

- **Non-root user**: the container runs as the `flux` user (UID 1000). `/app` is owned by this user; with the shipped configuration all runtime state lives under the Flux home `/app/.flux` (including the cache at `/app/.flux/.cache` and task outputs at `/app/.flux/.storage`). If you bind-mount these paths, make sure the host directories are writable by UID 1000.
- **Healthcheck**: the image ships a mode-aware `HEALTHCHECK`. In `server` mode it probes `GET /health` (returns 503 when the database is unreachable); in `worker`/`mcp` mode the flux process is PID 1, so container liveness already reflects process liveness and the check reports healthy.
- **tini is built in**: the image runs `tini` as PID 1, so zombie reaping and signal forwarding (SIGTERM-based worker drain, docker-runner cancellation) work without `docker run --init`. Keeping `init: true` in compose is harmless.
- **Base layer is patched at build time** (`apt-get upgrade`) and Python bytecode is precompiled; `PYTHONDONTWRITEBYTECODE=1` keeps runtime filesystems clean, which makes `--read-only` deployments practical (mount `/app/.flux` writable, e.g. a volume or tmpfs).
- **Runtime flags worth adding in production**: `--cap-drop=ALL --security-opt=no-new-privileges` for all roles; add `--read-only --tmpfs /tmp -v flux-data:/app/.flux` for the server. Docker-runner execution containers are launched by the worker — inject the same flags fleet-wide via `[flux.workers] docker_extra_args = ["--cap-drop=ALL", "--security-opt=no-new-privileges"]`.
- **Upgrading from a root-based image**: named volumes created by older (root-running) images may contain root-owned files (e.g. `flux-data` mounted at `/app/.flux`). After upgrading, fix ownership with `docker run --rm -u 0 --entrypoint chown -v flux-data:/data <image> -R 1000:1000 /data` (the entrypoint override is required because the image's default entrypoint would otherwise ignore the command), or recreate the volume (`docker volume rm flux-data`) if its contents are disposable.
- **Pin the base image by digest**: builds default to `python:3.12-slim`. For reproducible production builds, pass a digest, e.g. `docker build --build-arg PYTHON_IMAGE_VERSION=3.12-slim@sha256:<digest> -t flux .` (resolve the digest with `docker buildx imagetools inspect python:3.12-slim`).
- **The bundled `docker-compose.yml` is DEVELOPMENT-ONLY**: it ships well-known credentials (Keycloak `admin`/`admin`, PostgreSQL `flux`/`flux`) and a default bootstrap token/encryption key. Override `FLUX_BOOTSTRAP_TOKEN` and `FLUX_ENCRYPTION_KEY`, or better, provision real secrets out-of-band for anything beyond local development.

## Authentication Dev Environment

The docker-compose includes a Keycloak instance for local OIDC development:

```bash
# Start with Keycloak
docker-compose up -d keycloak
```

Keycloak admin console: `http://localhost:8080`
Admin credentials: `admin` / `admin`

Pre-seeded users in the `flux` realm:

| User | Password | Role |
|------|----------|------|
| `admin@local` | `admin` | admin |
| `operator@local` | `operator` | operator |
| `viewer@local` | `viewer` | viewer |

Get a test token:

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/realms/flux/protocol/openid-connect/token \
  -d "grant_type=password&client_id=flux-api&username=admin@local&password=admin" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

Use with Flux:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/workflows
```

## Troubleshooting

### GPUtil and Python 3.12 Compatibility

If you encounter an error like `ModuleNotFoundError: No module named 'distutils'` when running workers, this is due to GPUtil's dependency on the `distutils` module, which was removed in Python 3.12. The Dockerfile automatically installs `setuptools` to provide this compatibility.

### Common Issues

- **Worker connection errors**: Ensure the `FLUX_SERVER_URL` points to a running Flux server
- **Network connectivity**: When using Docker Compose, make sure services are on the same network
- **Port conflicts**: Check that the server port (default 8000) is not already in use
