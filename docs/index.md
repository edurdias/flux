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
   - [Task Caching](core-concepts/error-handling.md#task-caching)

## Advanced Features
### [Task Patterns](advanced-features/task-patterns.md)
   - [Parallel Execution](advanced-features/task-patterns.md#parallel-execution)
   - [Pipeline Processing](advanced-features/task-patterns.md#pipeline-processing)
   - [Task Mapping](advanced-features/task-patterns.md#task-mapping)
   - [Graph](advanced-features/task-patterns.md#graph)
   - [AI Agents](advanced-features/task-patterns.md#ai-agents)
   - [Agent Skills](advanced-features/task-patterns.md#agent-skills)
   - [MCP Client](advanced-features/task-patterns.md#mcp-client)
   - [Performance Considerations](advanced-features/task-patterns.md#performance-considerations)

### [AI Agents](advanced-features/ai-agents.md)
   - [Supported Providers](advanced-features/ai-agents.md#supported-providers)
   - [Tool Calling](advanced-features/ai-agents.md#tool-calling)
   - [Streaming](advanced-features/ai-agents.md#streaming)
   - [Structured Output](advanced-features/ai-agents.md#structured-output)
   - [Memory](advanced-features/ai-agents.md#memory)

### [Agent Plans](advanced-features/agent-plans.md)
   - [Plan Tools](advanced-features/agent-plans.md#the-six-tools)
   - [Step Lifecycle](advanced-features/agent-plans.md#step-lifecycle)
   - [Replanning](advanced-features/agent-plans.md#replanning)
   - [Plan Approval](advanced-features/agent-plans.md#plan-approval)
   - [Planning with Sub-Agents](advanced-features/agent-plans.md#planning-with-sub-agents)

### [Agent Skills](advanced-features/agent-skills.md)
   - [Defining Skills](advanced-features/agent-skills.md#defining-skills)
   - [SkillCatalog](advanced-features/agent-skills.md#skillcatalog)
   - [Agent Integration](advanced-features/agent-skills.md#agent-integration)
   - [Event Tracking](advanced-features/agent-skills.md#event-tracking)
   - [Error Handling](advanced-features/agent-skills.md#error-handling)

### [MCP Client](advanced-features/mcp-client.md)
   - [Tool Discovery](advanced-features/mcp-client.md#tool-discovery)
   - [Task Options](advanced-features/mcp-client.md#task-options)
   - [Connection Modes](advanced-features/mcp-client.md#connection-modes)
   - [Authentication](advanced-features/mcp-client.md#authentication)
   - [Agent Integration](advanced-features/mcp-client.md#agent-integration)
   - [Multi-Server](advanced-features/mcp-client.md#multi-server)
   - [Pause/Resume](advanced-features/mcp-client.md#pauseresume)

### [Workflow Controls](advanced-features/workflow-controls.md)
   - [Workflow Pause Points](advanced-features/workflow-controls.md#workflow-pause-points)
   - [Workflow Replay](advanced-features/workflow-controls.md#workflow-replay)
   - [Subworkflow Support](advanced-features/workflow-controls.md#subworkflows)

### [Cancellation](advanced-features/cancellation.md)
   - [Cancellation States](advanced-features/cancellation.md#cancellation-states)
   - [API Endpoint](advanced-features/cancellation.md#api-endpoint)
   - [Command Line Interface](advanced-features/cancellation.md#command-line-interface)

### [Observability](advanced-features/observability.md)
   - [Configuration](advanced-features/observability.md#configuration)
   - [Metrics](advanced-features/observability.md#metrics)
   - [Distributed Tracing](advanced-features/observability.md#distributed-tracing)

## Appendix
- [Examples in the Repository](https://github.com/flux-framework/flux/tree/main/examples)
- [API Reference](https://github.com/flux-framework/flux/tree/main/flux)
- [Version History](https://github.com/flux-framework/flux/releases)
- [Contributing Guidelines](https://github.com/flux-framework/flux/blob/main/CONTRIBUTING.md)
