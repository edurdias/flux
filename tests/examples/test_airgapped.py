"""Tests for the docker-airgapped examples.

Runner selection is a dispatch concern; inline execution runs the workflow
in the current process, so these verify the workflows themselves (including
the graceful fallback when no read-only asset mount is granted) and that
each carries the sealed-runner requirement.
"""

from __future__ import annotations

from examples.airgapped import sealed_keyword_count, sealed_redact


def test_sealed_keyword_count_runs_inline():
    ctx = sealed_keyword_count.run("the quick brown fox jumps over the lazy dog")
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output["quick"] == 1
    assert "the" not in ctx.output  # stopwords excluded via the fallback list


def test_sealed_keyword_count_orders_by_frequency():
    ctx = sealed_keyword_count.run("data data data pipeline pipeline sealed")
    assert ctx.has_succeeded, ctx.output
    assert list(ctx.output) == ["data", "pipeline", "sealed"]


def test_sealed_redact_masks_pii():
    ctx = sealed_redact.run("Contact Ada at ada@example.com or +1 (555) 010-9999 for access.")
    assert ctx.has_finished and ctx.has_succeeded
    assert "ada@example.com" not in ctx.output["text"]
    assert "555" not in ctx.output["text"]
    assert ctx.output["redactions"] == 2


def test_sealed_redact_clean_text_untouched():
    ctx = sealed_redact.run("Nothing sensitive here.")
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == {"text": "Nothing sensitive here.", "redactions": 0}


def test_sealed_classify_falls_back_without_the_service():
    from examples.airgapped import sealed_classify

    ctx = sealed_classify.run("Is this sealed?")
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output == {"label": "question", "via": "fallback"}


def test_examples_declare_the_sealed_runner():
    from examples.airgapped import sealed_classify

    assert sealed_keyword_count.runner == "docker-airgapped"
    assert sealed_redact.runner == "docker-airgapped"
    assert sealed_classify.runner == "docker-airgapped"
    assert sealed_classify.affinity == {"flux.service.classifier": "true"}
