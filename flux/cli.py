from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote
from uuid import uuid4


import importlib
import types

import click

if TYPE_CHECKING:
    import httpx
else:

    class _LazyModule(types.ModuleType):
        """Proxy that defers the actual import until first attribute access.

        Resolved attributes are cached on the instance so that
        ``unittest.mock.patch`` (which uses setattr/delattr) can override them.
        """

        def __getattr__(self, name: str):
            real = importlib.import_module(self.__name__)
            val = getattr(real, name)
            object.__setattr__(self, name, val)
            return val

    httpx = _LazyModule("httpx")


@click.group()
def cli():
    pass


@cli.group()
def workflow():
    pass


def get_server_url():
    """Get the server URL from configuration."""
    from flux.config import Configuration

    settings = Configuration.get().settings
    return f"http://{settings.server_host}:{settings.server_port}"


def get_http_client(timeout: float = 30.0) -> httpx.Client:
    """Create an HTTP client with auth headers from the current CLI credentials.

    Auth headers come from ``FLUX_AUTH_TOKEN`` if set, otherwise from a fresh
    access token exchanged from the stored OIDC refresh token. See
    ``cli_auth.get_auth_headers`` for details.
    """
    from flux.cli_auth import get_auth_headers

    return httpx.Client(timeout=timeout, headers=get_auth_headers())


@workflow.command("list")
@click.option(
    "--namespace",
    "-n",
    default=None,
    help="Filter by namespace",
)
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
def list_workflows(namespace: str | None, format: str, server_url: str | None):
    """List all registered workflows."""
    try:
        base_url = server_url or get_server_url()
        params = {"namespace": namespace} if namespace else None

        with get_http_client() as client:
            response = client.get(f"{base_url}/workflows", params=params)
            response.raise_for_status()
            workflows = response.json()

        if not workflows:
            click.echo("No workflows found.")
            return

        if format == "json":
            click.echo(json.dumps(workflows, indent=2))
        else:
            for wf in workflows:
                click.echo(f"- {wf['namespace']}/{wf['name']} (version {wf['version']})")
    except Exception as ex:
        click.echo(f"Error listing workflows: {str(ex)}", err=True)


