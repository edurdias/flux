from __future__ import annotations

import json

import click

from flux.cli import cli, get_http_client, get_server_url


@cli.group()
def service():
    """Manage workflow services."""
    pass


@service.command("create")
@click.argument("name")
@click.option("--namespace", "-n", multiple=True)
@click.option("--workflow", "-w", multiple=True)
@click.option("--exclude", "-e", multiple=True)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def create_service(name, namespace, workflow, exclude, format, server_url):
    """Create a new workflow service."""
    try:
        base_url = server_url or get_server_url()
        data = {
            "name": name,
            "namespaces": list(namespace),
            "workflows": list(workflow),
            "exclusions": list(exclude),
        }
        with get_http_client() as client:
            response = client.post(f"{base_url}/services", json=data)
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"Service '{name}' created.")
    except Exception as ex:
        click.echo(f"Error creating service: {str(ex)}", err=True)


@service.command("show")
@click.argument("name")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def show_service(name, format, server_url):
    """Show details of a workflow service."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.get(f"{base_url}/services/{name}")
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"Service: {result['name']}")
            if result.get("namespaces"):
                click.echo("Namespaces:")
                for ns in result["namespaces"]:
                    click.echo(f"  - {ns}")
            if result.get("workflows"):
                click.echo("Workflows:")
                for wf in result["workflows"]:
                    click.echo(f"  - {wf}")
            if result.get("exclusions"):
                click.echo("Exclusions:")
                for ex in result["exclusions"]:
                    click.echo(f"  - {ex}")
            if result.get("endpoints"):
                click.echo("Endpoints:")
                for ep in result["endpoints"]:
                    click.echo(f"  - {ep}")
    except Exception as ex:
        click.echo(f"Error showing service: {str(ex)}", err=True)


@service.command("list")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def list_services(format, server_url):
    """List all workflow services."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.get(f"{base_url}/services")
            response.raise_for_status()
            services = response.json()

        if not services:
            click.echo("No services found.")
            return

        if format == "json":
            click.echo(json.dumps(services, indent=2))
        else:
            for svc in services:
                ns_count = len(svc.get("namespaces", []))
                wf_count = len(svc.get("workflows", []))
                click.echo(f"- {svc['name']} ({ns_count} namespace(s), {wf_count} workflow(s))")
    except Exception as ex:
        click.echo(f"Error listing services: {str(ex)}", err=True)


@service.command("add")
@click.argument("name")
@click.option("--namespace", "-n", multiple=True)
@click.option("--workflow", "-w", multiple=True)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def add_to_service(name, namespace, workflow, format, server_url):
    """Add namespaces or workflows to a service."""
    try:
        base_url = server_url or get_server_url()
        data = {
            "add_namespaces": list(namespace),
            "add_workflows": list(workflow),
        }
        with get_http_client() as client:
            response = client.put(f"{base_url}/services/{name}", json=data)
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"Service '{name}' updated.")
    except Exception as ex:
        click.echo(f"Error updating service: {str(ex)}", err=True)


@service.command("remove")
@click.argument("name")
@click.option("--namespace", "-n", multiple=True)
@click.option("--workflow", "-w", multiple=True)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def remove_from_service(name, namespace, workflow, format, server_url):
    """Remove namespaces or workflows from a service."""
    try:
        base_url = server_url or get_server_url()
        data = {
            "remove_namespaces": list(namespace),
            "remove_workflows": list(workflow),
        }
        with get_http_client() as client:
            response = client.put(f"{base_url}/services/{name}", json=data)
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"Service '{name}' updated.")
    except Exception as ex:
        click.echo(f"Error updating service: {str(ex)}", err=True)


@service.command("exclude")
@click.argument("name")
@click.argument("workflow_refs", nargs=-1, required=True)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def exclude_from_service(name, workflow_refs, format, server_url):
    """Exclude workflows from a service."""
    try:
        base_url = server_url or get_server_url()
        data = {"add_exclusions": list(workflow_refs)}
        with get_http_client() as client:
            response = client.put(f"{base_url}/services/{name}", json=data)
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"Service '{name}' updated.")
    except Exception as ex:
        click.echo(f"Error updating service: {str(ex)}", err=True)


@service.command("include")
@click.argument("name")
@click.argument("workflow_refs", nargs=-1, required=True)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def include_in_service(name, workflow_refs, format, server_url):
    """Include previously excluded workflows in a service."""
    try:
        base_url = server_url or get_server_url()
        data = {"remove_exclusions": list(workflow_refs)}
        with get_http_client() as client:
            response = client.put(f"{base_url}/services/{name}", json=data)
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"Service '{name}' updated.")
    except Exception as ex:
        click.echo(f"Error updating service: {str(ex)}", err=True)


@service.command("delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def delete_service(name, yes, server_url):
    """Delete a workflow service."""
    try:
        if not yes:
            if not click.confirm(f"Are you sure you want to delete service '{name}'?"):
                click.echo("Operation cancelled.")
                return

        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.delete(f"{base_url}/services/{name}")
            response.raise_for_status()

        click.echo(f"Service '{name}' deleted.")
    except Exception as ex:
        click.echo(f"Error deleting service: {str(ex)}", err=True)
