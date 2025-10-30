"""
Simple test workflow to debug DataFrame serialization across pause/resume.
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
    """Load CSV file."""
    print(f"Loading CSV from: {file_path}")
    df = pd.read_csv(file_path)
    print(f"Loaded {len(df)} rows, {len(df.columns)} columns")
    return df


@task.with_options(output_storage=file_storage)
async def process_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """Process the DataFrame."""
    print(f"Processing DataFrame: {df.shape}")
    result = {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": list(df.columns),
        "first_row": df.head(1).to_dict(orient="records")[0] if len(df) > 0 else {},
    }
    print(f"Processing complete: {result}")
    return result


@workflow.with_options(name="test_dataframe_no_pause")
async def test_dataframe_no_pause(ctx: ExecutionContext[dict[str, str]]):
    """Test workflow WITHOUT pause - should work fine."""
    file_path = ctx.input.get("file_path")

    # Load CSV
    df = await load_csv(file_path)

    # Process it
    result = await process_dataframe(df)

    return {
        "status": "success",
        "result": result,
        "execution_id": ctx.execution_id,
    }


@workflow.with_options(name="test_dataframe_with_pause")
async def test_dataframe_with_pause(ctx: ExecutionContext[dict[str, str]]):
    """Test workflow WITH pause - validates DataFrame integrity across pause/resume."""
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

    print(
        f"Before pause - Shape: {df.shape}, First product: {before_pause_values['first_row_product']}, Total revenue: {before_pause_values['sum_revenue']}",
    )

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

    print(
        f"After pause - Shape: {df.shape}, First product: {after_pause_values['first_row_product']}, Total revenue: {after_pause_values['sum_revenue']}",
    )

    # Verify all values match
    integrity_check = all(
        before_pause_values[k] == after_pause_values[k] for k in before_pause_values.keys()
    )

    # Try to process it after resume
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


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    async def test():
        print("=" * 60)
        print("TEST 1: No pause (should work)")
        print("=" * 60)

        result1 = test_dataframe_no_pause.run(
            {"file_path": "examples/ai/sample_data/sales_data.csv"},
        )

        if result1.has_failed:
            print(f"FAILED: {result1.output}")
        else:
            print(f"SUCCESS: {result1.output}")

        print("\n" + "=" * 60)
        print("TEST 2: With pause (testing DataFrame preservation)")
        print("=" * 60)

        result2 = test_dataframe_with_pause.run(
            {"file_path": "examples/ai/sample_data/sales_data.csv"},
        )

        if result2.has_failed:
            print(f"FAILED after initial run: {result2.output}")
        else:
            print(f"Paused successfully: {result2.state}")
            print(f"Execution ID: {result2.execution_id}")

            # Now resume
            print("\nResuming workflow...")
            result3 = test_dataframe_with_pause.resume(
                result2.execution_id,
                {"message": "continuing"},
            )

            if result3.has_failed:
                print(f"FAILED after resume: {result3.output}")
            else:
                print(f"SUCCESS after resume: {result3.output}")

    asyncio.run(test())
