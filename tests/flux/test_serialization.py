"""The value codec: msgpack first, dill only where it must be (#260).

dill executes arbitrary code on load. Every payload moved onto msgpack is
one that can no longer do that, so the codec's job is to cover as much of
what Flux actually persists as it can *without* reconstructing classes,
and to be loud about what it could not cover.

Two properties matter beyond speed: exact types survive a round trip (a
tuple that comes back a list breaks replay comparison), and blobs written
by earlier versions still read.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

import dill
import pytest

from flux.serialization import decode, encode, is_msgpack_payload


class _NotEncodable:
    """Stands in for a workflow's own class -- no handler can exist for it."""

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return isinstance(other, _NotEncodable) and other.value == self.value


class TestValueTypesRoundTrip:
    @pytest.mark.parametrize(
        "value",
        [
            "a string",
            b"\x00\x01bytes",
            42,
            3.5,
            True,
            None,
            {"a": 1, "b": [1, 2], "c": {"d": None}},
            [1, "two", 3.0],
        ],
    )
    def test_native_types(self, value):
        assert decode(encode(value)) == value

    @pytest.mark.parametrize(
        "value",
        [
            (1, 2, 3),
            {1, 2, 3},
            frozenset({1, 2}),
            datetime.datetime(2026, 8, 19, 12, 30, 45, 123456),
            datetime.datetime.now(datetime.UTC),
            datetime.date(2026, 8, 19),
            datetime.time(12, 30, 45),
            datetime.timedelta(days=2, seconds=30),
            uuid.uuid4(),
            decimal.Decimal("1.250"),
        ],
    )
    def test_types_msgpack_cannot_express_natively(self, value):
        """These are the ones a naive msgpack swap loses -- a tuple comes
        back a list, a datetime does not encode at all."""
        restored = decode(encode(value))

        assert restored == value
        assert type(restored) is type(value)

    @pytest.mark.parametrize(
        "value",
        [
            datetime.timedelta(days=100000, microseconds=1),
            datetime.timedelta(days=999999999, microseconds=999999),
            datetime.timedelta(days=-1, microseconds=1),
            datetime.timedelta(microseconds=1),
            datetime.timedelta(seconds=0.1),
        ],
    )
    def test_timedelta_keeps_its_microseconds_at_any_magnitude(self, value):
        """Encoded through total_seconds() these lost precision -- a float
        cannot hold a microsecond alongside 100,000 days, and the largest
        case rounded a microsecond up to a whole second. Replay compares
        event values, so a rounded one is a mismatch waiting to happen."""
        assert decode(encode(value)) == value

    def test_nesting_is_preserved(self):
        value = {
            "when": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            "ids": (uuid.uuid4(), uuid.uuid4()),
            "tags": {"a", "b"},
            "rows": [{"n": decimal.Decimal("0.1")}],
        }

        restored = decode(encode(value))

        assert restored == value
        assert type(restored["ids"]) is tuple
        assert type(restored["tags"]) is set


class TestDillFallback:
    def test_an_unencodable_object_still_round_trips(self):
        value = _NotEncodable("payload")

        with pytest.warns(UserWarning, match="_NotEncodable"):
            blob = encode(value)

        assert decode(blob) == value

    def test_the_warning_names_the_type_so_it_can_be_fixed(self):
        with pytest.warns(UserWarning, match=r"_NotEncodable.*dill"):
            encode(_NotEncodable(1))

    def test_only_the_unencodable_part_falls_back(self):
        """A custom object nested in an otherwise-msgpack payload must not
        drag the whole payload onto the unsafe path."""
        value = {"ok": [1, 2], "custom": _NotEncodable("x")}

        with pytest.warns(UserWarning):
            blob = encode(value)

        assert is_msgpack_payload(blob)
        assert decode(blob) == value


class TestDualRead:
    def test_a_legacy_dill_blob_still_decodes(self):
        """Every deployment has rows written before this codec existed."""
        legacy = dill.dumps({"written": "by the old path", "n": 1})

        assert not is_msgpack_payload(legacy)
        assert decode(legacy) == {"written": "by the old path", "n": 1}

    def test_new_payloads_are_not_pickle_streams(self):
        """The tag has to be distinguishable from a pickle stream, which
        always starts with the PROTO opcode 0x80."""
        blob = encode({"a": 1})

        assert is_msgpack_payload(blob)
        assert blob[:1] != b"\x80"

    def test_a_legacy_blob_of_a_custom_class_still_decodes(self):
        legacy = dill.dumps(_NotEncodable("old"))

        assert decode(legacy) == _NotEncodable("old")


class TestDillIsOffTheEncodablePath:
    def test_encoding_a_value_type_does_not_import_dill(self):
        """The point of the codec: a payload it can express never pulls in
        the module that executes code on load. Checked in a subprocess
        because dill is already imported by the time this test runs -- other
        call sites still use it (flux/models.py, output_storage, cache), so
        this is a property of the codec, not yet of the process."""
        import subprocess
        import sys

        probe = (
            "import sys, datetime, uuid;"
            "from flux.serialization import encode, decode;"
            "blob = encode({'a': (1, 2), 'when': datetime.datetime.now(), 'id': uuid.uuid4()});"
            "decode(blob);"
            "print('dill' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False", (
            "encoding a msgpack-expressible payload imported dill"
        )

    def test_falling_back_does_import_dill(self):
        """The mirror: the fallback really is the unsafe path, and the
        warning is what tells an operator they are on it."""
        import subprocess
        import sys

        # complex() has no handler and is not msgpack-native, so it takes
        # the fallback -- a one-expression stand-in for a workflow's own class.
        probe = (
            "import sys, warnings;"
            "warnings.simplefilter('ignore');"
            "from flux.serialization import encode, decode;"
            "assert decode(encode(complex(1, 2))) == complex(1, 2);"
            "print('dill' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True"
