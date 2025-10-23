from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
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


@workflow.command("resume")
@click.argument("workflow_name")
@click.argument("execution_id")
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
def resume_workflow(
    workflow_name: str,
    execution_id: str,
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
                f"{base_url}/workflows/{workflow_name}/resume/{execution_id}/{mode}",
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
def schedule():
    """Manage workflow schedules."""
    pass


@schedule.command("create")
@click.argument("workflow_name")
@click.argument("schedule_name")
@click.option(
    "--cron",
    "-c",
    default=None,
    help="Cron expression (e.g., '0 9 * * MON-FRI' for 9 AM weekdays)",
)
@click.option(
    "--interval-hours",
    default=None,
    type=int,
    help="Interval in hours",
)
@click.option(
    "--interval-minutes",
    default=None,
    type=int,
    help="Interval in minutes",
)
@click.option(
    "--timezone",
    "-tz",
    default="UTC",
    help="Timezone for the schedule (default: UTC)",
)
@click.option(
    "--description",
    "-d",
    default=None,
    help="Description of the schedule",
)
@click.option(
    "--input",
    "-i",
    default=None,
    help="Input data for scheduled workflow executions (JSON format)",
)
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def create_schedule(
    workflow_name: str,
    schedule_name: str,
    cron: str | None,
    interval_hours: int | None,
    interval_minutes: int | None,
    timezone: str,
    description: str | None,
    input: str | None,
    server_url: str | None,
):
    """Create a new schedule for a workflow."""
    try:
        # Validate schedule parameters
        if not cron and not interval_hours and not interval_minutes:
            click.echo("Error: Must specify either --cron or --interval-* options", err=True)
            return

        if cron and (interval_hours or interval_minutes):
            click.echo("Error: Cannot specify both cron and interval options", err=True)
            return

        # Build schedule config
        if cron:
            schedule_config: dict[str, Any] = {
                "type": "cron",
                "cron_expression": cron,
                "timezone": timezone,
            }
        else:
            schedule_config = {
                "type": "interval",
                "interval_seconds": (interval_hours or 0) * 3600 + (interval_minutes or 0) * 60,
                "timezone": timezone,
            }

        # Parse input if provided
        input_data = None
        if input:
            input_data = parse_value(input)

        # Prepare request
        request_data = {
            "workflow_name": workflow_name,
            "name": schedule_name,
            "schedule_config": schedule_config,
            "description": description,
            "input_data": input_data,
        }

        base_url = server_url or get_server_url()

        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{base_url}/schedules", json=request_data)
            response.raise_for_status()
            result = response.json()

        click.echo(
            f"Successfully created schedule '{schedule_name}' for workflow '{workflow_name}'",
        )
        click.echo(f"Schedule ID: {result['id']}")
        click.echo(f"Next run: {result.get('next_run_at', 'Not scheduled')}")

    except Exception as ex:
        click.echo(f"Error creating schedule: {str(ex)}", err=True)


@schedule.command("list")
@click.option(
    "--workflow",
    "-w",
    default=None,
    help="Filter by workflow name",
)
@click.option(
    "--all",
    "-a",
    "show_all",
    is_flag=True,
    help="Show all schedules including paused/disabled ones",
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def list_schedules(workflow: str | None, show_all: bool, format: str, server_url: str | None):
    """List all schedules."""
    try:
        base_url = server_url or get_server_url()
        params: dict[str, Any] = {"active_only": not show_all}
        if workflow:
            params["workflow_name"] = workflow

        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{base_url}/schedules", params=params)
            response.raise_for_status()
            schedules = response.json()

        if not schedules:
            click.echo("No schedules found.")
            return

        if format == "json":
            click.echo(json.dumps(schedules, indent=2))
        else:
            click.echo(f"Found {len(schedules)} schedule(s):")
            click.echo()
            for schedule in schedules:
                status_indicator = "✓" if schedule["status"] == "active" else "⏸"
                click.echo(f"{status_indicator} {schedule['name']} ({schedule['workflow_name']})")
                click.echo(f"   Type: {schedule['schedule_type']} | Status: {schedule['status']}")
                click.echo(f"   Next run: {schedule.get('next_run_at', 'Not scheduled')}")
                click.echo(
                    f"   Runs: {schedule['run_count']} | Failures: {schedule['failure_count']}",
                )
                if schedule.get("description"):
                    click.echo(f"   Description: {schedule['description']}")
                click.echo()

    except Exception as ex:
        click.echo(f"Error listing schedules: {str(ex)}", err=True)


@schedule.command("show")
@click.argument("schedule_id")
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def show_schedule(schedule_id: str, server_url: str | None):
    """Show details of a specific schedule."""
    try:
        base_url = server_url or get_server_url()

        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{base_url}/schedules/{schedule_id}")
            response.raise_for_status()
            schedule = response.json()

        click.echo(f"\nSchedule: {schedule['name']}")
        click.echo(f"ID: {schedule['id']}")
        click.echo(f"Workflow: {schedule['workflow_name']}")
        click.echo(f"Type: {schedule['schedule_type']}")
        click.echo(f"Status: {schedule['status']}")
        click.echo(f"Created: {schedule['created_at']}")
        click.echo(f"Updated: {schedule['updated_at']}")
        click.echo(f"Last run: {schedule.get('last_run_at', 'Never')}")
        click.echo(f"Next run: {schedule.get('next_run_at', 'Not scheduled')}")
        click.echo(f"Total runs: {schedule['run_count']}")
        click.echo(f"Failures: {schedule['failure_count']}")

        if schedule.get("description"):
            click.echo(f"Description: {schedule['description']}")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Schedule '{schedule_id}' not found.", err=True)
        else:
            click.echo(f"Error showing schedule: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error showing schedule: {str(ex)}", err=True)


@schedule.command("pause")
@click.argument("schedule_id")
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def pause_schedule(schedule_id: str, server_url: str | None):
    """Pause a schedule."""
    try:
        base_url = server_url or get_server_url()

        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{base_url}/schedules/{schedule_id}/pause")
            response.raise_for_status()
            result = response.json()

        click.echo(f"Successfully paused schedule '{result['name']}'")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Schedule '{schedule_id}' not found.", err=True)
        else:
            click.echo(f"Error pausing schedule: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error pausing schedule: {str(ex)}", err=True)


@schedule.command("resume")
@click.argument("schedule_id")
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def resume_schedule(schedule_id: str, server_url: str | None):
    """Resume a paused schedule."""
    try:
        base_url = server_url or get_server_url()

        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{base_url}/schedules/{schedule_id}/resume")
            response.raise_for_status()
            result = response.json()

        click.echo(f"Successfully resumed schedule '{result['name']}'")
        click.echo(f"Next run: {result.get('next_run_at', 'Not scheduled')}")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Schedule '{schedule_id}' not found.", err=True)
        else:
            click.echo(f"Error resuming schedule: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error resuming schedule: {str(ex)}", err=True)


@schedule.command("delete")
@click.argument("schedule_id")
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
@click.confirmation_option(prompt="Are you sure you want to delete this schedule?")
def delete_schedule(schedule_id: str, server_url: str | None):
    """Delete a schedule."""
    try:
        base_url = server_url or get_server_url()

        with httpx.Client(timeout=30.0) as client:
            response = client.delete(f"{base_url}/schedules/{schedule_id}")
            response.raise_for_status()

        click.echo(f"Successfully deleted schedule '{schedule_id}'")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Schedule '{schedule_id}' not found.", err=True)
        else:
            click.echo(f"Error deleting schedule: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error deleting schedule: {str(ex)}", err=True)


@schedule.command("history")
@click.argument("schedule_id")
@click.option(
    "--limit",
    "-l",
    default=10,
    type=int,
    help="Number of history entries to show (default: 10)",
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def schedule_history(schedule_id: str, limit: int, format: str, server_url: str | None):
    """Show execution history for a schedule."""
    try:
        base_url = server_url or get_server_url()
        params = {"limit": limit}

        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{base_url}/schedules/{schedule_id}/history", params=params)
            response.raise_for_status()
            history = response.json()

        if not history:
            click.echo("No execution history found.")
            return

        if format == "json":
            click.echo(json.dumps(history, indent=2))
        else:
            click.echo(f"Execution history for schedule '{schedule_id}':")
            click.echo()
            for entry in history:
                status_icon = (
                    "✓"
                    if entry["status"] == "completed"
                    else "✗"
                    if entry["status"] == "failed"
                    else "⏸"
                )
                click.echo(f"{status_icon} {entry['scheduled_at']} - {entry['status'].upper()}")

                if entry.get("execution_id"):
                    click.echo(f"   Execution ID: {entry['execution_id']}")
                if entry.get("started_at"):
                    click.echo(f"   Started: {entry['started_at']}")
                if entry.get("completed_at"):
                    click.echo(f"   Completed: {entry['completed_at']}")
                if entry.get("error_message"):
                    click.echo(f"   Error: {entry['error_message']}")
                click.echo()

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Schedule '{schedule_id}' not found.", err=True)
        else:
            click.echo(f"Error getting schedule history: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error getting schedule history: {str(ex)}", err=True)


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
