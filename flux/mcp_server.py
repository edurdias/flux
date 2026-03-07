from __future__ import annotations

import json
import httpx

from typing import Literal
from fastmcp import FastMCP

from flux.config import Configuration
from flux.servers.models import ExecutionContext
from flux.utils import get_logger

logger = get_logger(__name__)


class MCPServer:
    def __init__(
        self,
        name: str | None = None,
        host: str | None = None,
        port: int | None = None,
        server_url: str | None = None,
        transport: Literal["stdio", "streamable-http", "sse"] | None = None,
    ):
        """
        Initialize the MCP server.

        Args:
            host: Host address to bind the server to
            port: Port to listen on
            name: Name used for identifying the MCP server
        """
        settings = Configuration.get().settings
        config = settings.mcp
        self.host = host or config.host
        self.port = port or config.port
        self.name = name or config.name
        self.server_url = server_url or config.server_url
        self.transport = transport or config.transport or "streamable-http"
        self.config = {
            "log_level": settings.log_level.lower(),
            "access_log": settings.log_level.lower() == "debug",
        }
        self.mcp = FastMCP(name or "Flux")
        self._setup_tools()

    def start(self):
        """Start the MCP server."""
        logger.info(f"Starting MCP server '{self.name}'")
        logger.info(f"Flux server at: {self.server_url}")

        self.mcp.run(
            transport=self.transport,
            host=self.host,
            port=self.port,
            path="/mcp",
            uvicorn_config=self.config,
        )
        logger.info(f"MCP server '{self.name}' is running at {self.host}:{self.port}")

    def _setup_tools(self):
        """Set up all MCP tools for Flux workflow orchestration."""

        # Workflow Management Tools
        @self.mcp.tool()
        async def list_workflows() -> dict[str, any]:
            """List all available workflows in the Flux system."""
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(f"{self.server_url}/workflows")
                    response.raise_for_status()
                    workflows = response.json()

                    logger.info(f"Retrieved {len(workflows)} workflows")
                    return {"success": True, "workflows": workflows, "count": len(workflows)}
            except httpx.ConnectError:
                error_msg = f"Could not connect to Flux server at {self.server_url}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def get_workflow_details(workflow_name: str) -> dict[str, any]:
            """Get detailed information about a specific workflow.

            Args:
                workflow_name: Name of the workflow to get details for
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(f"{self.server_url}/workflows/{workflow_name}")
                    response.raise_for_status()
                    workflow_details = response.json()

                    logger.info(f"Retrieved details for workflow: {workflow_name}")
                    return {"success": True, "workflow": workflow_details}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Workflow '{workflow_name}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error retrieving workflow details: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        # Workflow Execution Tools
        @self.mcp.tool()
        async def execute_workflow_async(
            workflow_name: str,
            input_data: str,
            detailed: bool = False,
        ) -> dict[str, any]:
            """Execute a workflow asynchronously and return immediately with execution ID.

            Args:
                workflow_name: Name of the workflow to execute
                input_data: JSON string of input data for the workflow
                detailed: Whether to return detailed execution information
            """
            try:
                # Parse input data
                try:
                    parsed_input = json.loads(input_data) if input_data else None
                except json.JSONDecodeError:
                    # If not valid JSON, treat as string
                    parsed_input = input_data

                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.server_url}/workflows/{workflow_name}/run/async",
                        json=parsed_input,
                        params={"detailed": detailed},
                    )
                    response.raise_for_status()
                    result = response.json()
                    context = ExecutionContext.from_dict(result)

                    logger.info(
                        f"Started async execution of workflow: {workflow_name} (ID: {context.execution_id})",
                    )
                    return context if detailed else context.summary()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Workflow '{workflow_name}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error executing workflow: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def execute_workflow_sync(
            workflow_name: str,
            input_data: str,
            detailed: bool = False,
        ) -> dict[str, any]:
            """Execute a workflow synchronously and wait for completion.

            Args:
                workflow_name: Name of the workflow to execute
                input_data: JSON string of input data for the workflow
                detailed: Whether to return detailed execution information
            """
            try:
                # Parse input data
                try:
                    parsed_input = json.loads(input_data) if input_data else None
                except json.JSONDecodeError:
                    parsed_input = input_data

                async with httpx.AsyncClient(timeout=300.0) as client:  # Longer timeout for sync
                    response = await client.post(
                        f"{self.server_url}/workflows/{workflow_name}/run/sync",
                        json=parsed_input,
                        params={"detailed": detailed},
                    )
                    response.raise_for_status()
                    result = response.json()
                    context = ExecutionContext.from_dict(result)

                    logger.info(
                        f"Completed sync execution of workflow: {workflow_name} (ID: {context.execution_id})",
                    )
                    return context if detailed else context.summary()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Workflow '{workflow_name}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error executing workflow: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def resume_workflow_async(
            workflow_name: str,
            execution_id: str,
            input_data: str,
            detailed: bool = False,
        ) -> dict[str, any]:
            """Resume a paused workflow asynchronously with input data.

            Args:
                workflow_name: Name of the workflow to resume
                execution_id: ID of the paused execution to resume
                input_data: JSON string of input data to provide during resume
                detailed: Whether to return detailed execution information
            """
            try:
                # Parse input data
                try:
                    parsed_input = json.loads(input_data) if input_data else None
                except json.JSONDecodeError:
                    # If not valid JSON, treat as string
                    parsed_input = input_data

                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.server_url}/workflows/{workflow_name}/resume/{execution_id}/async",
                        json=parsed_input,
                        params={"detailed": detailed},
                    )
                    response.raise_for_status()
                    result = response.json()
                    context = ExecutionContext.from_dict(result)

                    logger.info(
                        f"Started async resume of workflow: {workflow_name} (ID: {context.execution_id})",
                    )
                    return context if detailed else context.summary()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = (
                        f"Workflow '{workflow_name}' or execution '{execution_id}' not found"
                    )
                elif e.response.status_code == 400:
                    error_msg = f"Cannot resume execution: {e.response.text}"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error resuming workflow: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def resume_workflow_sync(
            workflow_name: str,
            execution_id: str,
            input_data: str,
            detailed: bool = False,
        ) -> dict[str, any]:
            """Resume a paused workflow synchronously and wait for completion.

            Args:
                workflow_name: Name of the workflow to resume
                execution_id: ID of the paused execution to resume
                input_data: JSON string of input data to provide during resume
                detailed: Whether to return detailed execution information
            """
            try:
                # Parse input data
                try:
                    parsed_input = json.loads(input_data) if input_data else None
                except json.JSONDecodeError:
                    # If not valid JSON, treat as string
                    parsed_input = input_data

                async with httpx.AsyncClient(timeout=300.0) as client:  # Longer timeout for sync
                    response = await client.post(
                        f"{self.server_url}/workflows/{workflow_name}/resume/{execution_id}/sync",
                        json=parsed_input,
                        params={"detailed": detailed},
                    )
                    response.raise_for_status()
                    result = response.json()
                    context = ExecutionContext.from_dict(result)

                    logger.info(
                        f"Completed sync resume of workflow: {workflow_name} (ID: {context.execution_id})",
                    )
                    return context if detailed else context.summary()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = (
                        f"Workflow '{workflow_name}' or execution '{execution_id}' not found"
                    )
                elif e.response.status_code == 400:
                    error_msg = f"Cannot resume execution: {e.response.text}"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error resuming workflow: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def upload_workflow(file_content: str) -> dict[str, any]:
            """Upload and register a new workflow file.

            Args:
                file_content: Python code content of the workflow file
            """
            try:
                # Send the file to the Flux API server as a multipart upload
                async with httpx.AsyncClient(timeout=30.0) as client:
                    # Convert string to bytes before uploading
                    file_bytes = file_content.encode("utf-8")
                    files = {"file": ("workflow.py", file_bytes, "text/x-python")}
                    response = await client.post(f"{self.server_url}/workflows", files=files)
                    response.raise_for_status()
                    result = response.json()

                logger.info("Uploaded workflow successfully via Flux API server")
                return {
                    "success": True,
                    "workflows": [w.get("name") for w in result],
                    "result": result,
                }
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except SyntaxError as e:
                error_msg = f"Syntax error in workflow file: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error uploading workflow: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        # Status & Monitoring Tools
        @self.mcp.tool()
        async def get_execution_status(
            workflow_name: str,
            execution_id: str,
            detailed: bool = False,
        ) -> dict[str, any]:
            """Get the current status of a workflow execution.

            Args:
                workflow_name: Name of the workflow
                execution_id: ID of the execution to check
                detailed: Whether to return detailed execution information including events
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(
                        f"{self.server_url}/workflows/{workflow_name}/status/{execution_id}",
                        params={"detailed": detailed},
                    )
                    response.raise_for_status()
                    result = response.json()
                    context = ExecutionContext.from_dict(result)
                    logger.info(
                        f"Retrieved status for execution of workflow: {workflow_name} (ID: {context.execution_id})",
                    )
                    return context if detailed else context.summary()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Execution '{execution_id}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error retrieving execution status: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def cancel_execution(
            workflow_name: str,
            execution_id: str,
            mode: str = "async",
            detailed: bool = False,
        ) -> dict[str, any]:
            """Cancel a running workflow execution.

            Args:
                workflow_name: Name of the workflow
                execution_id: ID of the execution to cancel
                mode: Cancellation mode - 'sync' (wait for completion) or 'async' (immediate)
                detailed: Whether to return detailed execution information
            """
            if mode not in ["sync", "async"]:
                return {"success": False, "error": "Mode must be 'sync' or 'async'"}

            try:
                timeout = 300.0 if mode == "sync" else 30.0
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(
                        f"{self.server_url}/workflows/{workflow_name}/cancel/{execution_id}",
                        params={"mode": mode, "detailed": detailed},
                    )
                    response.raise_for_status()
                    result = response.json()

                    logger.info(f"Cancelled execution: {execution_id} (mode: {mode})")
                    return {
                        "success": True,
                        "execution_id": execution_id,
                        "workflow_name": workflow_name,
                        "cancellation_mode": mode,
                        "status": result,
                    }
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Execution '{execution_id}' not found"
                elif e.response.status_code == 400:
                    error_msg = f"Cannot cancel execution: {e.response.text}"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error cancelling execution: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def delete_workflow(
            workflow_name: str,
            version: int | None = None,
        ) -> dict[str, any]:
            """Delete a workflow from the Flux system.

            Args:
                workflow_name: Name of the workflow to delete
                version: Optional specific version to delete. If not provided, deletes all versions.
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    params = {"version": version} if version is not None else {}
                    response = await client.delete(
                        f"{self.server_url}/workflows/{workflow_name}",
                        params=params,
                    )
                    response.raise_for_status()
                    result = response.json()

                    version_info = f" version {version}" if version else " (all versions)"
                    logger.info(f"Deleted workflow: {workflow_name}{version_info}")
                    return {"success": True, "message": result.get("message", "Workflow deleted")}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Workflow '{workflow_name}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error deleting workflow: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def list_workflow_versions(workflow_name: str) -> dict[str, any]:
            """List all versions of a specific workflow.

            Args:
                workflow_name: Name of the workflow to list versions for
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(
                        f"{self.server_url}/workflows/{workflow_name}/versions"
                    )
                    response.raise_for_status()
                    versions = response.json()

                    logger.info(f"Retrieved {len(versions)} versions for workflow: {workflow_name}")
                    return {"success": True, "versions": versions, "count": len(versions)}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Workflow '{workflow_name}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error listing workflow versions: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def get_workflow_version(workflow_name: str, version: int) -> dict[str, any]:
            """Get details of a specific workflow version.

            Args:
                workflow_name: Name of the workflow
                version: Version number to retrieve
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(
                        f"{self.server_url}/workflows/{workflow_name}/versions/{version}"
                    )
                    response.raise_for_status()
                    workflow = response.json()

                    logger.info(f"Retrieved workflow {workflow_name} version {version}")
                    return {"success": True, "workflow": workflow}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Workflow '{workflow_name}' version {version} not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error retrieving workflow version: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        # Execution Management Tools
        @self.mcp.tool()
        async def list_executions(
            workflow_name: str | None = None,
            state: str | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> dict[str, any]:
            """List workflow executions with optional filtering.

            Args:
                workflow_name: Optional workflow name to filter by
                state: Optional execution state to filter by (e.g., 'running', 'completed', 'failed')
                limit: Maximum number of executions to return
                offset: Number of executions to skip for pagination
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    params = {"limit": limit, "offset": offset}
                    if workflow_name:
                        params["workflow_name"] = workflow_name
                    if state:
                        params["state"] = state

                    response = await client.get(
                        f"{self.server_url}/executions",
                        params=params,
                    )
                    response.raise_for_status()
                    result = response.json()

                    executions = result.get("executions", [])
                    total = result.get("total", len(executions))
                    logger.info(f"Retrieved {len(executions)} executions (total: {total})")
                    return {
                        "success": True,
                        "executions": executions,
                        "total": total,
                        "limit": limit,
                        "offset": offset,
                    }
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error listing executions: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def get_execution(
            execution_id: str,
            detailed: bool = False,
        ) -> dict[str, any]:
            """Get details of a specific execution by ID.

            Args:
                execution_id: ID of the execution to retrieve
                detailed: Whether to return detailed execution information
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(
                        f"{self.server_url}/executions/{execution_id}",
                        params={"detailed": detailed},
                    )
                    response.raise_for_status()
                    result = response.json()
                    context = ExecutionContext.from_dict(result)

                    logger.info(f"Retrieved execution: {execution_id}")
                    return context if detailed else context.summary()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Execution '{execution_id}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error retrieving execution: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def list_workflow_executions(
            workflow_name: str,
            state: str | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> dict[str, any]:
            """List executions for a specific workflow.

            Args:
                workflow_name: Name of the workflow to list executions for
                state: Optional execution state to filter by
                limit: Maximum number of executions to return
                offset: Number of executions to skip for pagination
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    params = {"limit": limit, "offset": offset}
                    if state:
                        params["state"] = state

                    response = await client.get(
                        f"{self.server_url}/workflows/{workflow_name}/executions",
                        params=params,
                    )
                    response.raise_for_status()
                    result = response.json()

                    executions = result.get("executions", [])
                    total = result.get("total", len(executions))
                    logger.info(
                        f"Retrieved {len(executions)} executions for workflow {workflow_name}"
                    )
                    return {
                        "success": True,
                        "workflow_name": workflow_name,
                        "executions": executions,
                        "total": total,
                        "limit": limit,
                        "offset": offset,
                    }
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Workflow '{workflow_name}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error listing workflow executions: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        # Worker Management Tools
        @self.mcp.tool()
        async def list_workers() -> dict[str, any]:
            """List all workers in the Flux system."""
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(f"{self.server_url}/workers")
                    response.raise_for_status()
                    workers = response.json()

                    logger.info(f"Retrieved {len(workers)} workers")
                    return {"success": True, "workers": workers, "count": len(workers)}
            except httpx.ConnectError:
                error_msg = f"Could not connect to Flux server at {self.server_url}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def get_worker(worker_name: str) -> dict[str, any]:
            """Get details of a specific worker.

            Args:
                worker_name: Name of the worker to retrieve
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(f"{self.server_url}/workers/{worker_name}")
                    response.raise_for_status()
                    worker = response.json()

                    logger.info(f"Retrieved worker: {worker_name}")
                    return {"success": True, "worker": worker}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Worker '{worker_name}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error retrieving worker: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        # Schedule Management Tools
        @self.mcp.tool()
        async def create_schedule(
            workflow_name: str,
            name: str,
            schedule_config: str,
            description: str | None = None,
            input_data: str | None = None,
        ) -> dict[str, any]:
            """Create a new schedule for a workflow.

            Args:
                workflow_name: Name of the workflow to schedule
                name: Name for the schedule
                schedule_config: JSON string with schedule configuration (cron, interval, etc.)
                description: Optional description for the schedule
                input_data: Optional JSON string of input data for scheduled executions
            """
            try:
                # Parse schedule config
                try:
                    parsed_config = json.loads(schedule_config)
                except json.JSONDecodeError:
                    return {"success": False, "error": "Invalid JSON in schedule_config"}

                # Parse input data if provided
                parsed_input = None
                if input_data:
                    try:
                        parsed_input = json.loads(input_data)
                    except json.JSONDecodeError:
                        parsed_input = input_data

                async with httpx.AsyncClient(timeout=30.0) as client:
                    body = {
                        "workflow_name": workflow_name,
                        "name": name,
                        "schedule_config": parsed_config,
                    }
                    if description:
                        body["description"] = description
                    if parsed_input:
                        body["input_data"] = parsed_input

                    response = await client.post(
                        f"{self.server_url}/schedules",
                        json=body,
                    )
                    response.raise_for_status()
                    schedule = response.json()

                    logger.info(f"Created schedule '{name}' for workflow {workflow_name}")
                    return {"success": True, "schedule": schedule}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Workflow '{workflow_name}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error creating schedule: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def list_schedules(
            workflow_name: str | None = None,
            active_only: bool = False,
            limit: int | None = None,
            offset: int | None = None,
        ) -> dict[str, any]:
            """List schedules with optional filtering.

            Args:
                workflow_name: Optional workflow name to filter by
                active_only: If True, only return active schedules
                limit: Maximum number of schedules to return
                offset: Number of schedules to skip for pagination
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    params = {"active_only": active_only}
                    if workflow_name:
                        params["workflow_name"] = workflow_name
                    if limit is not None:
                        params["limit"] = limit
                    if offset is not None:
                        params["offset"] = offset

                    response = await client.get(
                        f"{self.server_url}/schedules",
                        params=params,
                    )
                    response.raise_for_status()
                    schedules = response.json()

                    logger.info(f"Retrieved {len(schedules)} schedules")
                    return {"success": True, "schedules": schedules, "count": len(schedules)}
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error listing schedules: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def get_schedule(schedule_id: str) -> dict[str, any]:
            """Get details of a specific schedule.

            Args:
                schedule_id: ID or name of the schedule to retrieve
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(f"{self.server_url}/schedules/{schedule_id}")
                    response.raise_for_status()
                    schedule = response.json()

                    logger.info(f"Retrieved schedule: {schedule_id}")
                    return {"success": True, "schedule": schedule}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Schedule '{schedule_id}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error retrieving schedule: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def update_schedule(
            schedule_id: str,
            schedule_config: str | None = None,
            description: str | None = None,
            input_data: str | None = None,
        ) -> dict[str, any]:
            """Update an existing schedule.

            Args:
                schedule_id: ID or name of the schedule to update
                schedule_config: Optional JSON string with new schedule configuration
                description: Optional new description
                input_data: Optional JSON string of new input data
            """
            try:
                body = {}

                if schedule_config:
                    try:
                        body["schedule_config"] = json.loads(schedule_config)
                    except json.JSONDecodeError:
                        return {"success": False, "error": "Invalid JSON in schedule_config"}

                if description is not None:
                    body["description"] = description

                if input_data:
                    try:
                        body["input_data"] = json.loads(input_data)
                    except json.JSONDecodeError:
                        body["input_data"] = input_data

                if not body:
                    return {"success": False, "error": "No update parameters provided"}

                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.put(
                        f"{self.server_url}/schedules/{schedule_id}",
                        json=body,
                    )
                    response.raise_for_status()
                    schedule = response.json()

                    logger.info(f"Updated schedule: {schedule_id}")
                    return {"success": True, "schedule": schedule}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Schedule '{schedule_id}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error updating schedule: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def pause_schedule(schedule_id: str) -> dict[str, any]:
            """Pause a schedule.

            Args:
                schedule_id: ID or name of the schedule to pause
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.server_url}/schedules/{schedule_id}/pause"
                    )
                    response.raise_for_status()
                    schedule = response.json()

                    logger.info(f"Paused schedule: {schedule_id}")
                    return {"success": True, "schedule": schedule}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Schedule '{schedule_id}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error pausing schedule: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def resume_schedule(schedule_id: str) -> dict[str, any]:
            """Resume a paused schedule.

            Args:
                schedule_id: ID or name of the schedule to resume
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.server_url}/schedules/{schedule_id}/resume"
                    )
                    response.raise_for_status()
                    schedule = response.json()

                    logger.info(f"Resumed schedule: {schedule_id}")
                    return {"success": True, "schedule": schedule}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Schedule '{schedule_id}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error resuming schedule: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def delete_schedule(schedule_id: str) -> dict[str, any]:
            """Delete a schedule.

            Args:
                schedule_id: ID or name of the schedule to delete
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.delete(
                        f"{self.server_url}/schedules/{schedule_id}"
                    )
                    response.raise_for_status()
                    result = response.json()

                    logger.info(f"Deleted schedule: {schedule_id}")
                    return {"success": True, "message": result.get("message", "Schedule deleted")}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Schedule '{schedule_id}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error deleting schedule: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        @self.mcp.tool()
        async def get_schedule_history(
            schedule_id: str,
            limit: int = 100,
            offset: int = 0,
        ) -> dict[str, any]:
            """Get execution history for a schedule.

            Args:
                schedule_id: ID or name of the schedule
                limit: Maximum number of history entries to return
                offset: Number of entries to skip for pagination
            """
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(
                        f"{self.server_url}/schedules/{schedule_id}/history",
                        params={"limit": limit, "offset": offset},
                    )
                    response.raise_for_status()
                    result = response.json()

                    entries = result.get("entries", [])
                    logger.info(
                        f"Retrieved {len(entries)} history entries for schedule {schedule_id}"
                    )
                    return {
                        "success": True,
                        "schedule_id": result.get("schedule_id"),
                        "workflow_name": result.get("workflow_name"),
                        "entries": entries,
                        "total": result.get("total", len(entries)),
                        "limit": limit,
                        "offset": offset,
                    }
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    error_msg = f"Schedule '{schedule_id}' not found"
                else:
                    error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Error retrieving schedule history: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        # Health Check Tool
        @self.mcp.tool()
        async def health_check() -> dict[str, any]:
            """Check the health status of the Flux server."""
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(f"{self.server_url}/health")
                    response.raise_for_status()
                    health = response.json()

                    logger.info(f"Health check: {health.get('status', 'unknown')}")
                    return {"success": True, "health": health}
            except httpx.ConnectError:
                error_msg = f"Could not connect to Flux server at {self.server_url}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP error: {e.response.status_code} - {e.response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}


if __name__ == "__main__":
    config = Configuration.get().settings.mcp
    MCPServer(
        name=config.name,
        host=config.host,
        port=config.port,
        server_url=config.server_url,
        transport=config.transport,
    ).start()
