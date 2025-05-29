from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import uuid4


import click
import httpx

from flux.config import Configuration
from flux.server import Server
from flux.worker import Worker
from flux.utils import parse_value
from flux.utils import to_json
from flux.secret_managers import SecretManager


@click.group()
def cli():
    pass


@cli.group()
def workflow():
    pass


def get_server_url():
    """Get the server URL from configuration."""
    settings = Configuration.get().settings
    return f"http://{settings.server_host}:{settings.server_port}"


@workflow.command("list")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format (simple or json)",
)
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def list_workflows(format: str, server_url: str | None):
    """List all registered workflows."""
    try:
        base_url = server_url or get_server_url()

        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{base_url}/workflows")
            response.raise_for_status()
            workflows = response.json()

        if not workflows:
            click.echo("No workflows found.")
            return

        if format == "json":
            click.echo(json.dumps(workflows, indent=2))
        else:
            for workflow in workflows:
                click.echo(f"- {workflow['name']} (version {workflow['version']})")
    except Exception as ex:
        click.echo(f"Error listing workflows: {str(ex)}", err=True)


@workflow.command("register")
@click.argument("filename")
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def register_workflows(filename: str, server_url: str | None):
    """Register workflows from a file."""
    try:
        file_path = Path(filename)
        if not file_path.exists():
            raise ValueError(f"File '{filename}' not found.")

        base_url = server_url or get_server_url()

        with httpx.Client(timeout=30.0) as client:
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, "text/x-python")}
                response = client.post(f"{base_url}/workflows", files=files)
                response.raise_for_status()
                result = response.json()

        click.echo(f"Successfully registered {len(result)} workflow(s) from '{filename}'.")
        for workflow in result:
            click.echo(f"  - {workflow['name']} (version {workflow['version']})")

    except Exception as ex:
        click.echo(f"Error registering workflow: {str(ex)}", err=True)


@workflow.command("show")
@click.argument("workflow_name")
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def show_workflow(workflow_name: str, server_url: str | None):
    """Show the details of a registered workflow."""
    try:
        base_url = server_url or get_server_url()

        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{base_url}/workflows/{workflow_name}")
            response.raise_for_status()
            workflow = response.json()

        click.echo(f"\nWorkflow: {workflow['name']}")
        click.echo(f"Version: {workflow['version']}")
        if "description" in workflow:
            click.echo(f"Description: {workflow['description']}")
        click.echo("\nDetails:")
        click.echo("-" * 50)
        click.echo(to_json(workflow))

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Workflow '{workflow_name}' not found.", err=True)
        else:
            click.echo(f"Error showing workflow: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error showing workflow: {str(ex)}", err=True)


@workflow.command("run")
@click.argument("workflow_name")
@click.argument("input")
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["sync", "async", "stream"]),
    default="async",
    help="Execution mode (sync, async, or stream)",
)
@click.option(
    "--detailed",
    "-d",
    is_flag=True,
    help="Show detailed execution information",
)
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def run_workflow(
    workflow_name: str,
    input: str,
    mode: str,
    detailed: bool,
    server_url: str | None,
):
    """Run the specified workflow."""
    try:
        base_url = server_url or get_server_url()
        parsed_input = parse_value(input)

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{base_url}/workflows/{workflow_name}/run/{mode}",
                json=parsed_input,
                params={"detailed": detailed},
            )
            response.raise_for_status()

            if mode == "stream":
                # Handle streaming response
                click.echo("Streaming execution...")
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data = line[6:]  # Remove "data: " prefix
                        if data.strip():
                            try:
                                event_data = json.loads(data)
                                click.echo(to_json(event_data))
                            except json.JSONDecodeError:
                                click.echo(data)
            else:
                result = response.json()
                click.echo(to_json(result))

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Workflow '{workflow_name}' not found.", err=True)
        else:
            click.echo(f"Error running workflow: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error running workflow: {str(ex)}", err=True)


