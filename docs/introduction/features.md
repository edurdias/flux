# Key Features

## High-Performance Task Execution
- **Parallel Execution**: Execute multiple tasks concurrently using built-in parallel processing
- **Task Mapping**: Apply operations across collections of data efficiently
- **Pipeline Processing**: Chain tasks together in efficient processing pipelines
- **Graph-based Workflows**: Create complex task dependencies using directed acyclic graphs

## Fault-Tolerance
- **Automatic Retries**: Configure retry attempts with customizable backoff strategies
- **Fallback Mechanisms**: Define fallback behavior for failed tasks
- **Error Recovery**: Roll back failed operations with custom recovery logic
- **Task Timeouts**: Set execution time limits to prevent hanging tasks

## Durable Execution
- **State Persistence**: Maintain workflow state across executions
- **Checkpoint Support**: Create save points in long-running workflows
- **Resume Capability**: Continue workflows from their last successful state
- **Event Tracking**: Monitor and log all workflow and task events

## Workflow Controls
- **Pause/Resume**: Pause workflows at defined points and resume when ready
- **State Inspection**: Examine workflow state at any point during execution
- **Workflow Replay**: Replay workflows for debugging or recovery
- **Subworkflow Support**: Compose complex workflows from simpler ones

## Security
- **OIDC/OAuth 2.0 Authentication**: Validate JWTs from Keycloak, Auth0, Okta, Microsoft Entra ID
- **Role-Based Access Control**: Built-in roles (admin, operator, viewer) plus custom roles
- **Task-Level Authorization**: Name-derived permissions with pre-flight and runtime checks
- **Service Accounts & API Keys**: Machine-to-machine authentication with optional expiry
- **Secret Management**: Securely handle sensitive data during workflow execution
- **Encrypted Storage**: Protect sensitive data at rest

## AI & MCP Integration
- **AI Agents**: LLM-powered tasks with tool use, structured output, and conversation history
- **Agent Plans**: Structured multi-step planning with dependency tracking, replanning, and plan approval
- **Agent Skills**: Reusable prompt-based capabilities that agents discover and invoke at runtime
- **MCP Client**: Connect to external MCP servers, discover tools at runtime, and use them as Flux tasks
- **Provider Support**: Ollama, OpenAI, Anthropic, and Google Gemini for AI agents; any MCP-compliant server for tools

## API Integration
- **HTTP API**: Built-in FastAPI server for HTTP access
- **RESTful Endpoints**: Easy-to-use REST API for workflow management
- **Programmatic Access**: Python API for direct integration

## Development Features
- **Type Safety**: Full type hinting support for better development experience
- **Testing Support**: Comprehensive testing utilities for workflows
- **Debugging Tools**: Rich debugging information and state inspection
- **Local Development**: Easy local development and testing workflow
