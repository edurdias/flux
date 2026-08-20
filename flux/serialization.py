"""Value codec for persisted state and event payloads (#260).

``dill`` executes arbitrary code on load. Everything Flux persists as a
runtime value -- execution input and output, event values, schedule input
-- has historically gone through it, which makes the HMAC in
``flux.security.integrity`` the only thing standing between a tampered row
and code execution. Every payload this codec can express in msgpack is one
that no longer needs that guarantee to be safe.

**What it covers.** msgpack's own types, plus the value types it cannot
express natively -- tuple, set, frozenset, datetime/date/time/timedelta,
UUID, Decimal -- carried as tagged extensions so an exact type survives a
round trip. That matters beyond tidiness: a tuple that comes back a list
changes an event value, and replay compares event values.

**What it does not.** Class instances -- Pydantic models, dataclasses,
exceptions, a workflow's own types -- are *not* reconstructed by importing
their module and rebuilding them, because "decode looks up a name and
imports it" is the property that makes deserialization dangerous in the
first place. They fall back to dill, nested precisely where they appear
(a custom object inside a dict does not drag the dict onto the fallback),
and each fallback warns with the type that caused it, so an operator can
see what is still on the unsafe path.

dill is imported lazily, inside the two branches that need it, so a
payload this codec can express never pulls the unsafe module in on its
account. (Other call sites still import it -- see the module list in the
#260 PR -- so this is a property of the codec, not yet of the process.)

**Reading.** Blobs written before this module are raw dill streams, which
always begin with pickle's PROTO opcode (0x80). The msgpack payloads carry
a tag that deliberately cannot start that way, so old and new rows are
distinguishable without a schema change or a migration.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
import warnings
from typing import Any

import msgpack

# Chosen so it can never be mistaken for a pickle stream: every dill blob
# begins with 0x80 (PROTO). Bumping the second byte is how a future format
# change stays readable alongside this one.
_TAG = b"\xfa\x01"

_EXT_TUPLE = 1
_EXT_SET = 2
_EXT_FROZENSET = 3
_EXT_DATETIME = 4
_EXT_DATE = 5
_EXT_TIME = 6
_EXT_TIMEDELTA = 7
_EXT_UUID = 8
_EXT_DECIMAL = 9
# The documented unsafe path: a value no handler covers, dill-encoded in
# place rather than forcing the whole payload onto dill.
_EXT_DILL = 127


def is_msgpack_payload(blob: bytes) -> bool:
    """Whether ``blob`` was written by this codec (rather than raw dill)."""
    return blob[: len(_TAG)] == _TAG


def _default(value: Any) -> msgpack.ExtType:
    if isinstance(value, tuple):
        return msgpack.ExtType(_EXT_TUPLE, encode_body(list(value)))
    if isinstance(value, frozenset):
        return msgpack.ExtType(_EXT_FROZENSET, encode_body(list(value)))
    if isinstance(value, set):
        return msgpack.ExtType(_EXT_SET, encode_body(list(value)))
    if isinstance(value, datetime.datetime):
        return msgpack.ExtType(_EXT_DATETIME, value.isoformat().encode())
    if isinstance(value, datetime.date):
        return msgpack.ExtType(_EXT_DATE, value.isoformat().encode())
    if isinstance(value, datetime.time):
        return msgpack.ExtType(_EXT_TIME, value.isoformat().encode())
    if isinstance(value, datetime.timedelta):
        # (days, seconds, microseconds) -- timedelta's own exact
        # representation. total_seconds() is a float, and a float cannot
        # hold a microsecond at large magnitudes: timedelta(days=100000,
        # microseconds=1) came back with microseconds=2. Replay compares
        # event values, so a rounded one is a mismatch waiting to happen.
        return msgpack.ExtType(
            _EXT_TIMEDELTA,
            encode_body([value.days, value.seconds, value.microseconds]),
        )
    if isinstance(value, uuid.UUID):
        return msgpack.ExtType(_EXT_UUID, value.bytes)
    if isinstance(value, decimal.Decimal):
        return msgpack.ExtType(_EXT_DECIMAL, str(value).encode())

    warnings.warn(
        f"{type(value).__name__} has no msgpack handler and was encoded with dill, "
        "which executes arbitrary code on load. Return a value type "
        "(dict/list/tuple/set/datetime/UUID/Decimal/primitive) to keep this payload "
        "off the unsafe path.",
        UserWarning,
        stacklevel=4,
    )
    import dill

    return msgpack.ExtType(_EXT_DILL, dill.dumps(value))


def _ext_hook(code: int, data: bytes) -> Any:
    if code == _EXT_TUPLE:
        return tuple(decode_body(data))
    if code == _EXT_SET:
        return set(decode_body(data))
    if code == _EXT_FROZENSET:
        return frozenset(decode_body(data))
    if code == _EXT_DATETIME:
        return datetime.datetime.fromisoformat(data.decode())
    if code == _EXT_DATE:
        return datetime.date.fromisoformat(data.decode())
    if code == _EXT_TIME:
        return datetime.time.fromisoformat(data.decode())
    if code == _EXT_TIMEDELTA:
        days, seconds, microseconds = decode_body(data)
        return datetime.timedelta(days=days, seconds=seconds, microseconds=microseconds)
    if code == _EXT_UUID:
        return uuid.UUID(bytes=data)
    if code == _EXT_DECIMAL:
        return decimal.Decimal(data.decode())
    if code == _EXT_DILL:
        import dill

        return dill.loads(data)
    return msgpack.ExtType(code, data)


def encode_body(value: Any) -> bytes:
    """msgpack bytes without the format tag (used by the ext handlers).

    ``strict_types`` is what makes the tuple handler reachable: without it
    msgpack packs a tuple as an array itself and never consults ``default``,
    so tuples would silently come back as lists. It also routes subclasses
    of dict/list to ``default`` rather than flattening them to their base
    type, which keeps an OrderedDict an OrderedDict (via the fallback)
    instead of quietly becoming a plain dict.
    """
    packer = msgpack.Packer(default=_default, use_bin_type=True, strict_types=True)
    return packer.pack(value)


def decode_body(blob: bytes) -> Any:
    return msgpack.unpackb(blob, ext_hook=_ext_hook, raw=False, strict_map_key=False)


def encode(value: Any) -> bytes:
    """Serialize ``value``, tagged so ``decode`` knows what it is reading."""
    return _TAG + encode_body(value)


def decode(blob: bytes) -> Any:
    """Deserialize a payload written by ``encode`` -- or by an older version.

    Blobs predating this codec are raw dill and are read as such: an
    upgrade must not make an existing execution history unreadable.
    """
    if is_msgpack_payload(blob):
        return decode_body(blob[len(_TAG) :])

    import dill

    return dill.loads(blob)
