# Contributing to Flux

Thank you for your interest in contributing to Flux! This guide will help you get started with contributing to the project, whether you're fixing bugs, adding features, improving documentation, or helping with testing.

## Getting Started

### Prerequisites

- Python 3.12 or higher
- Poetry for dependency management
- Git for version control

### Development Environment Setup

1. **Fork and Clone the Repository**
   ```bash
   # Fork the repository on GitHub, then clone your fork
   git clone https://github.com/YOUR_USERNAME/flux.git
   cd flux
   ```

2. **Install Development Dependencies**
   ```bash
   # Install Poetry if you haven't already
   curl -sSL https://install.python-poetry.org | python3 -

   # Install project dependencies and development tools
   poetry install

   # Activate the virtual environment
   poetry shell
   ```

3. **Verify Installation**
   ```bash
   # Run tests to ensure everything is working
   poetry run pytest

   # Check code quality tools
   poetry run ruff check
   poetry run pyright
   ```

## Development Workflow

### 1. Create a Feature Branch

```bash
# Create and switch to a new branch
git checkout -b feature/amazing-feature

# Or for bug fixes
git checkout -b fix/issue-description
```

### 2. Make Your Changes

Follow these guidelines when making changes:

- **Code Style**: Follow PEP 8 and use the project's configured tools
- **Type Hints**: Add type annotations for all new code
- **Documentation**: Update docstrings and documentation as needed
- **Tests**: Add tests for new functionality and ensure existing tests pass

### 3. Test Your Changes

```bash
# Run the full test suite
poetry run pytest

# Run tests with coverage
poetry run pytest --cov=flux --cov-report=html

# Run specific test files
poetry run pytest tests/flux/test_specific_module.py

# Run tests for examples
poetry run pytest tests/examples/
```

### 4. Code Quality Checks

```bash
# Format code with Ruff
poetry run ruff format

# Check for linting issues
poetry run ruff check

# Run type checking
poetry run pyright

# Run comprehensive code analysis
poetry run pylint flux/

# Check security issues
poetry run bandit -r flux/

# Check code complexity
poetry run radon cc flux/
```

### 5. Commit Your Changes

```bash
# Stage your changes
git add .

# Commit with a descriptive message
git commit -m "Add amazing feature that does X"

# Push to your fork
git push origin feature/amazing-feature
```

### 6. Create a Pull Request

1. Go to the GitHub repository
2. Click "New Pull Request"
3. Select your branch
4. Fill out the pull request template
5. Submit the pull request

## Code Style and Standards

### Python Code Style

- **Line Length**: Maximum 100 characters
- **Formatting**: Use Ruff for automatic formatting
- **Imports**: Organize imports using Ruff's import sorting
- **Type Hints**: Required for all public APIs and encouraged elsewhere

```python
# Good example
from typing import Any, Dict, List, Optional

from flux import ExecutionContext, task, workflow


@task.with_options(retry_max_attempts=3, timeout=30)
async def example_task(data: str, config: Dict[str, Any]) -> List[str]:
    """Process data according to configuration.

    Args:
        data: Input data to process
        config: Processing configuration options

    Returns:
        List of processed results

    Raises:
        ValueError: If data format is invalid
    """
    if not data:
        raise ValueError("Data cannot be empty")

    # Implementation here
    return processed_results
```

### Documentation Standards

- **Docstrings**: Use Google-style docstrings for all public functions and classes
- **Type Documentation**: Document complex types and return values
- **Examples**: Include usage examples in docstrings when helpful
- **API Documentation**: Update API docs when adding new public interfaces

```python
def complex_function(
    input_data: List[Dict[str, Any]],
    options: Optional[Dict[str, str]] = None
) -> Dict[str, List[Any]]:
    """Process complex input data with optional configuration.

    This function processes a list of dictionaries containing various data
    types and returns organized results based on the provided options.

    Args:
        input_data: List of dictionaries with string keys and any values.
            Each dictionary should contain at least a 'type' field.
        options: Optional configuration dictionary. Supported keys:
            - 'sort_by': Field to sort results by
            - 'filter': Filter pattern to apply

    Returns:
        Dictionary with processed results organized by type:
        - Keys are data types found in input
        - Values are lists of processed items

    Raises:
        ValueError: If input_data is empty or contains invalid items
        KeyError: If required fields are missing from input items

    Example:
        >>> data = [
        ...     {'type': 'user', 'name': 'Alice'},
        ...     {'type': 'admin', 'name': 'Bob'}
        ... ]
        >>> result = complex_function(data, {'sort_by': 'name'})
        >>> print(result)
        {'user': [{'name': 'Alice', 'type': 'user'}], 'admin': [...]}
    """
    # Implementation
```

