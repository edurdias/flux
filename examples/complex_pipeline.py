from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow
from flux.tasks import parallel
from flux.tasks import pipeline


@task
async def load_data(file_name: str) -> pd.DataFrame:
    if not Path(file_name).exists():
        raise FileNotFoundError(f"File not found: {file_name}")
    return pd.read_csv(file_name)


@task
async def split_data(df: pd.DataFrame) -> list[pd.DataFrame]:
    return np.array_split(df, 10)


@task
async def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop("email", axis=1)


@task
async def process_data(dfs: list[pd.DataFrame]):
    tasks = [clean_data(df) for df in dfs]
    results = await parallel(*tasks)
    return results


@task
async def join_data(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(dfs, ignore_index=True)


@task
async def save_data(df: pd.DataFrame, file_name: str):
    Path(file_name).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(file_name)
    return df


@task
async def analyze_data(df: pd.DataFrame):
    summary = {
        "shape": df.shape,
        "columns": df.columns.tolist(),
        "size": df.size,
        "total_elements": df.size,
        "memory_usage": df.memory_usage(deep=True).sum(),
        "dtypes": df.dtypes.apply(str).to_dict(),
        "null_counts": df.isnull().sum().to_dict(),
        "null_percentages": (df.isnull().mean() * 100).round(2).to_dict(),
        "numeric_stats": df.describe(include=[np.number]).to_dict(),
        "sample_values": df.head(1).to_dict(orient="records")[0],
        "unique_counts": df.nunique().to_dict(),
    }

    # Add categorical statistics if any exist
    categorical_stats = df.describe(include=["object", "category"])
    if not categorical_stats.empty:
        summary["categorical_stats"] = categorical_stats.to_dict()

    return summary


@workflow
async def complex_pipeline(ctx: ExecutionContext[dict[str, str]]):
    async def save(df: pd.DataFrame):
        return await save_data(df, ctx.input["output_file"])

    df = await pipeline(
        load_data,
        split_data,
        process_data,
        join_data,
        save,
        analyze_data,
        input=ctx.input["input_file"],
    )
    return df


if __name__ == "__main__":  # pragma: no cover
    input = {
        "input_file": "examples/data/sample.csv",
        "output_file": ".data/sample_output.csv",
    }

    ctx = complex_pipeline.run(input)
    print(ctx.to_json())
