"""
Example workflow demonstrating DataFrame serialization with pause/resume.

This example shows how to use LocalFileStorage to properly serialize
pandas DataFrames across workflow pause/resume cycles, ensuring data
integrity is maintained.

Usage:
    flux workflow run dataframe_with_pause '{"file_path": "path/to/data.csv"}'
    flux workflow resume dataframe_with_pause <execution_id> '{}'
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from flux import ExecutionContext, task, workflow
from flux.output_storage import LocalFileStorage
from flux.tasks import pause

file_storage = LocalFileStorage()


@task.with_options(output_storage=file_storage)
async def load_csv(file_path: str) -> pd.DataFrame:
    """Load CSV file into a DataFrame."""
    return pd.read_csv(file_path)


@task.with_options(output_storage=file_storage)
async def process_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """Process the DataFrame and return summary statistics."""
    return {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": list(df.columns),
        "first_row": df.head(1).to_dict(orient="records")[0] if len(df) > 0 else {},
    }


@workflow.with_options(name="dataframe_with_pause")
async def dataframe_with_pause(ctx: ExecutionContext[dict[str, str]]):
    """
    Workflow that validates DataFrame integrity across pause/resume.

    This workflow demonstrates that DataFrames are properly preserved
    when using LocalFileStorage with pause/resume functionality.
    """
    file_path = ctx.input.get("file_path")

    # Load CSV
    df = await load_csv(file_path)

    # Capture values before pause to validate integrity after resume
    before_pause_values = {
        "shape": df.shape,
        "first_row_product": df.iloc[0]["product"],
        "first_row_revenue": float(df.iloc[0]["revenue"]),
        "first_row_quantity": int(df.iloc[0]["quantity"]),
        "total_rows": len(df),
        "column_count": len(df.columns),
        "sum_revenue": float(df["revenue"].sum()),
    }

    # Pause here - DataFrame needs to be preserved
    resume_input = await pause("after_load")

    # Validate DataFrame was correctly restored
    after_pause_values = {
        "shape": df.shape,
        "first_row_product": df.iloc[0]["product"],
        "first_row_revenue": float(df.iloc[0]["revenue"]),
        "first_row_quantity": int(df.iloc[0]["quantity"]),
        "total_rows": len(df),
        "column_count": len(df.columns),
        "sum_revenue": float(df["revenue"].sum()),
    }

    # Verify all values match
    integrity_check = all(
        before_pause_values[k] == after_pause_values[k] for k in before_pause_values.keys()
    )

    # Process it after resume
    result = await process_dataframe(df)

    return {
        "status": "success" if integrity_check else "data_integrity_failed",
        "result": result,
        "before_pause": before_pause_values,
        "after_pause": after_pause_values,
        "integrity_check": integrity_check,
        "execution_id": ctx.execution_id,
        "resumed_with": resume_input,
    }
