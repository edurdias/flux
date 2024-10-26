from __future__ import annotations

import numpy as np
import pandas as pd

from flux.context import WorkflowExecutionContext
from flux.decorators import task
from flux.decorators import workflow
from flux.tasks import parallel
from flux.tasks import pipeline


@task
def load_data(file_name: str) -> pd.DataFrame:
    return pd.read_csv(file_name)


@task
def split_data(df: pd.DataFrame) -> list[pd.DataFrame]:
    return np.array_split(df, 10)


@task
def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop("email", axis=1)


@task
def process_data(dfs: list[pd.DataFrame]):
    tasks = [lambda: clean_data(df) for df in dfs]
    results = yield parallel(*tasks)
    return results


@task
def join_data(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(dfs, ignore_index=True)


@task
def save_data(df: pd.DataFrame, file_name: str):
    df.to_csv(file_name)


@workflow
def complex_pipeline(ctx: WorkflowExecutionContext[dict[str, str]]):
    yield pipeline(
        load_data,
        split_data,
        process_data,
        join_data,
        lambda df: save_data(df, ctx.input["output_file"]),
        input=ctx.input["input_file"],
    )


if __name__ == "__main__":  # pragma: no cover
    input = {
        "input_file": "examples/data/sample.csv",
        "output_file": ".data/sample_output.csv",
    }

    ctx = complex_pipeline.run(input)
    print(ctx.to_json())
