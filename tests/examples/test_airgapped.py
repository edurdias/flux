"""Tests for the docker-airgapped examples.

Runner selection is a dispatch concern; inline execution runs the workflow
in the current process, so these verify the workflows themselves (including
the graceful fallback when no read-only asset mount is granted) and that
each carries the sealed-runner requirement.
"""

from __future__ import annotations

from examples.airgapped import sealed_keyword_count, sealed_summarize


def test_sealed_keyword_count_runs_inline():
    ctx = sealed_keyword_count.run("the quick brown fox jumps over the lazy dog")
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output["quick"] == 1
    assert "the" not in ctx.output  # stopwords excluded via the fallback list


def test_sealed_keyword_count_orders_by_frequency():
    ctx = sealed_keyword_count.run("data data data pipeline pipeline sealed")
    assert ctx.has_succeeded, ctx.output
    assert list(ctx.output) == ["data", "pipeline", "sealed"]


def test_sealed_summarize_runs_inline():
    text = "Flux executes untrusted workflows inside a sealed container whose only capability channel is the stdio protocol to the parent worker."
    ctx = sealed_summarize.run(text)
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output["summary"].endswith("…")
    assert ctx.output["input_words"] == len(text.split())


def test_examples_declare_the_sealed_runner():
    assert sealed_keyword_count.runner == "docker-airgapped"
    assert sealed_summarize.runner == "docker-airgapped"
