"""
Workflow Sub-Agents Example.

Demonstrates a workflow agent that delegates to a remote Flux workflow,
including the pause/resume pattern for human approval.

Prerequisites:
    1. A running Flux server with a 'deploy_pipeline' workflow registered
    2. Set FLUX_SERVER_URL if not using the default (http://localhost:8000)
    3. Install Ollama and pull a model: ollama pull llama3.2

Usage:
    flux workflow run sub_agents_workflow '{"service": "api-gateway", "version": "2.1.0"}'
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent, workflow_agent

deployer = workflow_agent(
    name="deployer",
    description="Handles deployment pipelines. Delegate for deploy, "
    "rollback, or release operations. May pause for human approval.",
    workflow="deploy_pipeline",
)

manager = agent(
    "You are a release manager. When asked to deploy a service:\n"
    "1. Delegate to the 'deployer' agent with the service name and version\n"
    "2. If the deployment pauses for approval, review the details and "
    "resume with 'approved'\n"
    "3. Report the final deployment status",
    model="ollama/llama3.2",
    agents=[deployer],
)


@workflow
async def sub_agents_workflow(ctx: ExecutionContext):
    """Deploy a service using a workflow-backed sub-agent."""
    raw = ctx.input or {}
    service = raw.get("service", "my-service")
    version = raw.get("version", "1.0.0")
    return await manager(f"Deploy {service} version {version} to production")


if __name__ == "__main__":  # pragma: no cover
    result = sub_agents_workflow.run({"service": "api-gateway", "version": "2.1.0"})
    if result.has_succeeded:
        print(result.output)
    else:
        print(f"Failed: {result.output}")
