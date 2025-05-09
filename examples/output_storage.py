from __future__ import annotations

import pandas as pd

from flux.context import WorkflowExecutionContext
from flux.decorators import task
from flux.decorators import workflow
from flux.output_storage import LocalFileStorage


file_storage = LocalFileStorage()


@task.with_options(output_storage=file_storage)
async def load_data(file_name: str) -> pd.DataFrame:
    return pd.read_csv(file_name)


@workflow.with_options(output_storage=file_storage)
async def output_storage(ctx: WorkflowExecutionContext[str]):
    if not ctx.input:
        raise TypeError("Input not provided")
    data = await load_data(ctx.input)
    return data


if __name__ == "__main__":  # pragma: no cover
    ctx = output_storage.run("examples/data/sample.csv")
    print(ctx.to_json())
