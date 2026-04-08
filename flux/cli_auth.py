from __future__ import annotations

import json
import os
from pathlib import Path

import click
import httpx

from flux.config import Configuration


def get_server_url():
    settings = Configuration.get().settings
    return f"http://{settings.server_host}:{settings.server_port}"


def get_auth_headers() -> dict:
    token = os.environ.get("FLUX_AUTH_TOKEN")
    if not token:
        creds_path = Path.home() / ".flux" / "credentials"
        if creds_path.exists():
            token = creds_path.read_text().strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


# --- Auth group ---


@click.group()
def auth():
    """Authentication commands."""
    pass


@auth.command("status")
def auth_status():
    """Show current authentication status."""
    url = get_server_url()
    headers = get_auth_headers()
    if not headers:
        click.echo("Not logged in. Run 'flux auth login' or set FLUX_AUTH_TOKEN.")
        return
    try:
        httpx.get(f"{url}/health", headers=headers)
        click.echo(f"Authenticated. Server: {url}")
    except Exception as e:
        click.echo(f"Error connecting to server: {e}")


@auth.command("login")
def auth_login():
    """Login via Device Authorization Grant."""
    click.echo("Device Authorization Grant flow not yet implemented.")
    click.echo("Set FLUX_AUTH_TOKEN environment variable as a workaround.")


@auth.command("logout")
def auth_logout():
    """Clear stored credentials."""
    creds_path = Path.home() / ".flux" / "credentials"
    if creds_path.exists():
        creds_path.unlink()
    click.echo("Logged out.")


