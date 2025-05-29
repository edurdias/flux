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


if __name__ == "__main__":
    config = Configuration.get().settings.mcp
    MCPServer(
        name=config.name,
        host=config.host,
        port=config.port,
        server_url=config.server_url,
        transport=config.transport,
    ).start()
