# Installation

## Requirements

### Python Environment
- Python 3.12 or later
- pip or Poetry for package management

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
poetry add flux-core
```

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
def say_hello(name: str):
    return f"Hello, {name}"

@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    return (yield say_hello(ctx.input))
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
