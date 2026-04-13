"""E2E tests — pipelines, output storage, resource requests, secrets."""
from __future__ import annotations

import pytest

import psutil


def test_complex_pipeline(cli):
    cli.register("examples/complex_pipeline.py")
    r = cli.run_async_and_wait(
        "complex_pipeline",
        '{"input_file":"examples/data/sample.csv","output_file":".data/e2e_output.csv"}',
        timeout=180,
    )
    assert r["state"] == "COMPLETED"


def test_output_storage(cli):
    cli.register("examples/output_storage.py")
    r = cli.run("output_storage", '"examples/data/sample.csv"')
    assert r["state"] == "COMPLETED"


_TOTAL_GB = psutil.virtual_memory().total / (1024**3)


@pytest.mark.skipif(
    _TOTAL_GB < 16,
    reason=f"requires >=16GB RAM for resource matching (have {_TOTAL_GB:.0f}GB)",
)
def test_resource_requests_data(cli):
    cli.register("examples/resource_requests.py")
    r = cli.run_async_and_wait(
        "data_processing_workflow",
        '{"data_path":"examples/data/sample.csv"}',
        timeout=180,
    )
    assert r["state"] == "COMPLETED"


def test_secrets(cli):
    cli.secrets_set("example", "super secret")
    cli.register("examples/using_secrets.py")
    r = cli.run("using_secrets", "null")
    assert r["state"] == "COMPLETED"