@auth.command("test-token")
@click.argument("token")
def auth_test_token(token):
    """Decode a JWT token and show resolved identity and permissions."""
    url = get_server_url()
    resp = httpx.post(
        f"{url}/auth/test-token",
        json={"token": token},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        data = resp.json()
        click.echo(f"Subject: {data.get('subject')}")
        click.echo(f"Roles: {', '.join(data.get('roles', []))}")
        click.echo("Effective Permissions:")
        for perm in data.get("permissions", []):
            click.echo(f"  - {perm}")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@auth.command("permissions")
@click.option("--workflow", "-w", default=None, help="Filter by workflow name")
@click.option("--format", "-f", "fmt", type=click.Choice(["simple", "json"]), default="simple")
def auth_permissions(workflow, fmt):
    """List auto-derived permissions from registered workflows."""
    url = get_server_url()
    headers = get_auth_headers()
    params = {}
    if workflow:
        params["workflow"] = workflow
    resp = httpx.get(f"{url}/auth/permissions", headers=headers, params=params)
    if resp.status_code != 200:
        click.echo(f"Error: {resp.text}")
        return
    data = resp.json()
    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
    else:
        if isinstance(data, dict):
            for wf_name, perms in data.items():
                click.echo(f"\n{wf_name}:")
                for perm in perms:
                    click.echo(f"  - {perm}")
        elif isinstance(data, list):
            for perm in data:
                click.echo(f"  - {perm}")


# --- Roles group ---


@click.group()
def roles():
    """Role management commands."""
    pass


@roles.command("list")
@click.option("--format", "-f", "fmt", type=click.Choice(["simple", "json"]), default="simple")
def roles_list(fmt):
    """List all roles."""
    url = get_server_url()
    resp = httpx.get(f"{url}/admin/roles", headers=get_auth_headers())
    data = resp.json()
    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
    else:
        for role in data:
            marker = " (built-in)" if role.get("built_in") else ""
            click.echo(f"  {role['name']}{marker}")


@roles.command("show")
@click.argument("name")
def roles_show(name):
    """Show role details."""
    url = get_server_url()
    resp = httpx.get(f"{url}/admin/roles/{name}", headers=get_auth_headers())
    if resp.status_code == 404:
        click.echo(f"Role '{name}' not found.")
        return
    role = resp.json()
    click.echo(f"Name: {role['name']}")
    click.echo(f"Built-in: {role.get('built_in', False)}")
    click.echo("Permissions:")
    for perm in role.get("permissions", []):
        click.echo(f"  - {perm}")


@roles.command("create")
@click.argument("name")
@click.option("--permissions", "-p", multiple=True, required=True)
def roles_create(name, permissions):
    """Create a custom role."""
    url = get_server_url()
    resp = httpx.post(
        f"{url}/admin/roles",
        json={"name": name, "permissions": list(permissions)},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Role '{name}' created.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@roles.command("clone")
@click.argument("source")
@click.option("--name", "-n", required=True)
def roles_clone(source, name):
    """Clone an existing role."""
    url = get_server_url()
    resp = httpx.post(
        f"{url}/admin/roles/{source}/clone",
        json={"name": name},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Role '{source}' cloned as '{name}'.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@roles.command("update")
@click.argument("name")
@click.option("--add-permissions", "-a", multiple=True)
@click.option("--remove-permissions", "-r", multiple=True)
def roles_update(name, add_permissions, remove_permissions):
    """Update a role's permissions."""
    url = get_server_url()
    body = {}
    if add_permissions:
        body["add_permissions"] = list(add_permissions)
    if remove_permissions:
        body["remove_permissions"] = list(remove_permissions)
    resp = httpx.patch(
        f"{url}/admin/roles/{name}",
        json=body,
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Role '{name}' updated.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@roles.command("delete")
@click.argument("name")
def roles_delete(name):
    """Delete a custom role."""
    url = get_server_url()
    resp = httpx.delete(
        f"{url}/admin/roles/{name}",
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Role '{name}' deleted.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


# --- Service Accounts group ---


@click.group("service-accounts")
def service_accounts():
    """Service account management commands."""
    pass


@service_accounts.command("list")
@click.option("--format", "-f", "fmt", type=click.Choice(["simple", "json"]), default="simple")
def sa_list(fmt):
    """List all service accounts."""
    url = get_server_url()
    resp = httpx.get(
        f"{url}/admin/service-accounts",
        headers=get_auth_headers(),
    )
    data = resp.json()
    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
    else:
        for sa in data:
            click.echo(f"  {sa['name']} (roles: {', '.join(sa.get('roles', []))})")


@service_accounts.command("show")
@click.argument("name")
def sa_show(name):
    """Show service account details."""
    url = get_server_url()
    resp = httpx.get(
        f"{url}/admin/service-accounts/{name}",
        headers=get_auth_headers(),
    )
    if resp.status_code == 404:
        click.echo(f"Service account '{name}' not found.")
        return
    sa = resp.json()
    click.echo(f"Name: {sa['name']}")
    click.echo(f"Roles: {', '.join(sa.get('roles', []))}")


@service_accounts.command("create")
@click.argument("name")
@click.option("--roles", "-r", multiple=True, required=True)
def sa_create(name, roles):
    """Create a service account."""
    url = get_server_url()
    resp = httpx.post(
        f"{url}/admin/service-accounts",
        json={"name": name, "roles": list(roles)},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Service account '{name}' created.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@service_accounts.command("update")
@click.argument("name")
@click.option("--add-roles", "-a", multiple=True)
@click.option("--remove-roles", "-r", multiple=True)
def sa_update(name, add_roles, remove_roles):
    """Update service account roles."""
    url = get_server_url()
    body = {}
    if add_roles:
        body["add_roles"] = list(add_roles)
    if remove_roles:
        body["remove_roles"] = list(remove_roles)
    resp = httpx.patch(
        f"{url}/admin/service-accounts/{name}",
        json=body,
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Service account '{name}' updated.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@service_accounts.command("delete")
@click.argument("name")
def sa_delete(name):
    """Delete a service account."""
    url = get_server_url()
    resp = httpx.delete(
        f"{url}/admin/service-accounts/{name}",
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Service account '{name}' deleted.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@service_accounts.command("create-key")
@click.argument("name")
@click.option("--key-name", "-k", required=True)
@click.option("--expires", "-e", default=None, help="Expiry in days, e.g. '90d'")
def sa_create_key(name, key_name, expires):
    """Create an API key for a service account."""
    url = get_server_url()
    body = {"name": key_name}
    if expires:
        body["expires_days"] = int(expires.rstrip("d"))
    resp = httpx.post(
        f"{url}/admin/service-accounts/{name}/keys",
        json=body,
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        key = resp.json().get("key")
        click.echo(f"API key created: {key}")
        click.echo("Store this key securely — it will not be shown again.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@service_accounts.command("list-keys")
@click.argument("name")
def sa_list_keys(name):
    """List API keys for a service account."""
    url = get_server_url()
    resp = httpx.get(
        f"{url}/admin/service-accounts/{name}/keys",
        headers=get_auth_headers(),
    )
    data = resp.json()
    for key in data:
        expires = key.get("expires_at", "never")
        click.echo(f"  {key['name']} ({key['key_prefix']}...) expires: {expires}")


@service_accounts.command("revoke-key")
@click.argument("name")
@click.option("--key-name", "-k", required=True)
def sa_revoke_key(name, key_name):
    """Revoke an API key."""
    url = get_server_url()
    resp = httpx.delete(
        f"{url}/admin/service-accounts/{name}/keys/{key_name}",
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"API key '{key_name}' revoked.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")
