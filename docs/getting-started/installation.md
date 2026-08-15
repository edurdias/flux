# Installation

## Requirements

Before installing Flux, ensure you have:

- Python 3.12 or later
- pip (Python package installer)
- Optional: Poetry for dependency management

### Core Dependencies
Flux relies on the following packages:
```toml
pydantic = "^2.9.2"
sqlalchemy = "^2.0.35"
fastapi = "^0.115.2"
uvicorn = "^0.31.1"
pycryptodome = "^3.21.0"
```

### Storage Requirements
- Write access to create a `.data` directory for SQLite database storage
- Sufficient disk space for state persistence

## Installation Guide

### Using pip
```bash
pip install flux-core
```

### Using Poetry
```bash
# Initialize a new project
poetry init

# Add Flux as a dependency
poetry add flux-core

# Enter the virtual environment
poetry shell
```

### Container images

Published images carry the same `flux-core` release, differing only in which
optional extras are baked in. Pick by role — one image can serve them all, but
the smaller ones exist so a process only ships what it can use.

| Image | Extras | Use it for |
|---|---|---|
| `flux:<version>` | `postgresql`, `observability`, `ai` | anything; the default when you want one image |
| `flux:<version>-slim` | none | runner children (`docker_image`, `airgapped_image`) |
| `flux:<version>-server` | `postgresql`, `observability` | servers and workers that never call a hosted model |

Each also publishes `<major>.<minor>` and `latest` forms (`flux:latest-slim`,
`flux:0.84-server`, …). Pin the full `<version>` for a runner image: the child
must match its worker's `flux-core`.

```bash
docker pull edurdias/flux:latest          # everything
docker pull edurdias/flux:latest-slim     # sandboxed runner children
```

`-slim` matters most for the docker and airgapped runners. That container is
rootless, has no network, and reaches its engine only over a granted Unix
socket, so the LLM SDKs and the Postgres driver in the default image are
surface it cannot use — roughly 30 packages a sandbox does not need.

Building your own image from these is fine; the runner only requires
`flux-core` at a worker-compatible version.

### Installing for Development
If you're planning to contribute or need development tools:
```bash
git clone https://github.com/edurdias/flux
cd flux
poetry install
```

This will install additional development dependencies for testing and code quality:
- pytest and related plugins for testing
- pylint, pyright, and other linting tools
- pre-commit hooks for code quality

## Quick Setup

1. **Verify Installation**
Check that Flux is properly installed by creating a simple test workflow:

```python
from flux import task, workflow, WorkflowExecutionContext

@task
async def say_hello(name: str):
    return f"Hello, {name}"

@workflow
async def hello_world(ctx: WorkflowExecutionContext[str]):
    return await say_hello(ctx.input)
```

2. **Run the Workflow**
You can execute workflows in three ways:

```python
# Python
ctx = hello_world.run("World")
print(ctx.output)

# Command Line
flux exec hello_world.py hello_world "World"

# HTTP API
flux start examples
curl --location 'localhost:8000/hello_world' \
     --header 'Content-Type: application/json' \
     --data '"World"'
```

3. **Directory Structure**
Flux will automatically create its required directories:
```
your-project/
├── .data/          # SQLite database and state storage
└── your_workflows/
```

4. **Next Steps**
- Create your first workflow using the examples
- Learn about core concepts
- Explore advanced features
