"""The web console's pure text helpers, run for real (#245).

`console.js` cannot be imported outside a browser -- it touches `document`
at module scope -- which is why this repo pins its behavior with
source-level regexes. The helpers in `web/text.js` are the part that does
not need a DOM, so they are exercised directly instead, and compared
against the server-side implementations they mirror.

Skipped when node is unavailable; nothing in the build depends on it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from flux.agents.console.titles import truncate_title

PROBE = Path(__file__).parent / "text_probe.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not installed; the JS helpers are pinned by source guards instead",
)


def _run(cases: list[dict]) -> list:
    result = subprocess.run(
        ["node", str(PROBE), json.dumps(cases)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


TITLE_CASES = [
    "Please refactor the authentication module to use JWT tokens instead of cookies",
    "Investigate the flaky test suite failures in CI before shipping the release",
    "x" * 60,
    "short enough",
    "word " * 40,
    "Fix\tthe build now",
]


def test_client_truncation_matches_the_servers_character_for_character():
    """The bug this closes: the client hard-cut at 48 while the server cut on
    a word boundary, so the same title changed shape the moment a poll
    replaced the locally derived one with the server's."""
    cases = [{"op": "truncate", "text": text, "limit": 48} for text in TITLE_CASES]

    assert _run(cases) == [truncate_title(text, 48) for text in TITLE_CASES]


def test_truncation_matches_at_the_other_limits_the_console_uses():
    # The rail row (28) and the inline args line (64) call the same helper.
    for limit in (28, 64):
        cases = [{"op": "truncate", "text": text, "limit": limit} for text in TITLE_CASES]
        assert _run(cases) == [truncate_title(text, limit) for text in TITLE_CASES]


def test_a_truncated_title_never_exceeds_its_limit():
    for limit in (8, 28, 48, 64):
        results = _run([{"op": "truncate", "text": text, "limit": limit} for text in TITLE_CASES])
        assert all(len(result) <= limit for result in results), (limit, results)


def test_the_output_cut_is_measured_in_the_same_unit_as_its_size_label():
    """The label counts UTF-8 bytes; the cut used to count UTF-16 units, so a
    non-ASCII payload was cut at several times the advertised budget."""
    text = "héllo wörld " * 100
    (cut, size) = _run(
        [
            {"op": "sliceBytes", "text": text, "limit": 2048},
            {"op": "byteSize", "text": text},
        ],
    )

    assert len(cut.encode()) <= 2048
    assert size == len(text.encode())
    # Cut on a character boundary -- never a lone replacement char.
    assert "�" not in cut


def test_a_multibyte_character_is_never_split_by_the_cut():
    for limit in range(1, 12):
        (cut,) = _run([{"op": "sliceBytes", "text": "日本語テキスト", "limit": limit}])
        assert len(cut.encode()) <= limit
        assert "�" not in cut


def test_a_replacement_character_in_the_payload_survives_the_cut():
    """The cut walks back over UTF-8 continuation bytes rather than decoding
    and stripping U+FFFD -- stripping would also eat a replacement character
    the payload genuinely contained, which is real content silently lost."""
    text = "ok\ufffd" + "x" * 100
    (cut,) = _run([{"op": "sliceBytes", "text": text, "limit": 5}])

    # "ok" is 2 bytes, U+FFFD is 3: exactly the 5 asked for, kept whole.
    assert cut == "ok\ufffd"


def test_text_under_the_limit_is_returned_whole():
    (cut,) = _run([{"op": "sliceBytes", "text": "short", "limit": 2048}])
    assert cut == "short"


def test_size_labels_report_bytes_not_characters():
    (ascii_label, accented_label) = _run(
        [
            {"op": "formatSize", "text": "x" * 2048},
            {"op": "formatSize", "text": "é" * 2048},
        ],
    )

    assert ascii_label == "2.0 KB"
    # The same character count, twice the bytes -- and the label says so.
    assert accented_label == "4.0 KB"
