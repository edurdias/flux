from __future__ import annotations

from typing import Any

from fastapi import Body
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query

from flux.catalogs import WorkflowCatalog
from flux.errors import ExecutionError
from flux.errors import WorkflowNotFoundError


def create_app(path: str):
    app = FastAPI()

    @app.post("/{workflow}", response_model=dict[str, Any])
    @app.post("/{workflow}/{execution_id}", response_model=dict[str, Any])
    async def execute(
        workflow: str,
        execution_id: str | None = None,
        input: Any = Body(default=None),
        inspect: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            wf = WorkflowCatalog.create({"path": path}).get(workflow).code
            context = await wf.run(input, execution_id)
            return context.summary() if not inspect else context.to_dict()

        except WorkflowNotFoundError as ex:
            raise HTTPException(status_code=404, detail=ex.message)
        except ExecutionError as ex:
            raise HTTPException(status_code=404, detail=ex.message)
        except Exception as ex:
            raise HTTPException(status_code=500, detail=str(ex))

    return app
