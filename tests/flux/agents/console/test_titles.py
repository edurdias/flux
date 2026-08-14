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


def test_keeps_a_word_that_ends_exactly_at_the_limit():
    text = "Please refactor the authentication module to use JWT tokens instead of session cookies"
    detail = {"events": [_resumed(text)]}
    assert derived_title(detail) == "Please refactor the authentication module to use…"


def test_exactly_48_chars_is_not_truncated():
    text = "x" * 48
    detail = {"events": [_resumed(text)]}
    assert derived_title(detail) == text


def test_falls_back_to_a_hard_cut_when_the_first_word_has_no_boundary():
    text = "x" * 60  # one continuous run with no space to back up to
    detail = {"events": [_resumed(text)]}
    assert derived_title(detail) == ("x" * 48) + "…"


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
