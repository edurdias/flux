import importlib.util
from typing import Any, Dict, Optional
import click
from fastapi import Body, FastAPI, HTTPException, Query
import uvicorn

from flux.context_managers import ContextManager
from flux.exceptions import ExecutionException, WorkflowNotFoundException
from flux.executors import WorkflowExecutor


@click.group()
def cli():
    pass


@cli.command()
@click.argument("file_path")
@click.argument("workflow")
@click.option("--input", "-i", help="Workflow input.")
@click.option("--execution-id", "-e", help="Execution ID for existing executions.")
def exec(file_path: str, workflow: str, input: any, execution_id: str = None):
    """Execute the specified workflow"""

    executor = WorkflowExecutor.current({"file_path": file_path})
    print(executor.execute(workflow, input, execution_id).to_json())

@cli.command()
@click.argument("path")
def start(path: str):
    """Start the server to execute Workflows via API."""

    app = FastAPI()
    @app.post("/{workflow}", response_model=Dict[str, Any])
    @app.post("/{workflow}/{execution_id}", response_model=Dict[str, Any])
    async def execute(
        workflow: str,
        execution_id: Optional[str] = None,
        input: Any = Body(default=None),
        inspect: bool = Query(default=False),
    ) -> Dict[str, Any]:
        try:
            executor = WorkflowExecutor.current({"module": path})
            context = executor.execute(
                execution_id=execution_id,
                name=workflow,
                input=input,
            )

            return context.summary() if not inspect else context.to_dict()

        except WorkflowNotFoundException as ex:
            raise HTTPException(status_code=404, detail=ex.message)
        except ExecutionException as ex:
            raise HTTPException(status_code=404, detail=ex.message)
        except Exception as ex:
            raise HTTPException(status_code=500, detail=str(ex))

    @app.get("/inspect/{execution_id}", response_model=Dict[str, Any])
    async def execute(execution_id: str) -> Dict[str, Any]:
        try:

            context = ContextManager.default().get(execution_id)
            if not context:
                raise HTTPException(
                    status_code=404, detail=f"Execution '{execution_id}' not found!"
                )
            return context.to_dict()

        except HTTPException:
            raise
        except Exception as ex:
            raise HTTPException(status_code=500, detail=str(ex))

    uvicorn.run(app)


if __name__ == "__main__":
    cli()