## Testing Guidelines

### Test Structure

```bash
tests/
├── flux/                    # Unit tests for core functionality
│   ├── test_workflow.py
│   ├── test_task.py
│   └── test_secret_managers.py
├── examples/                # Integration tests for examples
│   ├── test_hello_world.py
│   └── test_complex_pipeline.py
└── conftest.py             # Shared test fixtures
```

### Writing Tests

```python
import pytest
from unittest.mock import MagicMock, patch

from flux import ExecutionContext, task, workflow


class TestExampleFeature:
    """Test suite for example feature."""

    @pytest.fixture
    def sample_data(self):
        """Provide sample data for tests."""
        return {"key": "value", "items": [1, 2, 3]}

    def test_basic_functionality(self, sample_data):
        """Test basic functionality works as expected."""
        result = process_data(sample_data)
        assert result is not None
        assert "processed" in result

    def test_error_handling(self):
        """Test error handling for invalid input."""
        with pytest.raises(ValueError, match="Invalid input"):
            process_data(None)

    @patch('flux.external_service.api_call')
    def test_with_mocks(self, mock_api_call, sample_data):
        """Test functionality with external dependencies mocked."""
        mock_api_call.return_value = {"status": "success"}

        result = process_with_api(sample_data)

        mock_api_call.assert_called_once()
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_async_functionality(self):
        """Test asynchronous functionality."""
        @workflow
        async def test_workflow(ctx: ExecutionContext):
            return await async_task()

        ctx = test_workflow.run()
        assert ctx.has_succeeded
```

### Test Categories

- **Unit Tests**: Test individual functions and classes in isolation
- **Integration Tests**: Test component interactions and example workflows
- **End-to-End Tests**: Test complete workflows through the API
- **Performance Tests**: Test performance characteristics and benchmarks

## Documentation

### Documentation Structure

```bash
docs/
├── index.md                 # Main documentation index
├── getting-started/         # Getting started guides
├── core-concepts/          # Core concept explanations
├── advanced-features/      # Advanced features
├── examples/               # Example workflows and patterns
├── api/                    # API reference documentation
└── contributing.md         # This file
```

### Building Documentation

```bash
# Install documentation dependencies
poetry install --with docs

# Serve documentation locally
poetry run mkdocs serve

# Build documentation
poetry run mkdocs build

# Deploy to GitHub Pages (maintainers only)
poetry run mkdocs gh-deploy
```

### Documentation Guidelines

- **Clear Structure**: Organize content logically with clear headings
- **Code Examples**: Include working code examples for all features
- **Cross-References**: Link related concepts and API documentation
- **Update mkdocs.yml**: Add new documentation pages to the navigation

## Types of Contributions

### Bug Reports

When reporting bugs, please include:

1. **Description**: Clear description of the issue
2. **Reproduction Steps**: Step-by-step instructions to reproduce
3. **Expected Behavior**: What should happen
4. **Actual Behavior**: What actually happens
5. **Environment**: Python version, OS, Flux version
6. **Code Sample**: Minimal code that demonstrates the issue

```python
# Example bug report code
from flux import workflow, ExecutionContext

@workflow
async def buggy_workflow(ctx: ExecutionContext):
    # This should work but doesn't
    return await problematic_operation()

# Error occurs when running:
ctx = buggy_workflow.run("test_input")
# Expected: successful execution
# Actual: raises ValueError
```

### Feature Requests

For feature requests, please include:

1. **Use Case**: Why is this feature needed?
2. **Proposed Solution**: How should it work?
3. **Alternatives**: What alternatives have you considered?
4. **Implementation Ideas**: Any thoughts on implementation?

### Code Contributions

We welcome contributions for:

- **Bug Fixes**: Fix reported issues
- **New Features**: Add new functionality
- **Performance Improvements**: Optimize existing code
- **Documentation**: Improve or add documentation
- **Tests**: Add or improve test coverage
- **Examples**: Add new example workflows

