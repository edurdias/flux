# Flux Documentation

> A distributed workflow orchestration engine for building stateful and fault-tolerant workflows

## 1. Introduction
- **[Welcome](introduction/welcome.md)** - What is Flux, vision, and why use it
- **[Key Features](introduction/features.md)** - Core capabilities overview
- **[Architecture Overview](introduction/architecture.md)** - High-level system design
<!-- - **[Comparison](introduction/comparison.md)** - How Flux differs from similar tools -->

## 2. Getting Started

### Quick Start
- **[Installation](getting-started/installation.md)** - Setup and prerequisites
- **[Your First Workflow](getting-started/first-workflow.md)** - Simple hello world example
- **[Basic Concepts](getting-started/basic-concepts.md)** - Tasks, workflows, and execution context

### Core Concepts
- **[Tasks](getting-started/tasks.md)** - Definition, decoration, and configuration
- **[Workflows](getting-started/workflows.md)** - Creating and structuring workflows
- **[Execution Context](getting-started/execution-context.md)** - State management and data flow
- **[Task Options](getting-started/task-options.md)** - Retry, timeout, fallback, and rollback
- **[Built-in Tasks](getting-started/built-in-tasks.md)** - Overview of provided utilities

### First Steps Tutorial
- **[Simple Workflow](getting-started/tutorials/simple-workflow.md)** - Building your first meaningful workflow
- **[Adding Error Handling](getting-started/tutorials/error-handling.md)** - Retry and fallback mechanisms
- **[Parallel Execution](getting-started/tutorials/parallel-execution.md)** - Using parallel tasks
- **[Pipeline Processing](getting-started/tutorials/pipeline-processing.md)** - Sequential task chains

## 3. User Guide

### Building Workflows
- **[Task Definition and Decoration](user-guide/task-definition.md)**
  - Basic task creation
  - Task options and configuration
  - Input/output handling
  - Type safety and hints
- **[Workflow Patterns](user-guide/workflow-patterns.md)**
  - Sequential workflows
  - Parallel execution
  - Conditional workflows
  - Dynamic workflows
  - Subworkflows and composition
- **[Data Flow and State Management](user-guide/data-flow.md)**
  - Execution context usage
  - State persistence
  - Data passing between tasks
  - Memory management

### Advanced Features
- **[Task Configuration](user-guide/task-configuration.md)**
  - Retry policies and backoff
  - Timeout management
  - Fallback and rollback handlers
  - Caching strategies
- **[Workflow Control](user-guide/workflow-control.md)**
  - Pause and resume
  - Deterministic replay
  - State inspection
  - Event handling
- **[Task Mapping and Iteration](user-guide/task-mapping.md)**
  - Map operations
  - Batch processing
  - Dynamic task creation

### Error Handling and Resilience
- **[Error Management](user-guide/error-management.md)**
  - Exception handling patterns
  - Retry strategies
  - Fallback mechanisms
  - Rollback procedures
- **[Fault Tolerance](user-guide/fault-tolerance.md)**
  - State persistence
  - Recovery patterns
  - Graceful degradation

### Security and Secrets
- **[Secrets Management](user-guide/secrets-management.md)**
  - CLI secrets operations
  - HTTP API for secrets
  - Task-level secret requests
  - Best practices
- **[Security Considerations](user-guide/security.md)**
  - Access control
  - Secure execution
  - Network security

## 4. Deployment

### Local Development
- **[Local Execution](deployment/local-execution.md)** - Running workflows on your machine
- **[Development Workflow](deployment/development-workflow.md)** - Best practices for development
- **[Testing and Debugging](deployment/testing-debugging.md)** - Testing strategies and tools

### Distributed Deployment
- **[Server Architecture](deployment/server-architecture.md)** - Understanding the server/worker model
- **[Server Setup](deployment/server-setup.md)** - Starting and configuring the Flux server
- **[Worker Management](deployment/worker-management.md)** - Deploying and scaling workers
- **[Network Configuration](deployment/network-configuration.md)** - Host, port, and connectivity setup

### Production Deployment
- **[Deployment Strategies](deployment/deployment-strategies.md)** - Best practices for production
- **[Scaling and Performance](deployment/scaling-performance.md)** - Optimizing for high throughput
- **[Monitoring and Observability](deployment/monitoring-observability.md)** - Tracking workflow execution
- **[High Availability](deployment/high-availability.md)** - Redundancy and failover strategies

## 5. API Reference

