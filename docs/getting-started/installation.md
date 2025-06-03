# Installation

## Requirements

Before installing Flux, ensure you have:

- **Python 3.12 or later** - Flux requires modern Python features
- **pip** (Python package installer) or **Poetry** for dependency management
- **Write access** to create a `.data` directory for SQLite database storage
- **Sufficient disk space** for state persistence and workflow execution

### Core Dependencies
Flux relies on the following core packages (automatically installed):

```toml
pydantic = "^2.9.2"          # Data validation and settings
sqlalchemy = "^2.0.35"       # Database ORM for state persistence
fastapi = "^0.115.2"         # HTTP API framework
uvicorn = "^0.31.1"          # ASGI server implementation
pycryptodome = "^3.21.0"     # Cryptographic functions for secrets
httpx = "^0.28.1"            # HTTP client with SSE support
dill = "^0.3.9"              # Advanced Python serialization
psutil = "^7.0.0"            # System and process utilities
```

### Optional Dependencies
For enhanced functionality:
- **Docker** - For containerized deployment
- **Git** - For version control and development

## Installation Guide

### Using pip (Recommended)
The simplest way to install Flux:

```bash
pip install flux-core
```

### Using Poetry
For projects using Poetry dependency management:

```bash
# Initialize a new project
poetry init

# Add Flux as a dependency
poetry add flux-core

# Enter the virtual environment
poetry shell
```

### Installing from Source
For development or the latest features:

```bash
git clone https://github.com/edurdias/flux
cd flux
poetry install
```

This installs additional development dependencies:
- **pytest** and related plugins for testing
- **pylint**, **pyright**, and other linting tools
- **pre-commit** hooks for code quality
- **mkdocs** for documentation

### Docker Installation

#### Using the Official Image
```bash
# Pull the official Flux image
docker pull edurdias/flux:latest

# Run Flux server
docker run -p 8000:8000 edurdias/flux:latest server

# Run Flux worker
docker run edurdias/flux:latest worker
```

#### Building from Source
```bash
git clone https://github.com/edurdias/flux
cd flux
docker build -t flux .
```

## Quick Setup

### 1. Verify Installation
Check that Flux is properly installed:

```bash
flux --help
```

You should see the Flux CLI help menu with available commands.

### 2. Create Your First Workflow
Create a simple test workflow to verify everything works:

```python
# save as hello_world.py
from flux import task, workflow, ExecutionContext

@task
async def say_hello(name: str) -> str:
    return f"Hello, {name}!"

@workflow
async def hello_world(ctx: ExecutionContext[str]):
    return await say_hello(ctx.input)

if __name__ == "__main__":
    # Run locally
    result = hello_world.run("World")
    print(result.output)  # "Hello, World!"
```

### 3. Run the Workflow
You can execute workflows in multiple ways:

#### Direct Python Execution
```python
python hello_world.py
```

#### Using Flux CLI (Local)
```bash
# Register and run workflow
flux workflow register hello_world.py
flux workflow run hello_world '"World"'
```

#### Using HTTP API (Distributed)
Start the server and workers:
```bash
# Terminal 1: Start the server
flux start server

# Terminal 2: Start a worker
flux start worker

# Terminal 3: Register and run workflow
flux workflow register hello_world.py
flux workflow run hello_world '"World"' --mode async
```

Or use HTTP directly:
```bash
# Upload workflow
curl -X POST 'http://localhost:8000/workflows' \
     -F 'file=@hello_world.py'

# Execute workflow
curl -X POST 'http://localhost:8000/workflows/hello_world/run/sync' \
     -H 'Content-Type: application/json' \
     -d '"World"'
```

### 4. Directory Structure
Flux automatically creates required directories:

```
your-project/
├── .data/                  # SQLite database and state storage
│   ├── flux.db            # Workflow execution state
│   └── secrets.db          # Encrypted secrets storage
├── your_workflows.py       # Your workflow definitions
└── logs/                   # Optional: Application logs
```

## Configuration

### Environment Variables
Configure Flux behavior using environment variables:

```bash
# Server configuration
export FLUX_SERVER_HOST=0.0.0.0
export FLUX_SERVER_PORT=8000

# Worker configuration
export FLUX_WORKER_SERVER_URL=http://localhost:8000
export FLUX_WORKER_BOOTSTRAP_TOKEN=your_token

# Logging
export FLUX_LOG_LEVEL=INFO
export FLUX_LOG_FORMAT=json
```

### Configuration File
Create a `flux.toml` file in your project root:

```toml
[server]
host = "0.0.0.0"
port = 8000

[workers]
server_url = "http://localhost:8000"
bootstrap_token = "your_token"

[logging]
level = "INFO"
format = "json"
```

## Verification

### Check Installation
```bash
# Verify Flux CLI is available
flux --version

# List available commands
flux --help

# Test server startup (dry run)
flux start server --help
```

### Run Built-in Examples
Flux includes example workflows to test your installation:

```bash
# Clone the repository to access examples
git clone https://github.com/edurdias/flux
cd flux/examples

# Run a simple example
python hello_world.py

# Run with parallel tasks
python parallel_tasks.py

# Test complex pipeline
python complex_pipeline.py
```

### Test HTTP API
Start the server and test the API:

```bash
# Start server
flux start server

# In another terminal, test the API
curl http://localhost:8000/health

# Should return: {"status": "healthy"}
```

## Troubleshooting

### Common Issues

#### Permission Errors
If you encounter permission errors:
```bash
# Use user installation
pip install --user flux-core

# Or use virtual environment
python -m venv flux-env
source flux-env/bin/activate  # On Windows: flux-env\Scripts\activate
pip install flux-core
```

#### Python Version Compatibility
Ensure you're using Python 3.12+:
```bash
python --version
# Should show Python 3.12.x or higher
```

#### Missing Dependencies
If you encounter import errors:
```bash
# Reinstall with all dependencies
pip install --force-reinstall flux-core

# Or install with development dependencies
pip install flux-core[dev]
```

#### Database Issues
If the SQLite database becomes corrupted:
```bash
# Remove and recreate the database
rm -rf .data/
# Flux will recreate the database on next run
```

### Getting Help

- **Documentation**: [https://edurdias.github.io/flux](https://edurdias.github.io/flux)
- **GitHub Issues**: [https://github.com/edurdias/flux/issues](https://github.com/edurdias/flux/issues)
- **Examples**: Check the `examples/` directory in the repository

## Next Steps

Now that Flux is installed:

1. **[Learn Core Concepts](basic-concepts.md)** - Understand tasks, workflows, and execution context
2. **[Create Your First Workflow](first-workflow.md)** - Build a complete workflow from scratch
3. **[Explore Built-in Tasks](built-in-tasks.md)** - Discover available utilities and functions
4. **[Try the Tutorials](tutorials/simple-workflow.md)** - Follow step-by-step guides

Ready to start building robust, distributed workflows with Flux!
