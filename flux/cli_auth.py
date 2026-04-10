from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import click
import httpx

from flux.config import Configuration


CREDENTIALS_FILE = Path.home() / ".flux" / "credentials.json"


def get_server_url():
    settings = Configuration.get().settings
    return f"http://{settings.server_host}:{settings.server_port}"


def save_credentials(token_response: dict, issuer: str, client_id: str = "flux-api") -> None:
    """Save only the refresh token to ~/.flux/credentials.json with 0600 permissions.

    Zero-trust: access tokens are never persisted to disk. On each CLI invocation
    the access token is fetched fresh using the refresh token (held in memory only).
    """
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)

    refresh_token = token_response.get("refresh_token")
    if not refresh_token:
        raise ValueError(
            "OIDC token response did not include a refresh_token. "
            "Enable offline_access scope or configure the client to issue refresh tokens.",
        )

    credentials = {
        "refresh_token": refresh_token,
        "issuer": issuer,
        "client_id": client_id,
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


def fetch_access_token(credentials: dict) -> str | None:
    """Exchange a refresh token for a fresh access token.

    Returns the access token string on success. The access token is never persisted.
    If the refresh fails, the stored credentials file is left intact so the user
    can see the error and decide whether to re-login.
    """
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        return None

    issuer = credentials.get("issuer")
    if not issuer:
        return None

    client_id = credentials.get("client_id", "flux-api")

    try:
        discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        resp = httpx.get(discovery_url, timeout=10)
        resp.raise_for_status()
        token_endpoint = resp.json()["token_endpoint"]

        resp = httpx.post(
            token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None

        token_data = resp.json()

        new_refresh_token = token_data.get("refresh_token")
        if new_refresh_token and new_refresh_token != refresh_token:
            updated = {**credentials, "refresh_token": new_refresh_token}
            CREDENTIALS_FILE.write_text(json.dumps(updated, indent=2))
            os.chmod(CREDENTIALS_FILE, stat.S_IRUSR | stat.S_IWUSR)

        return token_data.get("access_token")
    except Exception:
        return None


def get_auth_headers() -> dict:
    """Get auth headers from env var or refresh-token-derived access token.

    Zero-trust: never returns a persisted access token. Always fetches a fresh
    access token via refresh token exchange.
    """
    token = os.environ.get("FLUX_AUTH_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}

    credentials = load_credentials()
    if credentials:
        access_token = fetch_access_token(credentials)
        if access_token:
            return {"Authorization": f"Bearer {access_token}"}

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
        click.echo("Logged in via OIDC (refresh token stored, access tokens in-memory only)")
        click.echo(f"  Issuer: {credentials.get('issuer', 'unknown')}")
        click.echo(f"  Client: {credentials.get('client_id', 'unknown')}")
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
            data={
                "client_id": client_id,
                "scope": "openid profile email offline_access",
            },
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
                try:
                    save_credentials(token_data, issuer, client_id)
                except ValueError as err:
                    click.echo(f"Authentication succeeded but cannot persist: {err}")
                    return
                click.echo("Authentication successful!")
                click.echo(
                    "Refresh token stored at ~/.flux/credentials.json (0600). "
                    "Access tokens are fetched per-invocation and never persisted.",
                )
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
        if not data.get("valid"):
            click.echo(f"Error: {data.get('error', 'Invalid token')}")
            return
        click.echo(f"Subject: {data.get('subject')}")
        click.echo(f"Roles: {', '.join(data.get('roles', []))}")
        click.echo("Effective Permissions:")
        for perm in data.get("permissions", []):
            click.echo(f"  - {perm}")
    else:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        click.echo(f"Error: {detail}")


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
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        click.echo(f"Error: {detail}")
        return
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


# --- Principals group ---


@click.group()
def principals():
    """Principal management commands."""
    pass


@principals.command("list")
@click.option(
    "--type",
    "principal_type",
    type=click.Choice(["user", "service_account"]),
    default=None,
)
@click.option("--format", "-f", "fmt", type=click.Choice(["simple", "json"]), default="simple")
def principals_list(principal_type, fmt):
    """List all principals."""
    url = get_server_url()
    params = {}
    if principal_type:
        params["type"] = principal_type
    resp = httpx.get(f"{url}/admin/principals", headers=get_auth_headers(), params=params)
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        click.echo(f"Error: {detail}")
        return
    data = resp.json()
    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
    else:
        for p in data:
            enabled = "" if p.get("enabled", True) else " (disabled)"
            roles = ", ".join(p.get("roles", []))
            click.echo(f"  [{p['type']}] {p['subject']}{enabled} roles: {roles}")


@principals.command("show")
@click.argument("subject")
@click.option("--type", "principal_type", default=None)
@click.option("--issuer", default=None)
def principals_show(subject, principal_type, issuer):
    """Show principal details. Tries OIDC issuer first, then 'flux'."""
    url = get_server_url()
    params = {"subject": subject}
    if issuer:
        params["issuer"] = issuer
    elif principal_type:
        params["type"] = principal_type
    resp = httpx.get(f"{url}/admin/principals/lookup", headers=get_auth_headers(), params=params)
    if resp.status_code == 404:
        click.echo(f"Principal '{subject}' not found.")
        return
    if resp.status_code != 200:
        click.echo(f"Error: {resp.text}")
        return
    p = resp.json()
    click.echo(f"Subject:  {p['subject']}")
    click.echo(f"Type:     {p['type']}")
    click.echo(f"Issuer:   {p.get('external_issuer', 'flux')}")
    click.echo(f"Enabled:  {p.get('enabled', True)}")
    if p.get("display_name"):
        click.echo(f"Name:     {p['display_name']}")
    click.echo(f"Roles:    {', '.join(p.get('roles', []))}")


@principals.command("create")
@click.argument("subject")
@click.option(
    "--type",
    "principal_type",
    required=True,
    type=click.Choice(["user", "service_account"]),
)
@click.option("--role", "roles", multiple=True)
@click.option("--issuer", default=None)
@click.option("--display-name", default=None)
def principals_create(subject, principal_type, roles, issuer, display_name):
    """Create a principal."""
    url = get_server_url()
    body = {"subject": subject, "type": principal_type}
    if roles:
        body["roles"] = list(roles)
    if issuer:
        body["external_issuer"] = issuer
    if display_name:
        body["display_name"] = display_name
    resp = httpx.post(f"{url}/admin/principals", json=body, headers=get_auth_headers())
    if resp.status_code == 200:
        p = resp.json()
        click.echo(f"Principal '{subject}' created (id: {p.get('id', '?')}).")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@principals.command("grant")
@click.argument("subject")
@click.option("--role", required=True)
def principals_grant(subject, role):
    """Grant a role to a principal."""
    url = get_server_url()
    resp = httpx.post(
        f"{url}/admin/principals/lookup/roles",
        json={"subject": subject, "role": role},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Role '{role}' granted to '{subject}'.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@principals.command("revoke")
@click.argument("subject")
@click.option("--role", required=True)
def principals_revoke(subject, role):
    """Revoke a role from a principal."""
    url = get_server_url()
    resp = httpx.delete(
        f"{url}/admin/principals/lookup/roles/{role}",
        params={"subject": subject},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Role '{role}' revoked from '{subject}'.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@principals.command("enable")
@click.argument("subject")
def principals_enable(subject):
    """Enable a principal."""
    url = get_server_url()
    resp = httpx.post(
        f"{url}/admin/principals/lookup/enable",
        json={"subject": subject},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Principal '{subject}' enabled.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@principals.command("disable")
@click.argument("subject")
def principals_disable(subject):
    """Disable a principal."""
    url = get_server_url()
    resp = httpx.post(
        f"{url}/admin/principals/lookup/disable",
        json={"subject": subject},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Principal '{subject}' disabled.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@principals.command("delete")
@click.argument("subject")
@click.option("--force", is_flag=True, help="Force deletion including cascade effects")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def principals_delete(subject, force, yes):
    """Delete a principal."""
    url = get_server_url()
    if force and not yes:
        click.echo(
            f"Deleting '{subject}' with --force will cascade-delete all API keys and role assignments.",
        )
        if not click.confirm("Proceed?"):
            click.echo("Cancelled.")
            return
    resp = httpx.delete(
        f"{url}/admin/principals/lookup",
        params={"subject": subject, "force": force},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"Principal '{subject}' deleted.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@principals.command("create-key")
@click.argument("subject")
@click.option("--key-name", "-k", required=True)
@click.option("--expires", "-e", default=None, help="Expiry in days, e.g. '90d'")
def principals_create_key(subject, key_name, expires):
    """Create an API key for a service account principal."""
    url = get_server_url()
    body = {"name": key_name, "subject": subject}
    if expires:
        body["expires_in_days"] = int(expires.rstrip("d"))
    resp = httpx.post(
        f"{url}/admin/principals/lookup/keys",
        json=body,
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        key = resp.json().get("key")
        click.echo(f"API key created: {key}")
        click.echo("Store this key securely — it will not be shown again.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")


@principals.command("list-keys")
@click.argument("subject")
def principals_list_keys(subject):
    """List API keys for a principal."""
    url = get_server_url()
    resp = httpx.get(
        f"{url}/admin/principals/lookup/keys",
        params={"subject": subject},
        headers=get_auth_headers(),
    )
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        click.echo(f"Error: {detail}")
        return
    data = resp.json()
    for key in data:
        expires = key.get("expires_at", "never")
        click.echo(f"  {key['name']} ({key['key_prefix']}...) expires: {expires}")


@principals.command("revoke-key")
@click.argument("subject")
@click.option("--key-name", "-k", required=True)
def principals_revoke_key(subject, key_name):
    """Revoke an API key from a principal."""
    url = get_server_url()
    resp = httpx.delete(
        f"{url}/admin/principals/lookup/keys/{key_name}",
        params={"subject": subject},
        headers=get_auth_headers(),
    )
    if resp.status_code == 200:
        click.echo(f"API key '{key_name}' revoked.")
    else:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")