### HTTP API
- **[Workflow Management](api-reference/http-api/workflow-management.md)**
  - Upload workflows
  - List and inspect workflows
  - Workflow metadata
- **[Execution Control](api-reference/http-api/execution-control.md)**
  - Synchronous execution
  - Asynchronous execution
  - Streaming execution
- **[Status and Monitoring](api-reference/http-api/status-monitoring.md)**
  - Execution status
  - Detailed run information
  - Event streaming
- **[Administration](api-reference/http-api/administration.md)**
  - Secrets management
  - Server configuration
  - Health checks

### CLI Reference
- **[Command Overview](api-reference/cli/overview.md)**
  - Installation & setup
  - Basic usage
  - Configuration
- **[Service Commands](api-reference/cli/service-commands.md)**
  - Start server (`flux start server`)
  - Start worker (`flux start worker`)
  - Start MCP server (`flux start mcp`)
- **[Workflow Commands](api-reference/cli/workflow-commands.md)**
  - List workflows (`flux workflow list`)
  - Register workflows (`flux workflow register`)
  - Run workflows (`flux workflow run`)
  - Check status (`flux workflow status`)
- **[Secrets Management](api-reference/cli/secrets-commands.md)**
  - List secrets (`flux secrets list`)
  - Set secrets (`flux secrets set`)
  - Retrieve secrets (`flux secrets get`)
  - Remove secrets (`flux secrets remove`)

### Python API
- **[Core Decorators](api-reference/python-api/core-decorators.md)**
  - `@task` decorator and options
  - `@workflow` decorator
  - Task configuration methods
- **[Built-in Tasks](api-reference/python-api/built-in-tasks.md)**
  - Time operations (`now`, `sleep`)
  - Random operations (`choice`, `randint`, `randrange`)
  - Utilities (`uuid4`, `pause`)
  - Orchestration (`parallel`, `pipeline`, `call`)
- **[Execution Context](api-reference/python-api/execution-context.md)**
  - Context properties and methods
  - State inspection
  - Event access
- **[Workflow Execution](api-reference/python-api/workflow-execution.md)**
  - Local execution methods
  - Remote execution
  - Execution options

## 6. Tutorials

### Beginner Tutorials
- **[Building Your First Data Pipeline](tutorials/beginner/first-data-pipeline.md)** - End-to-end example
- **[Adding Resilience to Workflows](tutorials/beginner/adding-resilience.md)** - Error handling tutorial
- **[Working with External APIs](tutorials/beginner/external-apis.md)** - Integration patterns
- **[Scheduling and Automation](tutorials/beginner/scheduling-automation.md)** - Time-based execution

### Intermediate Tutorials
- **[Multi-Step Data Processing](tutorials/intermediate/multi-step-processing.md)** - Complex pipeline example
- **[Distributed Computing Patterns](tutorials/intermediate/distributed-patterns.md)** - Leveraging multiple workers
- **[State Management in Long-Running Workflows](tutorials/intermediate/state-management.md)** - Persistence patterns
- **[Building Reusable Workflow Components](tutorials/intermediate/reusable-components.md)** - Modular design

### Advanced Tutorials
- **[Custom Task Types and Extensions](tutorials/advanced/custom-task-types.md)** - Extending Flux functionality
- **[Performance Optimization](tutorials/advanced/performance-optimization.md)** - Tuning for high throughput
- **[Integration with External Systems](tutorials/advanced/external-systems.md)** - Database, message queues, etc.
- **[Building Workflow Libraries](tutorials/advanced/workflow-libraries.md)** - Creating reusable components

## 7. Integrations

### Data Sources and Sinks
- **[Databases](integrations/databases.md)** - SQL and NoSQL integration patterns
- **[File Systems](integrations/file-systems.md)** - Local and cloud storage
- **[Message Queues](integrations/message-queues.md)** - Async communication patterns
- **[APIs and Web Services](integrations/apis-web-services.md)** - HTTP integration

### Agent Protocols
- **[Model Context Protocol (MCP)](integrations/protocols/mcp.md)** - LLM integration and context sharing
- **[Agent Communication Protocol (ACP)](integrations/protocols/acp.md)** - Agent-to-agent communication
- **[Custom Protocol Adapters](integrations/protocols/custom-adapters.md)** - Building protocol bridges

