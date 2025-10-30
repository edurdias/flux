"""
Tests for DataFrame serialization across pause/resume.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from examples.dataframe_with_pause import dataframe_with_pause


def test_dataframe_integrity_across_pause():
    """Test that DataFrame values are preserved across pause/resume."""
    # Create a temporary CSV file with test data
    test_data = pd.DataFrame(
        {
            "product": ["Monitor 27inch", "HD Webcam", "Portable SSD 1TB"],
            "quantity": [8, 27, 42],
            "revenue": [3199.92, 2429.73, 5459.58],
            "cost": [2000.0, 1215.0, 3360.0],
        },
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        test_data.to_csv(f.name, index=False)
        temp_file = f.name

    try:
        # Run workflow to pause
        ctx = dataframe_with_pause.run({"file_path": temp_file})
        assert ctx.is_paused, "Workflow should be paused"

        # Resume workflow
        ctx = dataframe_with_pause.run(execution_id=ctx.execution_id)
        assert ctx.has_succeeded, "Workflow should have succeeded"

        # Validate integrity
        assert ctx.output["integrity_check"] is True, "DataFrame integrity check should pass"
        assert ctx.output["status"] == "success", "Status should be success"

        # Validate specific values
        assert ctx.output["before_pause"]["shape"] == (3, 4)
        assert ctx.output["after_pause"]["shape"] == (3, 4)
        assert ctx.output["before_pause"]["first_row_product"] == "Monitor 27inch"
        assert ctx.output["after_pause"]["first_row_product"] == "Monitor 27inch"
        assert abs(ctx.output["before_pause"]["sum_revenue"] - 11089.23) < 0.01
        assert abs(ctx.output["after_pause"]["sum_revenue"] - 11089.23) < 0.01

    finally:
        # Clean up temp file
        Path(temp_file).unlink()
