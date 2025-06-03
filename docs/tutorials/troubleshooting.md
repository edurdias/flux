# Troubleshooting Guide

This guide helps you diagnose and resolve common issues when working with Flux workflows. Issues are organized by category with symptoms, causes, and solutions.

> 🚀 **New to Flux?** If you're just getting started, check out [Your First Workflow](your-first-workflow.md) for a guided introduction that covers basic setup and usage patterns.

## Quick Diagnostic Checklist

Before diving into specific issues, run through this checklist:

- [ ] **Flux Server Running**: `flux start server` in one terminal
- [ ] **Worker Running**: `flux start worker` in another terminal
- [ ] **Python Environment**: Correct Python version (3.8+) and packages installed
- [ ] **Network Connectivity**: Server and worker can communicate
- [ ] **File Permissions**: Workflow files are readable
- [ ] **Syntax Errors**: Python files have no syntax errors

## Installation and Setup Issues

### Flux CLI Not Found

**Symptoms**:
```bash
flux: command not found
```

**Causes & Solutions**:

1. **Flux not installed**
   ```bash
   pip install flux-workflow
   ```
   - See: [Installation Guide](../getting-started/installation.md) for detailed setup

2. **Wrong Python environment**
   ```bash
   # Check which Python is active
   which python
   which pip

   # Activate correct environment (if using conda/venv)
   conda activate your-env
   # or
   source your-env/bin/activate
   ```
   - See: [Installation: Environment Setup](../getting-started/installation.md#environment-setup)

3. **Installation in user directory not in PATH**
   ```bash
   # Try with full path
   python -m flux.cli --help

   # Add to PATH (Linux/Mac)
   export PATH="$PATH:$HOME/.local/bin"
   ```

### Import Errors

**Symptoms**:
```
ModuleNotFoundError: No module named 'flux'
```

**Solutions**:

1. **Install Flux in active environment**
   ```bash
   pip install flux-workflow
   ```

2. **Check installation**
   ```bash
   pip list | grep flux
   python -c "import flux; print(flux.__version__)"
   ```

3. **Virtual environment issues**
   ```bash
   # Deactivate and reactivate environment
   deactivate
   source your-env/bin/activate
   pip install flux-workflow
   ```

## Server and Worker Issues

### Server Won't Start

**Symptoms**:
```
Error: Address already in use
```

**Solutions**:

1. **Port already in use**
   ```bash
   # Check what's using the port
   lsof -i :8080

   # Kill the process or use different port
   flux start server --port 8081
   ```

2. **Permission denied**
   ```bash
   # Use non-privileged port
   flux start server --port 8080
   ```

3. **Check server logs**
   ```bash
   flux start server --log-level debug
   ```

### Worker Can't Connect

**Symptoms**:
```
ConnectionError: Failed to connect to server
```

**Solutions**:

1. **Server not running**
   ```bash
   # Start server first
   flux start server
   ```

2. **Wrong server URL**
   ```bash
   # Specify correct server URL
   flux start worker --server-url http://localhost:8080
   ```

3. **Firewall/network issues**
   ```bash
   # Test connectivity
   curl http://localhost:8080/health
   ```

### Server/Worker Crashes

**Symptoms**:
- Process exits unexpectedly
- "Connection reset" errors

**Solutions**:

1. **Check system resources**
   ```bash
   # Monitor memory and CPU
   top
   free -h
   ```

2. **Increase verbosity**
   ```bash
   flux start server --log-level debug
   flux start worker --log-level debug
   ```

3. **Check for Python errors**
   - Look for stack traces in terminal output
   - Check workflow code for infinite loops or memory leaks

## Workflow Registration Issues

### Registration Fails

**Symptoms**:
```
Error registering workflow: SyntaxError
```

**Solutions**:

1. **Check Python syntax**
   ```bash
   python -m py_compile your_workflow.py
   ```

2. **Check imports**
   ```python
   # Make sure all imports are available
   from flux import workflow, task
   ```

3. **Validate decorators**
   ```python
   # Ensure proper decorator usage
   @task
   def my_task():
       pass

   @workflow
   def my_workflow():
       pass
   ```

### Workflow Not Listed

**Symptoms**:
- Registration appears successful but workflow not in list

**Solutions**:

1. **Check workflow definition**
   ```python
   # Must have @workflow decorator
   @workflow
   def my_workflow():
       return "result"
   ```

2. **Re-register workflow**
   ```bash
   flux workflow register your_file.py
   ```

3. **Check server connection**
   ```bash
   flux workflow list --server-url http://localhost:8080
   ```

## Workflow Execution Issues

### Workflow Won't Start

**Symptoms**:
```
Error: Workflow not found
```

**Solutions**:

1. **Verify workflow is registered**
   ```bash
   flux workflow list
   ```

2. **Check workflow name spelling**
   ```bash
   # Use exact name from list
   flux workflow run exact_workflow_name
   ```

3. **Re-register if needed**
   ```bash
   flux workflow register your_file.py
   ```

### Workflow Hangs

**Symptoms**:
- Workflow status shows "running" indefinitely
- No progress or output

**Solutions**:

1. **Check worker status**
   - Ensure worker is running and connected
   - Check worker logs for errors

2. **Check for infinite loops**
   ```python
   # Add timeouts to prevent hanging
   @task
   def potentially_hanging_task():
       import signal
       signal.alarm(60)  # 60 second timeout
       # Your task code here
   ```

3. **Debug with simple workflow**
   ```python
   @workflow
   def debug_workflow():
       print("Debug: workflow started")
       return "success"
   ```

### Task Execution Errors

**Symptoms**:
- Workflow fails with task errors
- Exception traces in logs

**Solutions**:

1. **Add error handling**
   ```python
   @task
   def robust_task(input_data):
       try:
           # Task logic here
           return process_data(input_data)
       except Exception as e:
           print(f"Task error: {e}")
           return {"error": str(e), "input": input_data}
   ```

2. **Validate input data**
   ```python
   @task
   def validated_task(data: dict) -> dict:
       # Validate inputs
       required_fields = ["id", "name", "email"]
       for field in required_fields:
           if field not in data:
               raise ValueError(f"Missing required field: {field}")

       # Process data
       return process_data(data)
   ```

3. **Test tasks individually**
   ```python
   # Test task with known good data
   @workflow
   def test_single_task():
       return problematic_task({"test": "data"})
   ```

## Data Flow Issues

### Data Not Passing Between Tasks

**Symptoms**:
- Tasks receive None or unexpected data
- Type errors between tasks

**Solutions**:

1. **Check return values**
   ```python
   @task
   def producer_task():
       result = {"data": "value"}
       print(f"Returning: {result}")  # Debug output
       return result

   @task
   def consumer_task(input_data):
       print(f"Received: {input_data}")  # Debug output
       return process(input_data)
   ```

2. **Validate data types**
   ```python
   from typing import Dict, List

   @task
   def type_safe_task(data: Dict[str, str]) -> List[str]:
       # Type hints help catch issues early
       return list(data.values())
   ```

3. **Use intermediate validation**
   ```python
   @workflow
   def validated_workflow():
       data = producer_task()

       # Validate data before passing to next task
       if not isinstance(data, dict):
           raise ValueError(f"Expected dict, got {type(data)}")

       return consumer_task(data)
   ```

### Serialization Errors

**Symptoms**:
```
TypeError: Object of type X is not JSON serializable
```

**Solutions**:

1. **Use JSON-serializable types**
   ```python
   # ✅ Good - JSON serializable
   @task
   def good_task() -> dict:
       return {
           "data": [1, 2, 3],
           "timestamp": "2024-01-01T00:00:00"
       }

   # ❌ Bad - Not JSON serializable
   @task
   def bad_task() -> datetime:
       return datetime.now()  # datetime objects aren't JSON serializable
   ```

2. **Convert complex objects**
   ```python
   from datetime import datetime

   @task
   def serializable_task() -> dict:
       return {
           "timestamp": datetime.now().isoformat(),
           "data": list(some_generator()),  # Convert generators to lists
           "status": "complete"
       }
   ```

## Performance Issues

### Slow Workflow Execution

**Symptoms**:
- Tasks take much longer than expected
- System becomes unresponsive

**Solutions**:

1. **Profile task performance**
   ```python
   import time

   @task
   def profiled_task(data):
       start_time = time.time()

       # Your task logic
       result = process_data(data)

       execution_time = time.time() - start_time
       print(f"Task completed in {execution_time:.2f} seconds")

       return result
   ```

2. **Optimize data processing**
   ```python
   @task
   def optimized_task(large_dataset):
       # Process in chunks instead of all at once
       chunk_size = 1000
       results = []

       for i in range(0, len(large_dataset), chunk_size):
           chunk = large_dataset[i:i + chunk_size]
           chunk_result = process_chunk(chunk)
           results.extend(chunk_result)

       return results
   ```

3. **Use parallel processing**
   ```python
   from flux import parallel

   @workflow
   def parallel_workflow(items):
       # Process items in parallel
       return parallel([process_item(item) for item in items])
   ```

### Memory Issues

**Symptoms**:
- Out of memory errors
- System becomes slow due to swapping

**Solutions**:

1. **Process data in chunks**
   ```python
   @task
   def memory_efficient_task(large_file_path):
       results = []

       # Read file in chunks instead of loading entirely
       with open(large_file_path, 'r') as f:
           while True:
               chunk = f.read(8192)  # 8KB chunks
               if not chunk:
                   break

               processed_chunk = process_chunk(chunk)
               results.append(processed_chunk)

       return results
   ```

2. **Clean up resources**
   ```python
   @task
   def resource_conscious_task():
       try:
           # Allocate resources
           large_data = load_large_dataset()
           result = process_data(large_data)

           # Explicitly clean up
           del large_data

           return result
       finally:
           # Cleanup in finally block
           cleanup_resources()
   ```

## Development Issues

### Debug Mode

Enable debug mode for more detailed logging:

```bash
# Server with debug logging
flux start server --log-level debug

# Worker with debug logging
flux start worker --log-level debug

# Run workflow with verbose output
flux workflow run my_workflow --verbose
```

### Development Workflow

1. **Test locally first**
   ```python
   # Test task functions directly
   def test_my_task():
       result = my_task(test_data)
       assert result == expected_result
   ```

2. **Use simple test workflows**
   ```python
   @workflow
   def test_workflow():
       print("Testing workflow execution")
       return {"status": "test_complete"}
   ```

3. **Incremental development**
   ```python
   # Start with simple workflow
   @workflow
   def simple_version():
       return basic_task()

   # Add complexity gradually
   @workflow
   def complex_version():
       data = fetch_data()
       processed = process_data(data)
       return generate_report(processed)
   ```

## Getting Additional Help

### Collect Debug Information

When reporting issues, include:

1. **System information**
   ```bash
   python --version
   pip list | grep flux
   uname -a  # Linux/Mac
   ```

2. **Flux configuration**
   ```bash
   flux --version
   flux workflow list
   ```

3. **Error logs**
   - Copy complete error messages
   - Include stack traces
   - Note when the error occurs

### Community Resources

- **[FAQ](faq.md)**: Check frequently asked questions first
- **[Best Practices](best-practices.md)**: Follow proven patterns to avoid issues
- **[Core Concepts](../core-concepts/workflow-management.md)**: Deep understanding prevents problems
- **GitHub Issues**: [Report bugs and issues](https://github.com/edurdias/flux/issues)
- **Discussions**: [Ask questions and share solutions](https://github.com/edurdias/flux/discussions)
- **Examples**: [Browse working examples](https://github.com/edurdias/flux/tree/main/examples)

### Documentation References

#### By Issue Type
- **Service Issues**: [CLI Service Commands](../cli/start.md)
- **Workflow Problems**: [CLI Workflow Commands](../cli/workflow.md)
- **Secret Issues**: [CLI Secrets Commands](../cli/secrets.md)
- **Task Problems**: [Working with Tasks Tutorial](working-with-tasks.md)

#### By Concept
- **Basic Setup**: [Getting Started](../getting-started/installation.md)
- **Core Concepts**: [Workflows](../getting-started/basic_concepts.md) and [Task System](../core-concepts/tasks.md)
- **Error Handling**: [Core Error Handling](../core-concepts/error-handling.md)
- **Production**: [Best Practices](best-practices.md)

### Best Practices for Troubleshooting

1. **Start simple**: Create minimal reproduction cases
2. **Check logs**: Always review server and worker logs
3. **Test incrementally**: Add complexity gradually
4. **Use version control**: Track changes that introduce issues
5. **Document solutions**: Keep notes on fixes for future reference

## Prevention Tips

### Code Quality

- **Use linting**: `pylint`, `flake8`, or `black` for code quality
- **Type checking**: Use `mypy` for static type analysis
- **Testing**: Write unit tests for task functions
- **Code review**: Have others review workflow code

### Environment Management

- **Virtual environments**: Use conda/venv for isolation
- **Requirements files**: Pin dependency versions
- **Environment variables**: Use for configuration
- **Documentation**: Document setup procedures

### Monitoring

- **Health checks**: Monitor server/worker health
- **Resource monitoring**: Track CPU, memory, disk usage
- **Logging**: Implement structured logging
- **Alerting**: Set up alerts for failures

Remember: Most issues are environmental or configuration-related. Start with the basics and work your way up to more complex diagnostics. 🔧
