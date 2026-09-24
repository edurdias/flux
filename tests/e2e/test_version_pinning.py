"""A paused execution must resume its registered version after a deployment."""

from __future__ import annotations


def test_resume_keeps_original_workflow_version(cli, tmp_path):
    source = tmp_path / "version_pinning.py"
    template = """from flux import workflow
from flux.tasks import pause

@workflow
async def version_pinning_e2e(ctx):
    await pause("deployment-gate")
    return "VERSION"
"""
    source.write_text(template.replace("VERSION", "v1"))
    cli.register(str(source))
    original = cli.run("version_pinning_e2e", mode="async")
    execution_id = original["execution_id"]
    cli.wait_for_state("version_pinning_e2e", execution_id, "PAUSED", timeout=30)

    source.write_text(template.replace("VERSION", "v2"))
    cli.register(str(source))
    cli.resume("version_pinning_e2e", execution_id)
    result = cli.wait_for_state("version_pinning_e2e", execution_id, "COMPLETED", timeout=30)
    assert result["output"] == "v1"