@workflow.command("status")
@click.argument("workflow_name")
@click.argument("execution_id")
@click.option(
    "--detailed",
    "-d",
    is_flag=True,
    help="Show detailed execution information",
)
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def workflow_status(
    workflow_name: str,
    execution_id: str,
    detailed: bool,
    server_url: str | None,
):
    """Check the status of a workflow execution."""
    try:
        base_url = server_url or get_server_url()

        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                f"{base_url}/workflows/{workflow_name}/status/{execution_id}",
                params={"detailed": detailed},
            )
            response.raise_for_status()
            result = response.json()

        click.echo(to_json(result))

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(
                f"Execution '{execution_id}' not found for workflow '{workflow_name}'.",
                err=True,
            )
        else:
            click.echo(f"Error checking workflow status: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error checking workflow status: {str(ex)}", err=True)


@cli.group()
def start():
    pass


@start.command()
@click.option("--host", "-h", default=None, help="Host to bind the server to.")
@click.option(
    "--port",
    "-p",
    default=None,
    type=int,
    help="Port to bind the server to.",
)
def server(host: str | None = None, port: int | None = None):
    """Start the Flux server."""
    settings = Configuration.get().settings
    host = host or settings.server_host
    port = port or settings.server_port
    Server(host, port).start()


@start.command()
@click.argument("name", type=str, required=False)
@click.option(
    "--server-url",
    "-surl",
    default=None,
    help="Server URL to connect to.",
)
def worker(name: str | None, server_url: str | None = None):
    name = name or f"worker-{uuid4().hex[-6:]}"
    settings = Configuration.get().settings.workers
    server_url = server_url or settings.server_url
    Worker(name, server_url).start()


@start.command()
@click.option("--host", "-h", default=None, help="Host to bind the MCP server to.")
@click.option("--port", "-p", default=None, type=int, help="Port to bind the MCP server to.")
@click.option("--name", "-n", default=None, help="Name for the MCP server.")
@click.option("--server-url", "-surl", default=None, help="Server URL to connect to.")
@click.option(
    "--transport",
    "-t",
    type=click.Choice(["stdio", "streamable-http", "sse"]),
    default="streamable-http",
    help="Transport protocol for MCP (stdio, streamable-http, sse)",
)
def mcp(
    name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    server_url: str | None = None,
    transport: Literal["stdio", "streamable-http", "sse"] | None = None,
):
    """Start the Flux MCP server that exposes API endpoints as tools."""
    from flux.mcp_server import MCPServer

    MCPServer(name, host, port, server_url, transport).start()


@cli.group()
def secrets():
    """Manage Flux secrets for secure task execution."""
    pass


@secrets.command("list")
def list_secrets():
    """List all available secrets (shows only secret names, not values)."""
    try:
        secret_manager = SecretManager.current()
        secrets_list = secret_manager.all()

        if not secrets_list:
            click.echo("No secrets found.")
            return

        click.echo("Available secrets:")
        for secret_name in secrets_list:
            click.echo(f"  - {secret_name}")
    except Exception as ex:
        click.echo(f"Error listing secrets: {str(ex)}", err=True)


@secrets.command("set")
@click.argument("name")
@click.argument("value")
def set_secret(name: str, value: str):
    """Set a secret value with given name and value.

    This command will create a new secret or update an existing one.
    """
    try:
        secret_manager = SecretManager.current()
        secret_manager.save(name, value)
        click.echo(f"Secret '{name}' has been set successfully.")
    except Exception as ex:
        click.echo(f"Error setting secret: {str(ex)}", err=True)


@secrets.command("get")
@click.argument("name")
def get_secret(name: str):
    """Get a secret value by name.

    Warning: This will display the secret value in the terminal.
    Only use this command for testing or in secure environments.
    """
    try:
        if not click.confirm(f"Are you sure you want to display the secret '{name}'?"):
            click.echo("Operation cancelled.")
            return

        secret_manager = SecretManager.current()
        result = secret_manager.get([name])
        click.echo(f"Secret '{name}': {result[name]}")
    except ValueError as ex:
        click.echo(f"Secret not found: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error getting secret: {str(ex)}", err=True)


@secrets.command("remove")
@click.argument("name")
def remove_secret(name: str):
    """Remove a secret by name.

    This permanently deletes the secret from the database.
    """
    try:
        secret_manager = SecretManager.current()
        secret_manager.remove(name)
        click.echo(f"Secret '{name}' has been removed successfully.")
    except Exception as ex:
        click.echo(f"Error removing secret: {str(ex)}", err=True)


if __name__ == "__main__":  # pragma: no cover
    cli()