@workflow.command("list-namespaces")
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
def list_namespaces(format: str, server_url: str | None):
    """List all namespaces with workflow counts."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.get(f"{base_url}/namespaces")
            response.raise_for_status()
            namespaces = response.json()

        if not namespaces:
            click.echo("No namespaces found.")
            return

        if format == "json":
            click.echo(json.dumps(namespaces, indent=2))
        else:
            for ns in namespaces:
                click.echo(f"- {ns['namespace']} ({ns['workflow_count']} workflow(s))")
    except Exception as ex:
        click.echo(f"Error listing namespaces: {str(ex)}", err=True)


@workflow.command("register")
@click.argument("filename")
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
def register_workflows(filename: str, format: str, server_url: str | None):
    """Register workflows from a file."""
    try:
        file_path = Path(filename)
        if not file_path.exists():
            raise ValueError(f"File '{filename}' not found.")

        base_url = server_url or get_server_url()

        with get_http_client() as client:
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, "text/x-python")}
                response = client.post(f"{base_url}/workflows", files=files)
                response.raise_for_status()
                result = response.json()

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"Successfully registered {len(result)} workflow(s) from '{filename}'.")
            for workflow in result:
                click.echo(f"  - {workflow['name']} (version {workflow['version']})")

    except Exception as ex:
        click.echo(f"Error registering workflow: {str(ex)}", err=True)


@workflow.command("show")
@click.argument("workflow_name")
@click.option(
    "--version",
    "-v",
    type=int,
    default=None,
    help="Specific workflow version to show (defaults to latest)",
)
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
def show_workflow(workflow_name: str, version: int | None, format: str, server_url: str | None):
    """Show the details of a registered workflow."""
    from flux.catalogs import resolve_workflow_ref
    from flux.utils import to_json

    try:
        base_url = server_url or get_server_url()
        namespace, name = resolve_workflow_ref(workflow_name)

        with get_http_client() as client:
            if version is not None:
                url = f"{base_url}/workflows/{namespace}/{name}/versions/{version}"
            else:
                url = f"{base_url}/workflows/{namespace}/{name}"
            response = client.get(url)
            response.raise_for_status()
            workflow = response.json()

        if format == "json":
            click.echo(to_json(workflow))
        else:
            click.echo(f"\nWorkflow: {workflow['name']}")
            click.echo(f"Version: {workflow['version']}")
            if "description" in workflow:
                click.echo(f"Description: {workflow['description']}")
            click.echo("\nDetails:")
            click.echo("-" * 50)
            click.echo(to_json(workflow))

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            version_str = f" version {version}" if version else ""
            click.echo(f"Workflow '{workflow_name}'{version_str} not found.", err=True)
        else:
            click.echo(f"Error showing workflow: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error showing workflow: {str(ex)}", err=True)


@workflow.command("delete")
@click.argument("workflow_name")
@click.option(
    "--version",
    "-v",
    type=int,
    default=None,
    help="Specific version to delete (defaults to all versions)",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.option(
    "--format",
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
def delete_workflow(
    workflow_name: str,
    version: int | None,
    force: bool,
    format: str,
    server_url: str | None,
):
    """Delete a workflow (all versions or a specific version)."""
    try:
        from flux.catalogs import resolve_workflow_ref

        base_url = server_url or get_server_url()
        namespace, name = resolve_workflow_ref(workflow_name)

        version_str = f" version {version}" if version else " (all versions)"
        if not force:
            if not click.confirm(f"Delete workflow '{workflow_name}'{version_str}?"):
                click.echo("Cancelled.")
                return

        params = {}
        if version is not None:
            params["version"] = version

        with get_http_client() as client:
            response = client.delete(
                f"{base_url}/workflows/{namespace}/{name}",
                params=params,
            )
            response.raise_for_status()

        if format == "json":
            click.echo(json.dumps({"status": "deleted", "workflow": workflow_name}, indent=2))
        else:
            click.echo(f"Successfully deleted workflow '{workflow_name}'{version_str}.")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Workflow '{workflow_name}' not found.", err=True)
        else:
            click.echo(f"Error deleting workflow: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error deleting workflow: {str(ex)}", err=True)


@workflow.command("versions")
@click.argument("workflow_name")
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
def list_workflow_versions(
    workflow_name: str,
    format: str,
    server_url: str | None,
):
    """List all versions of a workflow."""
    try:
        from flux.catalogs import resolve_workflow_ref

        base_url = server_url or get_server_url()
        namespace, name = resolve_workflow_ref(workflow_name)

        with get_http_client() as client:
            response = client.get(f"{base_url}/workflows/{namespace}/{name}/versions")
            response.raise_for_status()
            versions = response.json()

        if not versions:
            click.echo(f"No versions found for workflow '{workflow_name}'.")
            return

        if format == "json":
            click.echo(json.dumps(versions, indent=2))
        else:
            click.echo(f"\nVersions of '{workflow_name}':")
            click.echo("-" * 40)
            for v in versions:
                click.echo(f"  Version {v['version']} (id: {v['id']})")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Workflow '{workflow_name}' not found.", err=True)
        else:
            click.echo(f"Error listing versions: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error listing versions: {str(ex)}", err=True)


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
    "--version",
    "-v",
    type=int,
    default=None,
    help="Specific workflow version to run (defaults to latest)",
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
    version: int | None,
    detailed: bool,
    server_url: str | None,
):
    """Run the specified workflow."""
    try:
        from flux.catalogs import resolve_workflow_ref
        from flux.utils import parse_value, to_json

        base_url = server_url or get_server_url()
        namespace, name = resolve_workflow_ref(workflow_name)
        parsed_input = parse_value(input)

        params: dict[str, Any] = {"detailed": detailed}
        if version is not None:
            params["version"] = version

        with get_http_client(timeout=60.0) as client:
            response = client.post(
                f"{base_url}/workflows/{namespace}/{name}/run/{mode}",
                json=parsed_input,
                params=params,
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
        from flux.catalogs import resolve_workflow_ref
        from flux.utils import parse_value, to_json

        base_url = server_url or get_server_url()
        namespace, name = resolve_workflow_ref(workflow_name)
        parsed_input = parse_value(input)

        with get_http_client(timeout=60.0) as client:
            response = client.post(
                f"{base_url}/workflows/{namespace}/{name}/resume/{execution_id}/{mode}",
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
        from flux.catalogs import resolve_workflow_ref
        from flux.utils import to_json

        base_url = server_url or get_server_url()
        namespace, name = resolve_workflow_ref(workflow_name)

        with get_http_client() as client:
            response = client.get(
                f"{base_url}/workflows/{namespace}/{name}/status/{execution_id}",
                params={"detailed": detailed},
            )
            response.raise_for_status()
            result = response.json()

            pending = _fetch_pending_approvals(client, base_url, execution_id)

        click.echo(to_json(result))
        if pending:
            click.echo(f"Blocked on {len(pending)} approval(s)", err=True)

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


@workflow.command("cancel")
@click.argument("workflow_name")
@click.argument("execution_id")
@click.option("--server-url", "-cp-url", default=None, help="Server URL to connect to.")
def cancel_workflow(workflow_name: str, execution_id: str, server_url: str | None):
    """Cancel a running workflow execution."""
    try:
        from flux.catalogs import resolve_workflow_ref
        from flux.utils import to_json

        namespace, name = resolve_workflow_ref(workflow_name)
        base_url = server_url or get_server_url()

        with get_http_client() as client:
            response = client.get(
                f"{base_url}/workflows/{namespace}/{name}/cancel/{execution_id}",
            )
            response.raise_for_status()
            result = response.json()

        click.echo(to_json(result))

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Execution '{execution_id}' not found.", err=True)
        else:
            click.echo(f"Error cancelling execution: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error cancelling execution: {str(ex)}", err=True)


# =============================================================================
# Execution Commands
# =============================================================================


@cli.group()
def execution():
    """Manage workflow executions."""
    pass


@execution.command("list")
@click.option(
    "--workflow",
    "-w",
    default=None,
    help="Filter by workflow reference (namespace/name or bare name)",
)
@click.option(
    "--namespace",
    "-n",
    default=None,
    help="Filter by namespace",
)
@click.option(
    "--state",
    "-s",
    default=None,
    help="Filter by execution state (e.g., RUNNING, COMPLETED, FAILED)",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=50,
    help="Maximum number of results",
)
@click.option(
    "--offset",
    "-o",
    type=int,
    default=0,
    help="Number of results to skip",
)
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
def list_executions(
    workflow: str | None,
    namespace: str | None,
    state: str | None,
    limit: int,
    offset: int,
    format: str,
    server_url: str | None,
):
    """List workflow executions."""
    try:
        from flux.catalogs import resolve_workflow_ref

        base_url = server_url or get_server_url()

        if workflow and namespace:
            click.echo("Error: --workflow and --namespace are mutually exclusive.", err=True)
            return

        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if workflow:
            wf_namespace, wf_name = resolve_workflow_ref(workflow)
            params["namespace"] = wf_namespace
            params["workflow_name"] = wf_name
        elif namespace:
            params["namespace"] = namespace
        if state:
            params["state"] = state

        with get_http_client() as client:
            response = client.get(f"{base_url}/executions", params=params)
            response.raise_for_status()
            result = response.json()

        executions = result.get("executions", [])
        total = result.get("total", 0)

        if not executions:
            click.echo("No executions found.")
            return

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"\nExecutions ({len(executions)} of {total}):")
            click.echo("-" * 70)
            for ex in executions:
                state_str = ex.get("state", "UNKNOWN")
                worker = ex.get("worker_name") or "unassigned"
                ns = ex.get("workflow_namespace", "default")
                click.echo(
                    f"  {ex['execution_id'][:12]}...  "
                    f"{ns}/{ex['workflow_name']:20}  {state_str:12}  {worker}",
                )

    except httpx.HTTPStatusError as ex:
        click.echo(f"Error listing executions: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error listing executions: {str(ex)}", err=True)


@execution.command("show")
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
def show_execution(execution_id: str, detailed: bool, server_url: str | None):
    """Show details of a specific execution."""
    try:
        from flux.utils import to_json

        base_url = server_url or get_server_url()

        with get_http_client() as client:
            response = client.get(
                f"{base_url}/executions/{execution_id}",
                params={"detailed": detailed},
            )
            response.raise_for_status()
            result = response.json()

            pending = _fetch_pending_approvals(client, base_url, execution_id)

        click.echo(to_json(result))
        _render_pending_approvals(pending)

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Execution '{execution_id}' not found.", err=True)
        else:
            click.echo(f"Error showing execution: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error showing execution: {str(ex)}", err=True)


# =============================================================================
# Execution Approval Commands
# =============================================================================


def _fetch_pending_approvals(
    client: httpx.Client,
    base_url: str,
    execution_id: str,
) -> list[dict[str, Any]]:
    """Fetch pending approvals for an execution.

    Errors are caught and logged to stderr so the primary command output
    (the JSON payload on stdout) is never blocked by an approval-side
    failure. We narrow to the network/parse errors we actually expect —
    bare ``except Exception`` would also swallow programming errors.
    """
    try:
        resp = client.get(
            f"{base_url}/executions/{execution_id}/approvals",
            params={"status": "pending"},
        )
        resp.raise_for_status()
        return resp.json().get("approvals", []) or []
    except (httpx.HTTPError, json.JSONDecodeError) as ex:
        click.echo(f"(approvals lookup failed: {ex})", err=True)
        return []


def _render_pending_approvals(pending: list[dict[str, Any]]) -> None:
    """Render pending-approval info on stderr.

    Stderr keeps stdout reserved for the primary JSON payload so callers
    that pipe stdout to ``json.loads`` (e.g. E2E harness) keep working.
    """
    if not pending:
        return
    click.echo("", err=True)
    click.echo("Pending approvals:", err=True)
    for r in pending:
        requested = (r.get("requested_at") or "")[:19]
        click.echo(
            f"  - {r.get('task_call_id', '?')}  "
            f"{r.get('workflow_namespace', '?')}/"
            f"{r.get('workflow_name', '?')}/"
            f"{r.get('task_name', '?')}"
            f"  (requested {requested})",
            err=True,
        )


def _render_approvals_table(approvals: list[dict[str, Any]]) -> None:
    if not approvals:
        click.echo("(none)")
        return
    # EXECUTION and TASK CALL ID are shown in full: they are the exact
    # arguments `flux execution approve|reject` require, so a truncated value
    # would not be actionable.
    headers = ["REQUESTED", "WORKFLOW/TASK", "EXECUTION", "TASK CALL ID", "STATUS"]
    click.echo("  ".join(headers))
    for a in approvals:
        wf_task = (
            f"{a.get('workflow_namespace', '?')}/"
            f"{a.get('workflow_name', '?')}/"
            f"{a.get('task_name', '?')}"
        )
        execution_id = a.get("execution_id", "")
        task_call_id = a.get("task_call_id", "")
        requested = (a.get("requested_at") or "")[:19]
        status = a.get("status", "?")
        click.echo(
            f"{requested}  {wf_task}  {execution_id}  {task_call_id}  {status}",
        )


@execution.command("approvals")
@click.option(
    "--status",
    default="pending",
    type=click.Choice(["pending", "approved", "rejected", "cancelled", "all"]),
    help="Filter by approval status.",
)
@click.option(
    "--execution",
    "execution_id",
    default=None,
    help="Scope to one execution.",
)
@click.option("--namespace", "workflow_namespace", default=None, help="Filter by namespace.")
@click.option("--task", "task_name", default=None, help="Filter by task name.")
@click.option(
    "--workflow",
    default=None,
    help="Filter by workflow: '<name>' or '<namespace>/<name>'.",
)
@click.option("--age", default=None, help="Minimum age, e.g. '1h', '24h', '7d'.")
@click.option("--limit", default=20, type=int)
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def execution_approvals(
    status: str,
    execution_id: str | None,
    workflow_namespace: str | None,
    task_name: str | None,
    workflow: str | None,
    age: str | None,
    limit: int,
    as_json: bool,
    server_url: str | None,
):
    """List approval requests."""
    try:
        from flux.utils import parse_duration

        base_url = server_url or get_server_url()

        params: dict[str, Any] = {"status": status, "limit": limit}
        if execution_id:
            params["execution_id"] = execution_id
        if workflow_namespace:
            params["workflow_namespace"] = workflow_namespace
        if task_name:
            params["task_name"] = task_name
        if workflow:
            if "/" in workflow:
                ns, wn = workflow.split("/", 1)
                params["workflow_namespace"] = ns
                params["workflow_name"] = wn
            else:
                params["workflow_name"] = workflow
        if age:
            td = parse_duration(age)
            params["age_min"] = f"PT{int(td.total_seconds())}S"

        with get_http_client() as client:
            response = client.get(f"{base_url}/approvals", params=params)
            response.raise_for_status()
            result = response.json()

        if as_json:
            click.echo(json.dumps(result, indent=2))
            return
        _render_approvals_table(result.get("approvals", []))

    except httpx.HTTPStatusError as ex:
        click.echo(f"Error listing approvals: {str(ex)}", err=True)
        raise click.exceptions.Exit(1)
    except Exception as ex:
        click.echo(f"Error listing approvals: {str(ex)}", err=True)
        raise click.exceptions.Exit(1)


def _post_decision(
    execution_id: str,
    task_call_id: str,
    verb: Literal["approve", "reject"],
    reason: str | None,
    server_url: str | None,
    always: bool = False,
) -> dict[str, Any]:
    base_url = server_url or get_server_url()
    body: dict[str, Any] = {}
    if reason:
        body["reason"] = reason
    if always:
        body["always"] = True
    with get_http_client() as client:
        response = client.post(
            f"{base_url}/executions/{execution_id}/approvals/{quote(task_call_id, safe='')}/{verb}",
            json=body,
        )
        if response.status_code == 409:
            return response.json()
        response.raise_for_status()
        return response.json()


@execution.command("approve")
@click.argument("execution_id")
@click.argument("task_call_id")
@click.option("--reason", default=None, help="Optional reason for the decision.")
@click.option(
    "--always",
    is_flag=True,
    default=False,
    help=(
        "Standing grant: also auto-approve every later approval gate on the "
        "same task within this execution (including retry attempts)."
    ),
)
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def execution_approve(
    execution_id: str,
    task_call_id: str,
    reason: str | None,
    always: bool,
    server_url: str | None,
):
    """Approve a pending approval request."""
    try:
        resp = _post_decision(
            execution_id,
            task_call_id,
            "approve",
            reason,
            server_url,
            always=always,
        )
        if resp.get("error"):
            click.echo(
                f"Error: {resp.get('error')} ({resp.get('current_status', '?')})",
                err=True,
            )
            raise click.exceptions.Exit(1)
        click.echo("Approved.")
        click.echo(f"Execution {execution_id} → {resp.get('execution_state', 'unknown')}")
    except click.exceptions.Exit:
        raise
    except httpx.HTTPStatusError as ex:
        click.echo(f"Error approving: {str(ex)}", err=True)
        raise click.exceptions.Exit(1)
    except Exception as ex:
        click.echo(f"Error approving: {str(ex)}", err=True)
        raise click.exceptions.Exit(1)


@execution.command("reject")
@click.argument("execution_id")
@click.argument("task_call_id")
@click.option("--reason", default=None, help="Optional reason for the decision.")
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def execution_reject(
    execution_id: str,
    task_call_id: str,
    reason: str | None,
    server_url: str | None,
):
    """Reject a pending approval request."""
    try:
        resp = _post_decision(execution_id, task_call_id, "reject", reason, server_url)
        if resp.get("error"):
            click.echo(
                f"Error: {resp.get('error')} ({resp.get('current_status', '?')})",
                err=True,
            )
            raise click.exceptions.Exit(1)
        click.echo("Rejected.")
        click.echo(f"Execution {execution_id} → {resp.get('execution_state', 'unknown')}")
    except click.exceptions.Exit:
        raise
    except httpx.HTTPStatusError as ex:
        click.echo(f"Error rejecting: {str(ex)}", err=True)
        raise click.exceptions.Exit(1)
    except Exception as ex:
        click.echo(f"Error rejecting: {str(ex)}", err=True)
        raise click.exceptions.Exit(1)


# =============================================================================
# Worker Commands
# =============================================================================


@cli.group()
def worker():
    """Manage workers."""
    pass


@worker.command("list")
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
def list_workers(format: str, server_url: str | None):
    """List all registered workers."""
    try:
        base_url = server_url or get_server_url()

        with get_http_client() as client:
            response = client.get(f"{base_url}/workers")
            response.raise_for_status()
            workers = response.json()

        if not workers:
            click.echo("No workers found.")
            return

        if format == "json":
            click.echo(json.dumps(workers, indent=2))
        else:
            click.echo(f"\nWorkers ({len(workers)}):")
            click.echo("-" * 50)
            for w in workers:
                runtime = w.get("runtime")
                py_version = runtime.get("python_version", "unknown") if runtime else "unknown"
                labels = w.get("labels", {})
                label_str = ", ".join(f"{k}={v}" for k, v in labels.items()) if labels else ""
                click.echo(f"  {w['name']:30}  Python {py_version}  {label_str}")

    except httpx.HTTPStatusError as ex:
        click.echo(f"Error listing workers: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error listing workers: {str(ex)}", err=True)


@worker.command("show")
@click.argument("name")
@click.option(
    "--server-url",
    "-cp-url",
    default=None,
    help="Server URL to connect to.",
)
def show_worker(name: str, server_url: str | None):
    """Show details of a specific worker."""
    try:
        from flux.utils import to_json

        base_url = server_url or get_server_url()

        with get_http_client() as client:
            response = client.get(f"{base_url}/workers/{name}")
            response.raise_for_status()
            result = response.json()

        click.echo(to_json(result))

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Worker '{name}' not found.", err=True)
        else:
            click.echo(f"Error showing worker: {str(ex)}", err=True)
    except Exception as ex:
        click.echo(f"Error showing worker: {str(ex)}", err=True)


# =============================================================================
# Health Command
# =============================================================================


@cli.command("health")
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
def health_check(format: str, server_url: str | None):
    """Check the health of the Flux server."""
    try:
        base_url = server_url or get_server_url()

        with get_http_client(timeout=10.0) as client:
            response = client.get(f"{base_url}/health")
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            status = result.get("status", "unknown")
            database = "connected" if result.get("database") else "disconnected"
            version = result.get("version", "unknown")

            if status == "healthy":
                click.echo("✓ Server is healthy")
            else:
                click.echo("✗ Server is unhealthy", err=True)

            click.echo(f"  Database: {database}")
            click.echo(f"  Version: {version}")

    except httpx.ConnectError:
        click.echo(f"✗ Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"✗ Health check failed: {str(ex)}", err=True)


# =============================================================================
# Start Commands
# =============================================================================


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
    from flux.config import Configuration
    from flux.server import Server

    settings = Configuration.get().settings
    host = host or settings.server_host
    port = port or settings.server_port
    Server(host, port).start()


@start.command("worker")
@click.argument("name", type=str, required=False)
@click.option(
    "--server-url",
    "-surl",
    default=None,
    help="Server URL to connect to.",
)
@click.option(
    "--label",
    "-l",
    multiple=True,
    help="Worker label in key=value format (repeatable).",
)
def start_worker(name: str | None, server_url: str | None = None, label: tuple[str, ...] = ()):
    from flux.config import Configuration
    from flux.worker import Worker

    name = name or f"worker-{uuid4().hex[-6:]}"
    settings = Configuration.get().settings.workers
    server_url = server_url or settings.server_url

    labels = {}
    for item in label:
        if "=" not in item:
            click.echo(f"Invalid label format: '{item}'. Expected key=value.", err=True)
            raise SystemExit(1)
        k, v = item.split("=", 1)
        key = k.strip()
        value = v.strip()
        if not key:
            click.echo(f"Invalid label: '{item}'. Label key must be non-empty.", err=True)
            raise SystemExit(1)
        if not value:
            click.echo(f"Invalid label: '{item}'. Label value must be non-empty.", err=True)
            raise SystemExit(1)
        labels[key] = value

    Worker(name, server_url, labels=labels).start()


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


@cli.group("server")
def server_group():
    """Server lifecycle commands (run on the server host)."""
    pass


@server_group.command("join-token")
@click.option(
    "--ttl",
    "ttl_seconds",
    type=int,
    default=None,
    help="Token lifetime in seconds (default: [flux.workers] join_token_ttl, 3600).",
)
def server_join_token(ttl_seconds: int | None):
    """Mint a one-time worker join token (printed once, stored hashed).

    Hand the token to exactly one new worker as its registration credential
    (in place of the shared bootstrap token). It is consumed on first use
    and expires after the TTL. Runs against the server's database — execute
    on the server host. Once the fleet has migrated, disable the shared
    secret with [flux.workers] bootstrap_token_enabled = false.
    """
    from flux.config import Configuration
    from flux.security import join_tokens

    settings = Configuration.get().settings
    ttl = ttl_seconds or settings.workers.join_token_ttl
    try:
        token, expires_at = join_tokens.mint(ttl, created_by="cli")
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(token)
    click.echo(f"expires: {expires_at.isoformat()}Z", err=True)


@server_group.command("bootstrap-token")
@click.option(
    "--rotate",
    is_flag=True,
    default=False,
    help=(
        "Generate a fresh token and persist it. The server caches the token at startup, "
        "so you must restart it for the rotated value to take effect; existing workers "
        "must then re-register. If FLUX_WORKERS__BOOTSTRAP_TOKEN or [flux.workers] "
        "bootstrap_token is set, that override still wins until removed."
    ),
)
def server_bootstrap_token(rotate: bool):
    """Print the server's bootstrap token (or rotate it).

    Reading: prints the configured token (env / config file) if set; else the
    persisted file at <home>/bootstrap-token; else exits 1.

    Rotating: writes a new token to the persisted file. The running server
    keeps using its in-memory copy until restarted; configured overrides win.
    """
    from flux.config import Configuration
    from flux.security import bootstrap_token as bt

    settings = Configuration.get().settings
    # Mirror the resolver's normalization: a whitespace-only env/config value is
    # treated as unset so it does not silently win over the persisted file.
    configured = bt._normalize(settings.workers.bootstrap_token)
    home = settings.home

    if rotate:
        if configured:
            click.echo(
                "Warning: bootstrap_token is set via env var or config; rotating the file "
                "will not change the active token until that override is removed.",
                err=True,
            )
        token = bt.rotate(home)
        click.echo(token)
        return

    if configured:
        click.echo(configured)
        return

    persisted = bt.read_persisted(home)
    if persisted:
        click.echo(persisted)
        return

    click.echo(
        "No bootstrap token found. Start the server once to auto-generate one, "
        "or set FLUX_WORKERS__BOOTSTRAP_TOKEN.",
        err=True,
    )
    raise click.exceptions.Exit(1)


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
    "--run-as",
    default=None,
    help="Service account to run the schedule as (required when auth is enabled)",
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
def create_schedule(
    workflow_name: str,
    schedule_name: str,
    cron: str | None,
    interval_hours: int | None,
    interval_minutes: int | None,
    timezone: str,
    description: str | None,
    input: str | None,
    run_as: str | None,
    format: str,
    server_url: str | None,
):
    """Create a new schedule for a workflow."""
    try:
        from flux.catalogs import resolve_workflow_ref
        from flux.utils import parse_value

        namespace, wf_name = resolve_workflow_ref(workflow_name)

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
            "workflow_name": wf_name,
            "workflow_namespace": namespace,
            "name": schedule_name,
            "schedule_config": schedule_config,
            "description": description,
            "input_data": input_data,
            "run_as_service_account": run_as,
        }

        base_url = server_url or get_server_url()

        with get_http_client() as client:
            response = client.post(f"{base_url}/schedules", json=request_data)
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps(result, indent=2))
        else:
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

        with get_http_client() as client:
            response = client.get(f"{base_url}/schedules", params=params)
            response.raise_for_status()
            schedules = response.json()

        if not schedules:
            if format == "json":
                click.echo("[]")
            else:
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
def show_schedule(schedule_id: str, format: str, server_url: str | None):
    """Show details of a specific schedule."""
    try:
        base_url = server_url or get_server_url()

        with get_http_client() as client:
            response = client.get(f"{base_url}/schedules/{schedule_id}")
            response.raise_for_status()
            schedule = response.json()

        if format == "json":
            click.echo(json.dumps(schedule, indent=2))
        else:
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
def pause_schedule(schedule_id: str, format: str, server_url: str | None):
    """Pause a schedule."""
    try:
        base_url = server_url or get_server_url()

        with get_http_client() as client:
            response = client.post(f"{base_url}/schedules/{schedule_id}/pause")
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps({"status": "ok", "schedule_id": schedule_id}, indent=2))
        else:
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
def resume_schedule(schedule_id: str, format: str, server_url: str | None):
    """Resume a paused schedule."""
    try:
        base_url = server_url or get_server_url()

        with get_http_client() as client:
            response = client.post(f"{base_url}/schedules/{schedule_id}/resume")
            response.raise_for_status()
            result = response.json()

        if format == "json":
            click.echo(json.dumps({"status": "ok", "schedule_id": schedule_id}, indent=2))
        else:
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
@click.confirmation_option(prompt="Are you sure you want to delete this schedule?")
def delete_schedule(schedule_id: str, format: str, server_url: str | None):
    """Delete a schedule."""
    try:
        base_url = server_url or get_server_url()

        with get_http_client() as client:
            response = client.delete(f"{base_url}/schedules/{schedule_id}")
            response.raise_for_status()

        if format == "json":
            click.echo(json.dumps({"status": "ok", "schedule_id": schedule_id}, indent=2))
        else:
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

        with get_http_client() as client:
            response = client.get(f"{base_url}/schedules/{schedule_id}/history", params=params)
            response.raise_for_status()
            history = response.json()

        # The endpoint returns a ScheduleHistoryResponse object; the actual
        # rows live under "entries".
        entries = history.get("entries", []) if isinstance(history, dict) else history

        if format == "json":
            # Always emit valid JSON, even when there is no history.
            click.echo(json.dumps(history, indent=2))
        elif not entries:
            click.echo("No execution history found.")
        else:
            click.echo(f"Execution history for schedule '{schedule_id}':")
            click.echo()
            for entry in entries:
                state = entry.get("state", "UNKNOWN")
                status_icon = "✓" if state == "COMPLETED" else "✗" if state == "FAILED" else "⏸"
                click.echo(
                    f"{status_icon} {entry.get('execution_id', '?')} - {state}",
                )

                if entry.get("started_at"):
                    click.echo(f"   Started: {entry['started_at']}")
                if entry.get("completed_at"):
                    click.echo(f"   Completed: {entry['completed_at']}")
                if entry.get("error"):
                    click.echo(f"   Error: {entry['error']}")
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
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def list_secrets(format: str, server_url: str | None):
    """List all available secrets (shows only secret names, not values)."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.get(f"{base_url}/admin/secrets")
            response.raise_for_status()
            secrets_list = response.json()
        if format == "json":
            click.echo(json.dumps({"secrets": secrets_list}, indent=2))
        else:
            if not secrets_list:
                click.echo("No secrets found.")
                return
            click.echo("Available secrets:")
            for secret_name in secrets_list:
                click.echo(f"  - {secret_name}")
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error listing secrets: {str(ex)}", err=True)


