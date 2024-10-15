from typing import Any, Dict, Optional
from fastapi import Body, FastAPI, HTTPException, Query
import uvicorn

from flux.exceptions import ExecutionException, WorkflowNotFoundException
from flux.executors import WorkflowExecutor


app = FastAPI()


@app.post("/execute/{workflow}", response_model=Dict[str, Any])
@app.post("/execute/{workflow}/{execution_id}", response_model=Dict[str, Any])
async def execute(
    workflow: str,
    execution_id: Optional[str] = None,
    input: Any = Body(),
    inspect: bool = Query(default=False),
) -> Dict[str, Any]:
    try:

        executor = WorkflowExecutor.current({"module": "examples"})
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


def run():
    uvicorn.run("flux.server:app", reload=True)
