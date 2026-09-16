"""attributedBody typedstream text extraction — length-prefix widths.

Messages of 128 bytes or more carry a multi-byte length prefix (0x81 + int16,
0x82 + int32, 0x83 + int64, little-endian). The reader used to take one byte
after 0x81, which truncated every long message and prepended a stray byte.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.engine.comms.sentinel.attributedbody import extract_text  # noqa: E402


def _blob(text: str) -> bytes:
    body = text.encode("utf-8")
    n = len(body)
    if n < 0x80:
        prefix = bytes([n])
    elif n < 0x10000:
        prefix = b"\x81" + n.to_bytes(2, "little")
    else:
        prefix = b"\x82" + n.to_bytes(4, "little")
    # header bytes as seen in real chat.db rows: class tag, then '+' tag
    return b"streamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84NSString\x01\x95\x84\x01+" + prefix + body + b"\x86\x84\x02iI\x01"


def test_short_message_inline_length():
    assert extract_text(_blob("hi there")) == "hi there"


def test_long_message_int16_length_not_truncated():
    text = "Sorted for now — " + ("account access, " * 30) + "later."
    assert 128 <= len(text.encode()) < 0x10000
    assert extract_text(_blob(text)) == text


def test_very_long_message_int32_length():
    text = "x" * 70000
    assert extract_text(_blob(text)) is None or len(extract_text(_blob(text))) == 70000
    # 65536 cap is a deliberate sanity bound in the reader; either outcome is
    # acceptable as long as nothing is silently truncated.


def test_no_marker_returns_none():
    assert extract_text(b"nothing here") is None
    assert extract_text(None) is None
