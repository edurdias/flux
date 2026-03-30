"""
Deploy Pipeline — target workflow for sub_agents_workflow example.

A simulated deployment pipeline that pauses for human approval before
proceeding. This workflow is called remotely via ``workflow_agent()``
from the sub_agents_workflow example.

Register this workflow before sub_agents_workflow:

    flux workflow register examples/ai/deploy_pipeline.py

Then register and run the orchestrator:

    flux workflow register examples/ai/sub_agents_workflow.py
    flux workflow run sub_agents_workflow '{"service": "api-gateway", "version": "2.1.0"}' --mode sync
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


@task
async def validate_deployment(service: str, version: str) -> dict[str, Any]:
    """Validate that the service and version are deployable."""
    return {
        "service": service,
        "version": version,
        "checks": {
            "image_exists": True,
            "config_valid": True,
            "dependencies_met": True,
        },
        "status": "ready",
    }


@task
async def run_deployment(service: str, version: str) -> dict[str, Any]:
    """Simulate deploying the service."""
    return {
        "service": service,
        "version": version,
        "status": "deployed",
        "replicas": 3,
        "endpoint": f"https://{service}.example.com",
    }


@workflow
async def deploy_pipeline(ctx: ExecutionContext):
    """Deployment pipeline with human approval gate.

    Input: {"instruction": "Deploy api-gateway version 2.1.0", "input": {...}}
    """
    raw = ctx.input or {}
    instruction = raw.get("instruction", "")
    input_data = raw.get("input") or {}

    service = input_data.get("service", "unknown-service")
    version = input_data.get("version", "0.0.0")

    validation = await validate_deployment(service, version)

    approval = await pause(
        {
            "message": f"Deployment of {service} v{version} requires approval.",
            "validation": validation,
            "instruction": instruction,
        },
    )

    result = await run_deployment(service, version)
    result["approved_by"] = approval
    return result
