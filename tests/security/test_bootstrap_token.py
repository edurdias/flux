"""Tests for the bootstrap token resolver and persistence helpers."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from flux.security import bootstrap_token as bt


def test_generate_returns_64_char_hex():
    token = bt.generate()
    assert isinstance(token, str)
    assert len(token) == 64
    int(token, 16)


def test_generate_returns_unique_values():
    assert bt.generate() != bt.generate()


def test_read_persisted_returns_none_when_missing(tmp_path: Path):
    assert bt.read_persisted(tmp_path) is None


def test_write_creates_file_with_0600_mode(tmp_path: Path):
    path = bt.write(tmp_path, "secret-token")
    assert path.read_text() == "secret-token"
    if os.name != "nt":
        mode = path.stat().st_mode & 0o777
        assert mode == stat.S_IRUSR | stat.S_IWUSR, f"expected 0600, got {oct(mode)}"


def test_write_creates_parent_directory(tmp_path: Path):
    nested = tmp_path / "a" / "b" / "c"
    bt.write(nested, "x")
    assert (nested / bt.TOKEN_FILENAME).read_text() == "x"


def test_read_persisted_round_trips_write(tmp_path: Path):
    bt.write(tmp_path, "round-trip-token")
    assert bt.read_persisted(tmp_path) == "round-trip-token"


def test_read_persisted_strips_whitespace(tmp_path: Path):
    bt.write(tmp_path, "whitespace-token\n  ")
    assert bt.read_persisted(tmp_path) == "whitespace-token"


def test_read_persisted_returns_none_for_empty_file(tmp_path: Path):
    bt.write(tmp_path, "")
    assert bt.read_persisted(tmp_path) is None


def test_resolve_or_generate_uses_configured(tmp_path: Path):
    token, generated = bt.resolve_or_generate(tmp_path, configured="explicit-token")
    assert token == "explicit-token"
    assert generated is False
    assert not (
        tmp_path / bt.TOKEN_FILENAME
    ).exists(), "configured value must NOT trigger file generation"


def test_resolve_or_generate_reads_persisted_when_no_configured(tmp_path: Path):
    bt.write(tmp_path, "persisted-token")
    token, generated = bt.resolve_or_generate(tmp_path, configured=None)
    assert token == "persisted-token"
    assert generated is False


def test_resolve_or_generate_creates_when_neither_configured_nor_persisted(tmp_path: Path):
    token, generated = bt.resolve_or_generate(tmp_path, configured=None)
    assert generated is True
    assert len(token) == 64
    assert (tmp_path / bt.TOKEN_FILENAME).read_text() == token


def test_resolve_or_generate_treats_empty_string_configured_as_unset(tmp_path: Path):
    token, generated = bt.resolve_or_generate(tmp_path, configured="")
    assert generated is True


def test_rotate_writes_new_token_and_returns_it(tmp_path: Path):
    bt.write(tmp_path, "original")
    new = bt.rotate(tmp_path)
    assert new != "original"
    assert len(new) == 64
    assert bt.read_persisted(tmp_path) == new


def test_resolve_or_generate_logs_warning_on_first_generation(tmp_path: Path, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="flux.security.bootstrap_token"):
        bt.resolve_or_generate(tmp_path, configured=None)
    assert any(
        "Generated bootstrap token" in record.message for record in caplog.records
    ), f"expected a WARNING about generation; got: {[r.message for r in caplog.records]}"


def test_resolve_or_generate_does_not_log_when_using_configured(tmp_path: Path, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="flux.security.bootstrap_token"):
        bt.resolve_or_generate(tmp_path, configured="op-supplied")
    assert not any("Generated bootstrap token" in record.message for record in caplog.records)


@pytest.mark.parametrize("home_kind", ["str", "Path"])
def test_resolve_or_generate_accepts_str_or_path(tmp_path: Path, home_kind: str):
    home = str(tmp_path) if home_kind == "str" else tmp_path
    token, _ = bt.resolve_or_generate(home, configured=None)
    assert token


def test_read_persisted_returns_none_for_whitespace_only_file(tmp_path: Path):
    bt.write(tmp_path, "   \n\t  ")
    assert bt.read_persisted(tmp_path) is None


def test_read_persisted_returns_token_with_internal_punctuation(tmp_path: Path):
    bt.write(tmp_path, "abc-123-def")
    assert bt.read_persisted(tmp_path) == "abc-123-def"


def test_write_clobbers_existing_file_and_resets_mode(tmp_path: Path):
    p = bt.write(tmp_path, "first")
    if os.name != "nt":
        p.chmod(0o644)
    bt.write(tmp_path, "second")
    assert p.read_text() == "second"
    if os.name != "nt":
        mode = p.stat().st_mode & 0o777
        assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_resolve_or_generate_creates_home_dir_if_missing(tmp_path: Path):
    nested = tmp_path / "does" / "not" / "exist"
    assert not nested.exists()
    token, generated = bt.resolve_or_generate(nested, configured=None)
    assert generated is True
    assert (nested / bt.TOKEN_FILENAME).read_text() == token


def test_resolve_or_generate_preserves_persisted_with_trailing_newline(tmp_path: Path):
    """Operators may hand-edit the file; trailing whitespace must be tolerated."""
    (tmp_path / bt.TOKEN_FILENAME).write_text("manual-token\n")
    token, generated = bt.resolve_or_generate(tmp_path, configured=None)
    assert token == "manual-token"
    assert generated is False


def test_resolve_or_generate_idempotent_when_persisted(tmp_path: Path):
    """Calling twice returns the same token; the file is not rewritten."""
    first, _ = bt.resolve_or_generate(tmp_path, configured=None)
    mtime1 = (tmp_path / bt.TOKEN_FILENAME).stat().st_mtime
    second, generated = bt.resolve_or_generate(tmp_path, configured=None)
    assert first == second
    assert generated is False
    assert (tmp_path / bt.TOKEN_FILENAME).stat().st_mtime == mtime1


def test_rotate_creates_home_dir_if_missing(tmp_path: Path):
    nested = tmp_path / "fresh"
    token = bt.rotate(nested)
    assert (nested / bt.TOKEN_FILENAME).read_text() == token


def test_rotate_yields_unique_tokens(tmp_path: Path):
    a = bt.rotate(tmp_path)
    b = bt.rotate(tmp_path)
    assert a != b
    assert bt.read_persisted(tmp_path) == b


def test_resolve_or_generate_then_rotate_then_resolve_uses_new_token(tmp_path: Path):
    """End-to-end: generate, rotate, resolve again — final value matches rotated."""
    bt.resolve_or_generate(tmp_path, configured=None)
    rotated = bt.rotate(tmp_path)
    final, generated = bt.resolve_or_generate(tmp_path, configured=None)
    assert final == rotated
    assert generated is False
