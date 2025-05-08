from __future__ import annotations

import inspect
import json
from typing import Any

import click
import uvicorn

import flux.decorators as decorators
from flux.api import create_app
from flux.catalogs import WorkflowCatalog
from flux.config import Configuration
from flux.utils import import_module_from_file
from flux.utils import parse_value
from flux.utils import to_json


@click.group()
def cli():
    pass


@cli.group()
def workflow():
    pass


@workflow.command("list")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format (simple or json)",
)
def list_workflows(format: str):
    """List all registered workflows."""
    try:
        workflows = WorkflowCatalog.create().all()

        if not workflows:
            click.echo("No workflows found.")
            return

        if format == "json":
            output = [{"name": w.name, "version": w.version} for w in workflows]
            click.echo(json.dumps(output, indent=2))
        else:
            for workflow in workflows:
                click.echo(f"- {workflow.name} (version {workflow.version})")
    except Exception as ex:
        click.echo(f"Error listing workflows: {str(ex)}", err=True)


@workflow.command("register")
@click.argument("filename")
@click.argument("workflow_name")
def register_workflow(filename: str, workflow_name: str):
    """Register a workflow from a file."""

    try:
        module = import_module_from_file(filename)

        if not hasattr(module, workflow_name):
            raise ValueError(f"Workflow '{workflow_name}' not found in file '{filename}'.")

        workflow = getattr(module, workflow_name)

        if not isinstance(workflow, decorators.workflow):
            raise ValueError(f"Object '{workflow_name}' is not a valid workflow.")

        WorkflowCatalog.create().save(workflow)

        click.echo(f"Successfully registered workflow '{workflow.name}'.")

    except Exception as ex:
        click.echo(f"Error registering workflow: {str(ex)}", err=True)


@workflow.command("show")
@click.argument("workflow_name")
@click.option("--version", "-v", type=int, help="Specific version to show")
def show_workflow(workflow_name: str, version: int | None):
    """Show the details of a registered workflow."""
    try:
        catalog = WorkflowCatalog.create()
        workflow = catalog.get(workflow_name, version)

        if not workflow:
            click.echo(f"Workflow '{workflow_name}' not found.", err=True)
            return

        click.echo(f"\nWorkflow: {workflow.name}")
        click.echo(f"Version: {workflow.version}")
        click.echo("\nCode:")
        click.echo("-" * 100)
        click.echo(inspect.getsource(workflow.code._func))

    except Exception as ex:
        click.echo(f"Error showing workflow: {str(ex)}", err=True)


@workflow.command("delete")
@click.argument("workflow_name")
@click.option("--version", "-v", type=int, help="Specific version to delete")
def delete_workflow(workflow_name: str, version: int | None):
    """Delete a registered workflow."""
    try:
        msg = (
            f"Are you sure you want to delete workflow '{workflow_name}'"
            f"{f' version {version}' if version else ''}"
        )
        if not click.confirm(msg):
            return

        catalog = WorkflowCatalog.create()
        if version:
            catalog.delete(workflow_name, version)
            click.echo(f"Deleted workflow '{workflow_name}' version {version}")
        else:
            catalog.delete(workflow_name)
            click.echo(f"Deleted all versions of workflow '{workflow_name}'")

    except Exception as ex:
        click.echo(f"Error deleting workflow: {str(ex)}", err=True)


@workflow.command("run")
@click.argument("workflow_name")
@click.argument("input")
@click.option("--version", "-v", type=int, help="Specific version to run")
@click.option("--execution-id", "-e", help="Execution ID for existing executions")
@click.option("--inspect", "-i", is_flag=True, help="Show detailed execution information")
def run_workflow(
    workflow_name: str,
    input: Any,
    version: int | None,
    execution_id: str | None,
    inspect: bool,
):
    """Run the specified workflow."""
    try:
        workflow = WorkflowCatalog.create().get(workflow_name, version).code
        context = workflow.run(parse_value(input), execution_id)
        output = context if inspect else context.summary()

        click.echo(to_json(output))

    except Exception as ex:
        click.echo(f"Error running workflow: {str(ex)}", err=True)


@cli.command()
@click.argument("path")
@click.option("--host", "-h", default=None, help="Host to bind the server to.")
@click.option("--port", "-p", default=None, help="Port to bind the server to.")
def start(path: str, host: str | None = None, port: int | None = None):
    """Start the server to execute Workflows via API."""
    settings = Configuration.get().settings
    uvicorn.run(
        create_app(path),
        port=port or settings.server_port,
        host=host or settings.server_host,
    )


if __name__ == "__main__":  # pragma: no cover
    cli()
