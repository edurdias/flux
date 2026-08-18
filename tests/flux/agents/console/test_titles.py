"""Tests for derived_title -- the first-user-message session-title heuristic."""

from __future__ import annotations

from flux.agents.console.titles import derived_title


def _resumed(message: str) -> dict:
    """A WORKFLOW_RESUMED event as it appears in a detailed execution read.

    ``resume()`` records the raw resume input verbatim as the event value
    (flux/domain/execution_context.py::resume); for the chat workflows every
    console session drives, that input is ``{"message": <text>}`` -- exactly
    what ``ConsoleService.send`` posts.
    """
    return {"type": "WORKFLOW_RESUMED", "value": {"message": message}}


def test_uses_first_user_message_unchanged_when_short():
    detail = {"events": [_resumed("Fix the flaky test")]}
    assert derived_title(detail) == "Fix the flaky test"


def test_strips_surrounding_whitespace():
    detail = {"events": [_resumed("   Fix the flaky test   ")]}
    assert derived_title(detail) == "Fix the flaky test"


def test_collapses_internal_newlines_into_single_spaces():
    """A title is drawn on one line -- the TUI rail gives it exactly one row,
    and a wrapped row reads as two sessions. A pasted multi-line first
    message must therefore come back single-line."""
    detail = {"events": [_resumed("Fix the build\nthen ship it")]}
    title = derived_title(detail)
    assert title == "Fix the build then ship it"
    assert "\n" not in title


def test_collapses_runs_of_whitespace_including_tabs():
    detail = {"events": [_resumed("  Fix\t\tthe   build \r\n now  ")]}
    assert derived_title(detail) == "Fix the build now"


def test_truncation_measures_the_collapsed_text():
    """Collapsing happens before truncation, so the 48-char budget is spent
    on words, not on the whitespace that separated them."""
    text = "Investigate the flaky test suite\n\n\n\nfailures in CI before shipping"
    detail = {"events": [_resumed(text)]}
    assert derived_title(detail) == "Investigate the flaky test suite failures in CI…"


def test_truncates_on_clean_word_boundary():
    text = "Investigate the flaky test suite failures in CI before shipping the release tomorrow"
    detail = {"events": [_resumed(text)]}
    assert derived_title(detail) == "Investigate the flaky test suite failures in CI…"


def test_backs_up_to_previous_word_when_the_cut_lands_mid_word():
    text = (
        "Refactor the authentication subsystem to properly support rotating "
        "credentials safely and quickly"
    )
    detail = {"events": [_resumed(text)]}
    assert derived_title(detail) == "Refactor the authentication subsystem to…"


def test_a_word_ending_on_the_budgets_last_column_yields_to_the_ellipsis():
    """ "...module to use" ends exactly at 48, but the ellipsis needs that
    column too, so the last whole word that still fits wins. The title is
    48 columns wide either way -- which is the point (#245)."""
    text = "Please refactor the authentication module to use JWT tokens instead of session cookies"
    detail = {"events": [_resumed(text)]}
    title = derived_title(detail)
    assert title == "Please refactor the authentication module to…"
    assert len(title) <= 48


def test_exactly_48_chars_is_not_truncated():
    text = "x" * 48
    detail = {"events": [_resumed(text)]}
    assert derived_title(detail) == text


def test_falls_back_to_a_hard_cut_when_the_first_word_has_no_boundary():
    text = "x" * 60  # one continuous run with no space to back up to
    detail = {"events": [_resumed(text)]}
    title = derived_title(detail)
    assert title == ("x" * 47) + "…"
    assert len(title) == 48


def test_skips_resumes_without_a_message_key():
    detail = {
        "events": [
            {"type": "WORKFLOW_RESUMED", "value": {"elicitation_response": {"action": "accept"}}},
            _resumed("actual first chat message"),
        ],
    }
    assert derived_title(detail) == "actual first chat message"


def test_returns_first_message_not_last():
    detail = {"events": [_resumed("first message"), _resumed("second message")]}
    assert derived_title(detail) == "first message"


def test_none_when_no_user_message_yet():
    detail = {
        "events": [
            {"type": "WORKFLOW_SCHEDULED", "value": None},
            {"type": "WORKFLOW_STARTED", "value": None},
        ],
    }
    assert derived_title(detail) is None


def test_none_for_empty_or_missing_event_log():
    assert derived_title({"events": []}) is None
    assert derived_title({}) is None


def test_is_deterministic():
    detail = {"events": [_resumed("Fix the flaky test")]}
    assert derived_title(detail) == derived_title(detail)


def test_a_truncated_title_never_exceeds_the_limit():
    """The ellipsis is part of the budget, not an extra column.

    A caller sizing a fixed-width row (the TUI rail) computes its padding
    from the limit it asked for; one column more wraps the row, and a
    client that hard-cuts at the same number renders a different shape
    than the server sent (#245).
    """
    from flux.agents.console.titles import truncate_title

    texts = [
        "Please refactor the authentication module to use JWT tokens instead of cookies",
        "Investigate the flaky test suite failures in CI before shipping the release",
        "x" * 200,
        "word " * 40,
        "supercalifragilisticexpialidocious " * 4,
    ]
    for text in texts:
        for limit in (8, 20, 47, 48, 49):
            assert len(truncate_title(text, limit)) <= limit, (text[:20], limit)


def test_truncation_still_prefers_a_word_boundary():
    from flux.agents.console.titles import truncate_title

    assert truncate_title("Refactor the authentication subsystem now", 20) == "Refactor the…"
