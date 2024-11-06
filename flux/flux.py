from __future__ import annotations

from typing import Any

import click
import uvicorn

from flux.api import create_app
from flux.config import Configuration
from flux.executors import WorkflowExecutor


@click.group()
def cli():
    pass


@cli.command()
@click.argument("path")
@click.argument("workflow")
@click.option("--input", "-i", help="Workflow input.")
@click.option("--execution-id", "-e", help="Execution ID for existing executions.")
def exec(path: str, workflow: str, input: Any, execution_id: str | None = None):
    """Execute the specified workflow"""

    executor = WorkflowExecutor.get({"path": path})
    print(executor.execute(workflow, input, execution_id).to_json())


@cli.command()
@click.argument("path")
def start(path: str):
    """Start the server to execute Workflows via API."""
    settings = Configuration.get().settings
    uvicorn.run(create_app(path), port=settings.api_port)


if __name__ == "__main__":  # pragma: no cover
    cli()