### Development Tools
- **[IDE Integration](integrations/development-tools/ide-integration.md)** - VS Code, PyCharm setup
- **[Testing Frameworks](integrations/development-tools/testing-frameworks.md)** - Unit and integration testing
- **[CI/CD Integration](integrations/development-tools/cicd-integration.md)** - Automated deployment
- **[Monitoring Tools](integrations/development-tools/monitoring-tools.md)** - Observability stack integration

### Cloud Platforms
- **[AWS Deployment](integrations/cloud-platforms/aws.md)** - ECS, Lambda, and other services
- **[Google Cloud](integrations/cloud-platforms/gcp.md)** - GCP deployment patterns
- **[Azure](integrations/cloud-platforms/azure.md)** - Azure-specific configuration
- **[Kubernetes](integrations/cloud-platforms/kubernetes.md)** - Container orchestration

## 8. Performance and Optimization

### Performance Tuning
- **[Task Optimization](performance/task-optimization.md)** - Efficient task design
- **[Workflow Design Patterns](performance/workflow-design-patterns.md)** - Scalable workflow architecture
- **[Resource Management](performance/resource-management.md)** - CPU, memory, and I/O optimization
- **[Caching Strategies](performance/caching-strategies.md)** - Avoiding redundant computation

### Monitoring and Observability
- **[Metrics and Logging](performance/metrics-logging.md)** - Built-in observability features
- **[Performance Monitoring](performance/performance-monitoring.md)** - Tracking execution performance
- **[Alerting and Notifications](performance/alerting-notifications.md)** - Setting up alerts
- **[Troubleshooting](performance/troubleshooting.md)** - Common issues and solutions

## 9. Contributing

### Development Setup
- **[Environment Setup](contributing/environment-setup.md)** - Local development environment
- **[Code Organization](contributing/code-organization.md)** - Project structure understanding
- **[Development Tools](contributing/development-tools.md)** - Linting, testing, and quality tools

### Contributing Guidelines
- **[Code Standards](contributing/code-standards.md)** - Style guide and best practices
- **[Testing Requirements](contributing/testing-requirements.md)** - Test coverage and standards
- **[Documentation Standards](contributing/documentation-standards.md)** - Contributing to documentation
- **[Pull Request Process](contributing/pull-request-process.md)** - How to contribute code

### Community
- **[Getting Help](contributing/getting-help.md)** - Support channels and resources
- **[Community Guidelines](contributing/community-guidelines.md)** - Code of conduct
- **[Roadmap](contributing/roadmap.md)** - Future development plans
- **[License](contributing/license.md)** - Apache 2.0 license details

## 10. Reference

### Configuration Reference
- **[Server Configuration](reference/configuration/server-configuration.md)** - All server configuration options
- **[Worker Configuration](reference/configuration/worker-configuration.md)** - Worker-specific settings
- **[Environment Variables](reference/configuration/environment-variables.md)** - Configuration via environment
- **[Configuration Files](reference/configuration/configuration-files.md)** - YAML/JSON configuration formats

### Troubleshooting
- **[Common Issues](reference/troubleshooting/common-issues.md)** - FAQ and common problems
- **[Error Messages](reference/troubleshooting/error-messages.md)** - Understanding error codes
- **[Debugging Guide](reference/troubleshooting/debugging-guide.md)** - Debugging techniques
- **[Performance Issues](reference/troubleshooting/performance-issues.md)** - Performance troubleshooting

### Release Management
- **[Version History](reference/releases/version-history.md)** - Complete changelog
- **[Upgrade Guides](reference/releases/upgrade-guides.md)** - Step-by-step upgrade process
- **[Migration Tools](reference/releases/migration-tools.md)** - Automated migration utilities
- **[Breaking Changes](reference/releases/breaking-changes.md)** - Version compatibility matrix
- **[Deprecation Notices](reference/releases/deprecation-notices.md)** - Planned feature removals

### Support Resources
- **[Community Support](reference/support/community-support.md)** - Forums, Discord, Stack Overflow
- **[Commercial Support](reference/support/commercial-support.md)** - Enterprise support options
- **[Professional Services](reference/support/professional-services.md)** - Consulting and training
- **[Service Level Agreements](reference/support/sla.md)** - Support tiers and response times

### Appendices
- **[Glossary](reference/appendices/glossary.md)** - Terms and definitions
- **[Examples Repository](reference/appendices/examples.md)** - Links to complete examples
- **[License](reference/appendices/license.md)** - Full license text
- **[Acknowledgments](reference/appendices/acknowledgments.md)** - Contributors and attributions
