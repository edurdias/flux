from __future__ import annotations

import json
from typing import Any

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
@click.option("--mcp", is_flag=True, default=False, help="Enable MCP endpoint.")
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def create_service(name, namespace, workflow, exclude, format, mcp, server_url):
    """Create a new workflow service."""
    try:
        base_url = server_url or get_server_url()
        data = {
            "name": name,
            "namespaces": list(namespace),
            "workflows": list(workflow),
            "exclusions": list(exclude),
            "mcp_enabled": mcp,
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


@service.command("update")
@click.argument("name")
@click.option("--mcp/--no-mcp", default=None, help="Enable or disable MCP endpoint.")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def update_service(name, mcp, format, server_url):
    """Update service-level settings."""
    try:
        base_url = server_url or get_server_url()
        data = {}
        if mcp is not None:
            data["mcp_enabled"] = mcp
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
            click.echo(f"MCP: {'enabled' if result.get('mcp_enabled') else 'disabled'}")
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
            if format == "json":
                click.echo("[]")
            else:
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


@service.command("start")
@click.argument("name")
@click.option("--port", "-p", default=9000, type=int, help="Port to listen on.")
@click.option("--host", default="0.0.0.0", help="Host to bind to.")
@click.option("--mcp/--no-mcp", default=None, help="Enable or disable MCP endpoint.")
@click.option("--server-url", "-cp-url", default=None, help="Flux server URL to connect to.")
@click.option("--cache-ttl", default=60, type=int, help="Endpoint cache TTL in seconds.")
@click.option(
    "--mcp-issuer",
    default=None,
    help="IdP issuer URL for MCP auth (enables token validation and OAuth discovery).",
)
@click.option("--mcp-audience", default=None, help="Expected JWT audience for MCP auth.")
@click.option(
    "--mcp-jwks-uri",
    default=None,
    help="JWKS URI for MCP token validation (discovered from issuer OIDC metadata if omitted).",
)
@click.option(
    "--mcp-base-url",
    default=None,
    help="Public base URL for OAuth discovery metadata (overrides auto-constructed URL, useful behind TLS termination).",
)
def start_service(
    name,
    port,
    host,
    mcp,
    server_url,
    cache_ttl,
    mcp_issuer,
    mcp_audience,
    mcp_jwks_uri,
    mcp_base_url,
):
    """Start a standalone service proxy."""
    from flux.service_proxy import create_standalone_app

    import uvicorn

    flux_url = server_url or get_server_url()

    enable_mcp = mcp
    if enable_mcp is None:
        try:
            with get_http_client() as client:
                response = client.get(f"{flux_url}/services/{name}")
                response.raise_for_status()
                svc_info = response.json()
                enable_mcp = svc_info.get("mcp_enabled", False)
        except Exception:
            enable_mcp = False

    mcp_auth = None
    resolved_issuer = None
    has_auth_flags = any([mcp_issuer, mcp_audience, mcp_jwks_uri])
    if enable_mcp:
        mcp_auth, resolved_issuer = _build_mcp_auth(
            host=host,
            port=port,
            issuer=mcp_issuer,
            audience=mcp_audience,
            jwks_uri=mcp_jwks_uri,
            base_url=mcp_base_url,
        )
    elif has_auth_flags:
        click.echo(
            "Warning: --mcp-issuer/--mcp-audience/--mcp-jwks-uri ignored because MCP is disabled.",
            err=True,
        )

    click.echo(f"Starting service '{name}' on {host}:{port}")
    click.echo(f"Flux server: {flux_url}")
    click.echo(f"MCP: {'enabled' if enable_mcp else 'disabled'}")
    if mcp_auth:
        click.echo(f"MCP auth: {resolved_issuer}")
    app = create_standalone_app(
        name,
        flux_url,
        cache_ttl,
        enable_mcp=enable_mcp,
        mcp_auth=mcp_auth,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


def _build_mcp_auth(
    host: str,
    port: int,
    issuer: str | None = None,
    audience: str | None = None,
    jwks_uri: str | None = None,
    base_url: str | None = None,
) -> tuple[Any, str | None]:
    """Build a FastMCP auth provider from explicit flags or Flux OIDC config.

    Returns ``(provider, resolved_issuer)`` or ``(None, None)``.
    """
    if not issuer:
        try:
            from flux.config import FluxConfig

            cfg = FluxConfig()
            oidc = cfg.security.auth.oidc
            if oidc.enabled and oidc.issuer:
                issuer = oidc.issuer
                audience = audience or oidc.audience or None
            else:
                return None, None
        except Exception:
            return None, None

    if not jwks_uri:
        jwks_uri = _discover_jwks_uri(issuer)

    from fastmcp.server.auth import JWTVerifier, RemoteAuthProvider

    if not base_url:
        if host in ("0.0.0.0", "::"):
            base_url = f"http://localhost:{port}"
        else:
            base_url = f"http://{host}:{port}"

    verifier = JWTVerifier(
        jwks_uri=jwks_uri,
        issuer=issuer,
        audience=audience,
    )
    provider = RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[issuer],
        base_url=base_url,
    )
    return provider, issuer


def _discover_jwks_uri(issuer: str) -> str:
    """Discover the JWKS URI from the issuer's OpenID Connect metadata.

    Falls back to ``{issuer}/.well-known/jwks.json`` if discovery fails.
    """
    discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        with get_http_client() as client:
            response = client.get(discovery_url)
            response.raise_for_status()
            metadata = response.json()
        discovered = metadata.get("jwks_uri")
        if isinstance(discovered, str) and discovered:
            return discovered
        click.echo(
            f"Warning: OIDC discovery at '{discovery_url}' did not return a valid 'jwks_uri'. "
            f"Falling back to legacy JWKS URL.",
            err=True,
        )
    except Exception as ex:
        click.echo(
            f"Warning: OIDC discovery failed for '{discovery_url}': {ex}. "
            f"Falling back to legacy JWKS URL.",
            err=True,
        )
    return f"{issuer.rstrip('/')}/.well-known/jwks.json"


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
