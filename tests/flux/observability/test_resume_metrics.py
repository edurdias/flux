"""Tests for resume-pipeline metric recorders."""

from __future__ import annotations


def test_record_resume_queued_callable():
    from flux.observability import get_metrics

    m = get_metrics()
    if m is not None:
        m.record_resume_queued("default", "test_wf")


def test_record_resume_scheduled_callable():
    from flux.observability import get_metrics

    m = get_metrics()
    if m is not None:
        m.record_resume_scheduled("default", "test_wf", 0.123)


def test_record_resume_claimed_callable():
    from flux.observability import get_metrics

    m = get_metrics()
    if m is not None:
        m.record_resume_claimed("default", "test_wf", 0.456)
