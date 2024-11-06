from __future__ import annotations

from typing import Any

from fastapi import Body
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query

from flux.context_managers import ContextManager
from flux.errors import ExecutionError
from flux.errors import WorkflowNotFoundError
from flux.executors import WorkflowExecutor


def create_app(path: str):
    app = FastAPI()

    executor = WorkflowExecutor.get({"path": path})
    context_manager = ContextManager.default()

    @app.post("/{workflow}", response_model=dict[str, Any])
    @app.post("/{workflow}/{execution_id}", response_model=dict[str, Any])
    async def execute(
        workflow: str,
        execution_id: str | None = None,
        input: Any = Body(default=None),
        inspect: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            context = executor.execute(
                execution_id=execution_id,
                name=workflow,
                input=input,
            )

            return context.summary() if not inspect else context.to_dict()

        except WorkflowNotFoundError as ex:
            raise HTTPException(status_code=404, detail=ex.message)
        except ExecutionError as ex:
            raise HTTPException(status_code=404, detail=ex.message)
        except Exception as ex:
            raise HTTPException(status_code=500, detail=str(ex))

    @app.get("/inspect/{execution_id}", response_model=dict[str, Any])
    async def inspect(execution_id: str) -> dict[str, Any]:
        try:
            context = context_manager.get(execution_id)
            if not context:
                raise HTTPException(
                    status_code=404,
                    detail=f"Execution '{execution_id}' not found!",
                )
            return context.to_dict()

        except HTTPException:
            raise
        except Exception as ex:
            raise HTTPException(status_code=500, detail=str(ex))

    return app