@secrets.command("set")
@click.argument("name")
@click.argument("value")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def set_secret(name: str, value: str, format: str, server_url: str | None):
    """Set a secret value with given name and value.

    This command will create a new secret or update an existing one.
    """
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.post(f"{base_url}/admin/secrets", json={"name": name, "value": value})
            response.raise_for_status()
        if format == "json":
            click.echo(json.dumps({"status": "ok", "name": name}, indent=2))
        else:
            click.echo(f"Secret '{name}' has been set successfully.")
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error setting secret: {str(ex)}", err=True)


@secrets.command("get")
@click.argument("name")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def get_secret(name: str, format: str, server_url: str | None):
    """Get a secret value by name.

    Warning: This will display the secret value in the terminal.
    Only use this command for testing or in secure environments.
    """
    try:
        if format != "json":
            if not click.confirm(f"Are you sure you want to display the secret '{name}'?"):
                click.echo("Operation cancelled.")
                return

        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.get(f"{base_url}/admin/secrets/{name}")
            if response.status_code == 404:
                click.echo(f"Secret not found: {name}", err=True)
                return
            response.raise_for_status()
            data = response.json()
        value = data.get("value", data)
        if format == "json":
            click.echo(json.dumps({"name": name, "value": value}, indent=2))
        else:
            click.echo(f"Secret '{name}': {value}")
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error getting secret: {str(ex)}", err=True)


