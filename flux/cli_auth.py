from __future__ import annotations

import json
import os
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import httpx

from flux.config import Configuration


CREDENTIALS_FILE = Path.home() / ".flux" / "credentials.json"


def get_server_url():
    settings = Configuration.get().settings
    return f"http://{settings.server_host}:{settings.server_port}"


def save_credentials(token_response: dict, issuer: str) -> None:
    """Save OIDC tokens to ~/.flux/credentials.json with 0600 permissions."""
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)

    expires_in = token_response.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    credentials = {
        "access_token": token_response["access_token"],
        "refresh_token": token_response.get("refresh_token"),
        "expires_at": expires_at.isoformat(),
        "token_type": token_response.get("token_type", "Bearer"),
        "issuer": issuer,
    }

    CREDENTIALS_FILE.write_text(json.dumps(credentials, indent=2))
    os.chmod(CREDENTIALS_FILE, stat.S_IRUSR | stat.S_IWUSR)


def load_credentials() -> dict | None:
    """Load credentials from file, return None if missing or invalid."""
    if not CREDENTIALS_FILE.exists():
        return None
    try:
        return json.loads(CREDENTIALS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def refresh_access_token(credentials: dict) -> dict | None:
    """Refresh the access token using the refresh token. Returns new credentials or None."""
    if not credentials.get("refresh_token"):
        return None

    issuer = credentials.get("issuer")
    if not issuer:
        return None

    try:
        discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        resp = httpx.get(discovery_url, timeout=10)
        resp.raise_for_status()
        token_endpoint = resp.json()["token_endpoint"]

        for client_id in ["flux-api", "flux-cli"]:
            resp = httpx.post(
                token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": credentials["refresh_token"],
                    "client_id": client_id,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                token_data = resp.json()
                save_credentials(token_data, issuer)
                return load_credentials()
        return None
    except Exception:
        return None


def get_auth_headers() -> dict:
    """Get auth headers from env var or stored credentials."""
    token = os.environ.get("FLUX_AUTH_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}

    credentials = load_credentials()
    if credentials:
        try:
            expires_at = datetime.fromisoformat(credentials["expires_at"])
            if expires_at <= datetime.now(timezone.utc) + timedelta(seconds=30):
                refreshed = refresh_access_token(credentials)
                if refreshed:
                    credentials = refreshed
        except (KeyError, ValueError):
            pass

        return {
            "Authorization": f"{credentials.get('token_type', 'Bearer')} {credentials['access_token']}",
        }

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

    credentials = load_credentials()
    if credentials:
        click.echo("Logged in via OIDC")
        click.echo(f"  Issuer: {credentials.get('issuer', 'unknown')}")
        click.echo(f"  Expires: {credentials.get('expires_at', 'unknown')}")
    else:
        click.echo("Using FLUX_AUTH_TOKEN from environment")
    click.echo(f"  Server: {url}")


@auth.command("login")
@click.option("--issuer", default=None, help="OIDC issuer URL (defaults to config)")
@click.option("--client-id", default="flux-api", help="OIDC client ID")
def auth_login(issuer, client_id):
    """Authenticate via OIDC Device Authorization Grant."""
    if issuer is None:
        oidc_config = Configuration.get().settings.security.auth.oidc
        if not oidc_config.enabled or not oidc_config.issuer:
            click.echo("Error: OIDC not configured. Pass --issuer or enable OIDC in flux.toml.")
            return
        issuer = oidc_config.issuer

    discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        resp = httpx.get(discovery_url, timeout=10)
        resp.raise_for_status()
        discovery = resp.json()
    except Exception as e:
        click.echo(f"Error fetching OIDC discovery: {e}")
        return

    device_auth_endpoint = discovery.get("device_authorization_endpoint")
    token_endpoint = discovery.get("token_endpoint")

    if not device_auth_endpoint:
        click.echo("Error: IdP does not support Device Authorization Grant.")
        return

    try:
        resp = httpx.post(
            device_auth_endpoint,
            data={"client_id": client_id, "scope": "openid profile email"},
            timeout=10,
        )
        resp.raise_for_status()
        device_data = resp.json()
    except Exception as e:
        click.echo(f"Error requesting device code: {e}")
        return

    device_code = device_data["device_code"]
    user_code = device_data["user_code"]
    verification_uri = device_data.get("verification_uri", device_data.get("verification_url"))
    verification_uri_complete = device_data.get("verification_uri_complete")
    interval = device_data.get("interval", 5)

    click.echo("\nTo authenticate, visit:")
    click.echo(f"  {verification_uri_complete or verification_uri}")
    click.echo(f"\nAnd enter this code: {user_code}\n")
    click.echo("Waiting for authentication...")

    start = time.time()
    timeout = device_data.get("expires_in", 600)
    while time.time() - start < timeout:
        time.sleep(interval)
        try:
            resp = httpx.post(
                token_endpoint,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": client_id,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                token_data = resp.json()
                save_credentials(token_data, issuer)
                click.echo("Authentication successful!")
                return
            elif resp.status_code == 400:
                error = resp.json().get("error", "")
                if error == "authorization_pending":
                    continue
                elif error == "slow_down":
                    interval += 5
                    continue
                else:
                    click.echo(f"Authentication failed: {error}")
                    return
        except Exception as e:
            click.echo(f"Polling error: {e}")
            return

    click.echo("Authentication timed out.")


@auth.command("logout")
def auth_logout():
    """Clear stored credentials."""
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()
    old_creds = Path.home() / ".flux" / "credentials"
    if old_creds.exists():
        old_creds.unlink()
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
        json={"new_name": name},
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
        body["expires_in_days"] = int(expires.rstrip("d"))
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