### Documentation Contributions

- **Tutorials**: Step-by-step guides for specific use cases
- **API Documentation**: Improve docstrings and API references
- **Examples**: Add new workflow examples
- **Corrections**: Fix typos, errors, or unclear explanations

## Code Review Process

### Pull Request Guidelines

1. **Title**: Clear, descriptive title
2. **Description**: Explain what changes you made and why
3. **Tests**: Include tests for new functionality
4. **Documentation**: Update relevant documentation
5. **Breaking Changes**: Clearly mark any breaking changes

### Review Criteria

Pull requests are reviewed for:

- **Functionality**: Does the code work as intended?
- **Tests**: Are there adequate tests with good coverage?
- **Code Quality**: Does it follow project standards?
- **Documentation**: Is it properly documented?
- **Performance**: Does it introduce performance regressions?
- **Security**: Are there any security implications?

### Getting Reviews

- **Request Reviews**: Tag relevant maintainers or experts
- **Respond to Feedback**: Address all review comments
- **Update PR**: Push additional commits to address feedback
- **CI Checks**: Ensure all continuous integration checks pass

## Release Process

### Versioning

Flux follows [Semantic Versioning](https://semver.org/):

- **MAJOR**: Incompatible API changes
- **MINOR**: New functionality (backwards compatible)
- **PATCH**: Bug fixes (backwards compatible)

### Release Checklist

For maintainers preparing releases:

1. **Update Version**: Update version in `pyproject.toml`
2. **Update Changelog**: Document all changes
3. **Run Tests**: Ensure all tests pass
4. **Build Package**: Test package building
5. **Tag Release**: Create git tag
6. **Deploy**: Deploy to PyPI
7. **Update Docs**: Deploy updated documentation

## Community Guidelines

### Code of Conduct

- **Be Respectful**: Treat all contributors with respect
- **Be Inclusive**: Welcome contributors from all backgrounds
- **Be Constructive**: Provide helpful feedback and suggestions
- **Be Patient**: Remember that people have different experience levels

### Getting Help

- **Discussions**: Use GitHub Discussions for questions
- **Issues**: Report bugs and request features via GitHub Issues
- **Documentation**: Check documentation first
- **Examples**: Look at example workflows for common patterns

### Recognition

Contributors are recognized through:

- **GitHub Contributors**: Listed in repository contributors
- **Changelog**: Mentioned in release notes
- **Documentation**: Credited in relevant documentation sections

## Development Tools

### Pre-commit Hooks

Set up pre-commit hooks to automatically check code quality:

```bash
# Install pre-commit
poetry run pre-commit install

# Run on all files
poetry run pre-commit run --all-files
```

### IDE Configuration

#### VS Code
```json
{
  "python.defaultInterpreterPath": ".venv/bin/python",
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": true,
  "python.formatting.provider": "black",
  "python.sortImports.args": ["--profile", "black"],
  "[python]": {
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  }
}
```

#### PyCharm
- Configure Python interpreter to use Poetry virtual environment
- Enable Pylint and other code quality tools
- Set up code formatting to use project standards

### Useful Development Commands

```bash
# Run specific test categories
poetry run pytest -m "not slow"        # Skip slow tests
poetry run pytest tests/flux/           # Only unit tests
poetry run pytest tests/examples/       # Only integration tests

# Generate test coverage report
poetry run pytest --cov=flux --cov-report=html
open htmlcov/index.html                 # View coverage report

# Profile performance
poetry run python -m cProfile -o profile.prof script.py
poetry run python -c "import pstats; pstats.Stats('profile.prof').sort_stats('time').print_stats(20)"

# Check for security issues
poetry run bandit -r flux/

# Analyze code complexity
poetry run radon cc flux/ --min=B       # Show complex functions

# Generate dependency graph
poetry run pydeps flux/ --max-bacon=2
```

## Thank You

Thank you for contributing to Flux! Your contributions help make Flux better for everyone. Whether you're reporting bugs, suggesting features, improving documentation, or submitting code changes, every contribution is valuable and appreciated.

For questions about contributing, feel free to open a GitHub Discussion or reach out to the maintainers.

Happy coding! 🚀