@secrets.command("remove")
@click.argument("name")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def remove_secret(name: str, format: str, server_url: str | None):
    """Remove a secret by name.

    This permanently deletes the secret from the database.
    """
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.delete(f"{base_url}/admin/secrets/{name}")
            response.raise_for_status()
        if format == "json":
            click.echo(json.dumps({"status": "ok", "name": name}, indent=2))
        else:
            click.echo(f"Secret '{name}' has been removed successfully.")
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error removing secret: {str(ex)}", err=True)


@cli.group()
def config():
    """Manage Flux configuration key-value pairs."""
    pass


@config.command("list")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def list_configs(format: str, server_url: str | None):
    """List all configuration keys."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.get(f"{base_url}/admin/configs")
            response.raise_for_status()
            config_list = response.json()
        if format == "json":
            click.echo(json.dumps({"configs": config_list}, indent=2))
        else:
            if not config_list:
                click.echo("No configs found.")
                return
            click.echo("Available configs:")
            for name in config_list:
                click.echo(f"  - {name}")
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error listing configs: {str(ex)}", err=True)


@config.command("set")
@click.argument("name")
@click.argument("value")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def set_config(name: str, value: str, format: str, server_url: str | None):
    """Set a configuration value."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.post(f"{base_url}/admin/configs", json={"name": name, "value": value})
            response.raise_for_status()
        if format == "json":
            click.echo(json.dumps({"status": "ok", "name": name}, indent=2))
        else:
            click.echo(f"Config '{name}' has been set successfully.")
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error setting config: {str(ex)}", err=True)


