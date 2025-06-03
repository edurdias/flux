# Flux Documentation

> Flux documentation is AI generated. If you find any issues, please let us know.

## Introduction
### [Overview of Flux](introduction/overview.md)
### [Key Features](introduction/features.md)
  - [High-Performance Task Execution](introduction/features.md#high-performance-task-execution)
  - [Fault-Tolerance](introduction/features.md#fault-tolerance)
  - [Durable Execution](introduction/features.md#durable-execution)
  - [Workflow Controls](introduction/features.md#workflow-controls)
  - [API Integration](introduction/features.md#api-integration)
  - [Security](introduction/features.md#security)
  - [Development Features](introduction/features.md#development-features)
### [Use Cases](introduction/use-cases.md)

## Getting Started
### [Installation](getting-started/installation.md)
   - [Requirements](getting-started/installation.md#requirements)
   - [Installation Guide](getting-started/installation.md#installation-guide)
   - [Quick Setup](getting-started/installation.md#quick-setup)

### [Basic Concepts](getting-started/basic_concepts.md)
   - [Workflows](getting-started/basic_concepts.md#workflows)
   - [Tasks](getting-started/basic_concepts.md#tasks)
   - [Execution Context](getting-started/basic_concepts.md#execution-context)
   - [Events](getting-started/basic_concepts.md#events)

### [Quick Start Guide](getting-started/quick-start-guide.md)
   - [First Workflow](getting-started/quick-start-guide.md#first-workflow)
   - [Running Workflows](getting-started/quick-start-guide.md#running-workflows)

## CLI Reference
### [Command Overview](cli/index.md)
   - [Installation & Setup](cli/index.md#installation)
   - [Basic Usage](cli/index.md#basic-usage)
   - [Configuration](cli/index.md#configuration)

### [Workflow Commands](cli/workflow.md)
   - [List Workflows](cli/workflow.md#flux-workflow-list)
   - [Register Workflows](cli/workflow.md#flux-workflow-register)
   - [Run Workflows](cli/workflow.md#flux-workflow-run)
   - [Check Status](cli/workflow.md#flux-workflow-status)

### [Service Commands](cli/start.md)
   - [Start Server](cli/start.md#flux-start-server)
   - [Start Worker](cli/start.md#flux-start-worker)
   - [Start MCP Server](cli/start.md#flux-start-mcp)

### [Secrets Management](cli/secrets.md)
   - [List Secrets](cli/secrets.md#flux-secrets-list)
   - [Set Secrets](cli/secrets.md#flux-secrets-set)
   - [Retrieve Secrets](cli/secrets.md#flux-secrets-get)
   - [Remove Secrets](cli/secrets.md#flux-secrets-remove)

## Core Concepts
### [Workflow Management](core-concepts/workflow-management.md)
   - [Creating Workflows](core-concepts/workflow-management.md#creating-workflows)
   - [Workflow Lifecycle](core-concepts/workflow-management.md#workflow-lifecycle)
   - [Workflow States](core-concepts/workflow-management.md#workflow-states)

### [Task System](core-concepts/tasks.md)
   - [Task Creation](core-concepts/tasks.md#task-creation)
   - [Task Options](core-concepts/tasks.md#task-options)
   - [Task Composition](core-concepts/tasks.md#task-composition)
   - [Error Handling](core-concepts/tasks.md#error-handling)
   - [Built-in Tasks](core-concepts/tasks.md#built-in-tasks)

### [Execution Model](core-concepts/execution-model.md)
   - [Local Execution](core-concepts/execution-model.md#local-execution)
   - [API-based Execution](core-concepts/execution-model.md#api-based-execution)
   - [Execution Context](core-concepts/execution-model.md#execution-context)
   - [Paused Workflows](core-concepts/execution-model.md#paused-workflows)
   - [State Management](core-concepts/execution-model.md#state-management)
   - [Event System](core-concepts/execution-model.md#event-system)

### [Error Handling & Recovery](core-concepts/error-handling.md)
   - [Task-Level Error Handling](core-concepts/error-handling.md#task-level-error-handling)
   - [Retry Mechanisms](core-concepts/error-handling.md#retry-mechanism)
   - [Fallback Strategies](core-concepts/error-handling.md#fallback-strategy)
   - [Rollback Operations](core-concepts/error-handling.md#rollback-operations)
   - [Timeout Handling](core-concepts/error-handling.md#timeout-handling)

## Advanced Features
### [Task Patterns](advanced-features/task-patterns.md)
   - [Parallel Execution](advanced-features/task-patterns.md#parallel-execution)
   - [Pipeline Processing](advanced-features/task-patterns.md#pipeline-processing)
   - [Task Mapping](advanced-features/task-patterns.md#task-mapping)
   - [Graph](advanced-features/task-patterns.md#graph)
   - [Performance Considerations](advanced-features/task-patterns.md#performance-considerations)

### [Workflow Controls](advanced-features/workflow-controls.md)
   - [Workflow Pause Points](advanced-features/workflow-controls.md#workflow-pause-points)
   - [Workflow Replay](advanced-features/workflow-controls.md#workflow-replay)
   - [Subworkflow Support](advanced-features/workflow-controls.md#subworkflows)

### [Workflow Cancellation](advanced-features/cancellation.md)
   - [Overview](advanced-features/cancellation.md#overview)
   - [Cancellation States](advanced-features/cancellation.md#cancellation-states)
   - [API and CLI Usage](advanced-features/cancellation.md#how-to-use)
   - [Implementation Details](advanced-features/cancellation.md#implementation-details)

## Appendix
- [Examples in the Repository](https://github.com/edurdias/flux/tree/main/examples)
- [API Reference](https://github.com/edurdias/flux/tree/main/flux)
- [Version History](https://github.com/edurdias/flux/releases)
- [Contributing Guidelines](https://github.com/edurdias/flux/blob/main/CONTRIBUTING.md)
