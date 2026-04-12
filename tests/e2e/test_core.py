"""E2E tests — core workflow examples."""
from __future__ import annotations


def test_hello_world(cli):
    cli.register("examples/hello_world.py")
    r = cli.run("hello_world", '"Joe"')
    assert r["state"] == "COMPLETED"
    assert r["output"] == "Hello, Joe"


def test_simple_pipeline(cli):
    cli.register("examples/simple_pipeline.py")
    r = cli.run("simple_pipeline", "5")
    assert r["state"] == "COMPLETED"


def test_parallel_tasks(cli):
    cli.register("examples/parallel_tasks.py")
    r = cli.run("parallel_tasks_workflow", '"Joe"')
    assert r["state"] == "COMPLETED"


def test_nested_tasks(cli):
    cli.register("examples/nested_tasks.py")
    r = cli.run("nested_tasks_workflow", "null")
    assert r["state"] == "COMPLETED"


def test_determinism(cli):
    cli.register("examples/determinism.py")
    r = cli.run("determinism", "null")
    assert r["state"] == "COMPLETED"


def test_sleep(cli):
    cli.register("examples/sleep.py")
    r = cli.run("sleep_workflow", "null", timeout=45)
    assert r["state"] == "COMPLETED"


def test_graph(cli):
    cli.register("examples/graph/simple_graph.py")
    r = cli.run("simple_graph", '"Joe"')
    assert r["state"] == "COMPLETED"


def test_fibo_benchmark(cli):
    cli.register("examples/fibo_benchmark.py")
    r = cli.run("fibo_benchmark", "[10,33]", timeout=120)
    assert r["state"] == "COMPLETED"


def test_workflow_versions(cli):
    # hello_world already registered; register again to create a new version
    cli.register("examples/hello_world.py")
    versions = cli.versions("hello_world")
    assert len(versions) >= 2