@config.command("get")
@click.argument("name")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def get_config_cmd(name: str, format: str, server_url: str | None):
    """Get a configuration value by name."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.get(f"{base_url}/admin/configs/{name}")
            if response.status_code == 404:
                click.echo(f"Config not found: {name}", err=True)
                return
            response.raise_for_status()
            data = response.json()
        value = data.get("value", data)
        if format == "json":
            click.echo(json.dumps({"name": name, "value": value}, indent=2))
        else:
            click.echo(f"Config '{name}': {value}")
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error getting config: {str(ex)}", err=True)


@config.command("remove")
@click.argument("name")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["simple", "json"]),
    default="simple",
    help="Output format",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def remove_config(name: str, format: str, server_url: str | None):
    """Remove a configuration by name."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.delete(f"{base_url}/admin/configs/{name}")
            response.raise_for_status()
        if format == "json":
            click.echo(json.dumps({"status": "ok", "name": name}, indent=2))
        else:
            click.echo(f"Config '{name}' has been removed successfully.")
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error removing config: {str(ex)}", err=True)


@cli.group()
def agent():
    """Manage Flux agents."""
    pass


@agent.command("create")
@click.argument("name")
@click.option("--model", "-m", required=False, help="Model in provider/model_name format")
@click.option("--system-prompt", "-s", required=False, help="System prompt text")
@click.option(
    "--system-prompt-file",
    type=click.Path(exists=True),
    help="Read system prompt from file",
)
@click.option("--description", "-d", help="Agent description")
@click.option(
    "--file",
    "-f",
    "definition_file",
    type=click.Path(exists=True),
    help="YAML definition file",
)
@click.option("--tools", multiple=True, help="Built-in tool names (repeatable)")
@click.option("--tools-file", type=click.Path(exists=True), help="Python file with @task tools")
@click.option("--workflow-file", type=click.Path(exists=True), help="Custom workflow file")
@click.option("--mcp-server", multiple=True, help="MCP server URL (repeatable)")
@click.option("--skills-dir", type=click.Path(exists=True), help="Skills directory")
@click.option("--planning/--no-planning", default=None, help="Enable planning")
@click.option("--max-tool-calls", type=int, help="Max tool call iterations")
@click.option("--max-tokens", type=int, help="Max LLM response tokens")
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high"]),
    help="Reasoning depth",
)
@click.option(
    "--format",
    "-F",
    "output_format",
    type=click.Choice(["simple", "json"]),
    default="simple",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def create_agent(
    name,
    model,
    system_prompt,
    system_prompt_file,
    description,
    definition_file,
    tools,
    tools_file,
    workflow_file,
    mcp_server,
    skills_dir,
    planning,
    max_tool_calls,
    max_tokens,
    reasoning_effort,
    output_format,
    server_url,
):
    """Create an agent definition."""
    import yaml

    from flux.agents.types import AgentDefinition

    try:
        data = {}
        if definition_file:
            with open(definition_file) as f:
                data = yaml.safe_load(f)

        data["name"] = name
        if model:
            data["model"] = model
        if system_prompt:
            data["system_prompt"] = system_prompt
        if system_prompt_file:
            with open(system_prompt_file) as f:
                data["system_prompt"] = f.read()
        if description:
            data["description"] = description
        if tools:
            data["tools"] = list(tools)
        if tools_file:
            data["tools_file"] = tools_file
        if workflow_file:
            data["workflow_file"] = workflow_file
        if mcp_server:
            data["mcp_servers"] = [{"url": url} for url in mcp_server]
        if skills_dir:
            data["skills_dir"] = skills_dir
        if planning is not None:
            data["planning"] = planning
        if max_tool_calls is not None:
            data["max_tool_calls"] = max_tool_calls
        if max_tokens is not None:
            data["max_tokens"] = max_tokens
        if reasoning_effort:
            data["reasoning_effort"] = reasoning_effort

        if data.get("tools_file"):
            tools_path = Path(data["tools_file"])
            if tools_path.exists() and tools_path.is_file():
                data["tools_file"] = tools_path.read_text()

        if data.get("workflow_file"):
            wf_path = Path(data["workflow_file"])
            if wf_path.exists() and wf_path.is_file():
                data["workflow_file"] = wf_path.read_text()

        if data.get("skills_dir"):
            skills_path = Path(data["skills_dir"])
            if skills_path.is_dir():
                skills_data = {}
                for skill_dir in skills_path.iterdir():
                    if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                        skill_files = {}
                        for f in skill_dir.rglob("*"):
                            if f.is_file():
                                rel = str(f.relative_to(skills_path))
                                skill_files[rel] = f.read_text()
                        skills_data[skill_dir.name] = skill_files
                data["skills_dir"] = json.dumps(skills_data)

        definition = AgentDefinition(**data)

        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.post(f"{base_url}/admin/agents", json=definition.model_dump())
            response.raise_for_status()

        if output_format == "json":
            click.echo(json.dumps({"status": "ok", "name": name}, indent=2))
        else:
            click.echo(f"Agent '{name}' created successfully.")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 409:
            click.echo(f"Agent '{name}' already exists.", err=True)
        else:
            click.echo(f"Error creating agent: {str(ex)}", err=True)
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error creating agent: {str(ex)}", err=True)


@agent.command("list")
@click.option("--format", "-f", type=click.Choice(["simple", "json"]), default="simple")
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def list_agents(format, server_url):
    """List all agents."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.get(f"{base_url}/admin/agents")
            response.raise_for_status()
            agents = response.json()

        if format == "json":
            click.echo(json.dumps({"agents": agents}, indent=2))
        else:
            if not agents:
                click.echo("No agents found.")
                return
            click.echo("Agents:")
            for a in agents:
                desc = f" — {a['description']}" if a.get("description") else ""
                click.echo(f"  {a['name']} ({a['model']}){desc}")

    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error listing agents: {str(ex)}", err=True)


@agent.command("show")
@click.argument("name")
@click.option("--format", "-f", type=click.Choice(["simple", "json", "yaml"]), default="yaml")
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def show_agent(name, format, server_url):
    """Show agent definition."""
    import yaml

    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.get(f"{base_url}/admin/agents/{name}")
            response.raise_for_status()
            data = response.json()

        if format == "json":
            click.echo(json.dumps(data, indent=2))
        elif format == "yaml":
            click.echo(yaml.dump(data, default_flow_style=False, sort_keys=False))
        else:
            for key, value in data.items():
                click.echo(f"  {key}: {value}")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Agent '{name}' not found.", err=True)
        else:
            click.echo(f"Error showing agent: {str(ex)}", err=True)
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error showing agent: {str(ex)}", err=True)


@agent.command("update")
@click.argument("name")
@click.option("--model", "-m", help="Model in provider/model_name format")
@click.option("--system-prompt", "-s", help="System prompt text")
@click.option(
    "--system-prompt-file",
    type=click.Path(exists=True),
    help="Read system prompt from file",
)
@click.option("--description", "-d", help="Agent description")
@click.option(
    "--file",
    "-f",
    "definition_file",
    type=click.Path(exists=True),
    help="YAML definition file",
)
@click.option("--planning/--no-planning", default=None, help="Enable planning")
@click.option("--max-tool-calls", type=int, help="Max tool call iterations")
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high"]),
    help="Reasoning depth",
)
@click.option(
    "--format",
    "-F",
    "output_format",
    type=click.Choice(["simple", "json"]),
    default="simple",
)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def update_agent(
    name,
    model,
    system_prompt,
    system_prompt_file,
    description,
    definition_file,
    planning,
    max_tool_calls,
    reasoning_effort,
    output_format,
    server_url,
):
    """Update an agent definition."""
    import yaml

    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            get_resp = client.get(f"{base_url}/admin/agents/{name}")
            get_resp.raise_for_status()
            data = get_resp.json()

        if definition_file:
            with open(definition_file) as f:
                file_data = yaml.safe_load(f)
                data.update(file_data)

        if model:
            data["model"] = model
        if system_prompt:
            data["system_prompt"] = system_prompt
        if system_prompt_file:
            with open(system_prompt_file) as f:
                data["system_prompt"] = f.read()
        if description:
            data["description"] = description
        if planning is not None:
            data["planning"] = planning
        if max_tool_calls is not None:
            data["max_tool_calls"] = max_tool_calls
        if reasoning_effort:
            data["reasoning_effort"] = reasoning_effort

        data["name"] = name

        with get_http_client() as client:
            put_resp = client.put(f"{base_url}/admin/agents/{name}", json=data)
            put_resp.raise_for_status()

        if output_format == "json":
            click.echo(json.dumps({"status": "ok", "name": name}, indent=2))
        else:
            click.echo(f"Agent '{name}' updated successfully.")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Agent '{name}' not found.", err=True)
        else:
            click.echo(f"Error updating agent: {str(ex)}", err=True)
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error updating agent: {str(ex)}", err=True)


@agent.command("delete")
@click.argument("name")
@click.option("--format", "-f", type=click.Choice(["simple", "json"]), default="simple")
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def delete_agent(name, format, server_url):
    """Delete an agent definition."""
    try:
        base_url = server_url or get_server_url()
        with get_http_client() as client:
            response = client.delete(f"{base_url}/admin/agents/{name}")
            response.raise_for_status()

        if format == "json":
            click.echo(json.dumps({"status": "ok", "name": name}, indent=2))
        else:
            click.echo(f"Agent '{name}' deleted successfully.")

    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            click.echo(f"Agent '{name}' not found.", err=True)
        else:
            click.echo(f"Error deleting agent: {str(ex)}", err=True)
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error deleting agent: {str(ex)}", err=True)


@agent.command("start")
@click.argument("name")
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["terminal", "web", "api"]),
    default="terminal",
    help="Serving mode",
)
@click.option("--session", "-s", "session_id", help="Attach to existing session")
@click.option("--port", "-p", type=int, help="Port for web/api mode")
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host to bind web/api mode (default: 127.0.0.1; use 0.0.0.0 to expose externally)",
)
@click.option("--server", default=None, help="Flux server URL (default: from config)")
@click.option("--plain", is_flag=True, help="Use plain ANSI terminal (no TUI)")
def start_agent(name, mode, session_id, port, host, server, plain):
    """Start an agent in the specified mode."""
    import asyncio

    from flux.agents.process import AgentProcess
    from flux.config import Configuration

    try:
        if server is None:
            settings = Configuration.get().settings
            server = f"http://{settings.server_host}:{settings.server_port}"

        token = _get_auth_token()

        workflow_name = "agent_chat"
        try:
            with get_http_client() as client:
                resp = client.get(f"{server}/admin/agents/{name}")
                if resp.status_code == 200:
                    agent_def = resp.json()
                    if agent_def.get("workflow_file"):
                        click.echo("Custom workflow detected. Registering...")
                        custom_name = f"agent_custom_{name}"
                        source = agent_def["workflow_file"]
                        if isinstance(source, str):
                            source = source.encode("utf-8")
                        reg_resp = client.post(
                            f"{server}/workflows",
                            files={"file": (f"{custom_name}.py", source)},
                        )
                        if reg_resp.status_code == 200:
                            workflow_name = custom_name
                            click.echo(f"Custom workflow registered as '{custom_name}'.")
                        else:
                            click.echo(
                                f"Warning: failed to register custom workflow: {reg_resp.text}",
                                err=True,
                            )
        except Exception as ex:
            # Falling back to the default workflow silently would run the wrong
            # thing — surface the failure so the operator can see why.
            click.echo(
                f"Warning: custom workflow lookup/registration raised {type(ex).__name__}: {ex}; "
                f"falling back to default 'agent_chat'.",
                err=True,
            )

        if plain:
            import os

            os.environ["FLUX_PLAIN_TERMINAL"] = "1"

        process = AgentProcess(
            agent_name=name,
            server_url=server,
            mode=mode,
            session_id=session_id,
            token=token,
            port=port,
            host=host,
            workflow_name=workflow_name,
        )
        asyncio.run(process.run())
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        click.echo(f"Error starting agent: {str(ex)}", err=True)


@agent.command("stop")
@click.argument("session_id")
def stop_agent(session_id):
    """Stop a running agent session."""
    import httpx

    from flux.config import Configuration

    click.echo(f"Stopping session {session_id}...")

    try:
        settings = Configuration.get().settings
        server_url = f"http://{settings.server_host}:{settings.server_port}"
        token = _get_auth_token()

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = httpx.post(
            f"{server_url}/executions/{session_id}/cancel",
            headers=headers,
        )
        response.raise_for_status()
        click.echo(f"Session {session_id} stopped.")
    except Exception as ex:
        click.echo(f"Error stopping session: {str(ex)}", err=True)


@agent.group("session")
def agent_session():
    """Manage agent sessions."""
    pass


@agent_session.command("list")
@click.argument("agent_name", required=False)
@click.option("--format", "-f", type=click.Choice(["simple", "json"]), default="simple")
@click.option("--state", "state", default=None, help="Filter by execution state")
@click.option("--limit", type=int, default=50, show_default=True)
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def list_sessions(agent_name, format, state, limit, server_url):
    """List agent sessions."""
    try:
        url = server_url or get_server_url()
        if agent_name:
            endpoint = f"{url}/agents/{agent_name}/sessions"
        else:
            endpoint = f"{url}/agents/sessions"
        params: dict[str, str] = {"limit": str(limit)}
        if state:
            params["state"] = state

        with get_http_client() as client:
            resp = client.get(endpoint, params=params)
            resp.raise_for_status()
            data = resp.json()

        sessions = data.get("sessions", []) if isinstance(data, dict) else []

        if format == "json":
            click.echo(json.dumps(data, indent=2))
        else:
            if not sessions:
                click.echo("No sessions found.")
                return
            click.echo("Sessions:")
            for s in sessions:
                eid = s.get("execution_id", "?")
                agent = s.get("agent_name", "?")
                st = s.get("state", "?")
                started = s.get("started_at") or "?"
                click.echo(
                    f"  {eid[:12]}...  state={st:10s}  agent={agent:20s}  started={started}",
                )
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error listing sessions: {str(ex)}", err=True)


@agent_session.command("show")
@click.argument("session_id")
@click.option("--format", "-f", type=click.Choice(["simple", "json"]), default="simple")
@click.option("--server-url", "server_url", default=None, help="Flux server URL")
def show_session(session_id, format, server_url):
    """Show session details."""
    try:
        url = server_url or get_server_url()
        with get_http_client() as client:
            resp = client.get(f"{url}/executions/{session_id}")
            if resp.status_code == 404:
                click.echo(f"Session not found: {session_id}", err=True)
                return
            resp.raise_for_status()
            data = resp.json()

        if format == "json":
            click.echo(json.dumps(data, indent=2))
        else:
            click.echo(f"Session: {data.get('execution_id', session_id)}")
            click.echo(f"  State:    {data.get('state', '?')}")
            click.echo(
                f"  Workflow: {data.get('workflow_namespace', '?')}/{data.get('workflow_name', '?')}",
            )
            click.echo(f"  Worker:   {data.get('current_worker') or 'none'}")
            inp = data.get("input")
            agent = inp.get("agent", "?") if isinstance(inp, dict) else "?"
            click.echo(f"  Agent:    {agent}")
            if data.get("output"):
                click.echo(f"  Output:   {json.dumps(data['output'])[:200]}")
    except httpx.ConnectError:
        click.echo(f"Cannot connect to server at {server_url or get_server_url()}", err=True)
    except Exception as ex:
        click.echo(f"Error showing session: {str(ex)}", err=True)


@agent_session.command("resume")
@click.argument("session_id")
def resume_session(session_id):
    """Resume a session in terminal mode (shortcut for start --session)."""
    import asyncio

    from flux.agents.process import AgentProcess
    from flux.config import Configuration

    try:
        settings = Configuration.get().settings
        server = f"http://{settings.server_host}:{settings.server_port}"
        token = _get_auth_token()

        process = AgentProcess(
            agent_name="",
            server_url=server,
            mode="terminal",
            session_id=session_id,
            token=token,
        )
        asyncio.run(process.run())
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        click.echo(f"Error resuming session: {str(ex)}", err=True)


def _get_auth_token() -> str | None:
    """Resolve the operator auth token for agent processes.

    Resolution order:
    1. ``FLUX_AUTH_TOKEN`` environment variable.
    2. Bearer token derived from OIDC credentials via ``cli_auth.get_auth_headers()``
       (refresh-token exchange on each invocation).

    Returns ``None`` when no token can be resolved so callers may proceed
    unauthenticated against servers that do not enforce auth.
    """
    import os

    token = os.environ.get("FLUX_AUTH_TOKEN")
    if token:
        return token

    try:
        from flux.cli_auth import get_auth_headers

        headers = get_auth_headers()
        auth_header = headers.get("Authorization", "") if headers else ""
        if auth_header.startswith("Bearer "):
            return auth_header[len("Bearer ") :]
    except Exception:
        pass

    return None


@cli.group("db")
def db_group():
    """Database schema / migration commands."""
    pass


def _migration_engine():
    # Build the engine WITHOUT constructing a DatabaseRepository: its __init__
    # runs migrations as a side effect, which would make read-only commands like
    # `flux db current` silently upgrade the database. __new__ skips __init__;
    # _create_engine reads only from configuration.
    from flux.config import Configuration
    from flux.models import PostgreSQLRepository, SQLiteRepository

    settings = Configuration.get().settings
    cls = PostgreSQLRepository if settings.database_type == "postgresql" else SQLiteRepository
    return cls.__new__(cls)._create_engine()


@db_group.command("upgrade")
def db_upgrade():
    """Migrate the database to the latest schema revision."""
    from flux.migrations.runner import current_revision, run_migrations

    engine = _migration_engine()
    run_migrations(engine)
    click.echo(f"Database is at revision: {current_revision(engine)}")


@db_group.command("current")
def db_current():
    """Show the database's current schema revision."""
    from flux.migrations.runner import current_revision

    rev = current_revision(_migration_engine())
    click.echo(rev or "none (database is unmanaged or empty)")


@db_group.command("history")
def db_history():
    """List the migration revisions, newest first."""
    from flux.migrations.runner import _alembic_config
    from alembic.script import ScriptDirectory

    cfg = _alembic_config(_migration_engine())
    for script in ScriptDirectory.from_config(cfg).walk_revisions():
        click.echo(f"{script.revision}  {script.doc.splitlines()[0] if script.doc else ''}")


from flux.cli_auth import auth, principals, roles  # noqa: E402

cli.add_command(auth)
cli.add_command(roles)
cli.add_command(principals)

import flux.cli_service  # noqa: F401, E402


if __name__ == "__main__":  # pragma: no cover
    cli()
