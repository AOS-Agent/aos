"""Extract message text from iMessage chat.db attributedBody blob.

On modern macOS, iMessage stores message text in a binary NSAttributedString
(NSKeyedArchiver/typedstream format) in the `attributedBody` column instead
of populating the simple `text` column. Direct SQLite queries that only
read `text` miss these messages entirely.

This decoder uses the typedstream length-prefix pattern to extract the
string reliably (not just "longest printable run", which gets confused
by metadata strings like 'NSDictionary' that follow the text).

Typedstream format after the NSString class definition:
    \\x01 \\x94 \\x84 \\x01 [tag] [length_encoding] [utf8_bytes]

Where:
    tag = 0x2B ('+')  — C-string
    length encoding:
        single byte L  if L < 0x80
        \\x81 [L]       if L < 256
        \\x82 [L_hi] [L_lo]  if L < 65536
        etc.
"""

from __future__ import annotations

from typing import Optional


def extract_text(blob: Optional[bytes]) -> Optional[str]:
    """Extract the message text from attributedBody. Returns None if not found."""
    if not blob or not isinstance(blob, (bytes, bytearray)):
        return None

    marker = b"NSString"
    idx = blob.find(marker)
    if idx < 0:
        return None

    # Walk past NSString and the class trailer. There may be more than one
    # NSString reference in the blob (one for the class definition, one for
    # the actual string instance). Try each occurrence.
    while idx >= 0:
        text = _try_decode_at(blob, idx + len(marker))
        if text:
            return text
        # Try the next occurrence of NSString
        idx = blob.find(marker, idx + 1)

    return None


def _try_decode_at(blob: bytes, start: int) -> Optional[str]:
    """Attempt to decode a string after the NSString marker at `start`."""
    # The typedstream marker sequence after the class name varies slightly,
    # but the C-string tag 0x2B ('+') reliably appears within ~6 bytes.
    n = len(blob)

    # Scan ahead for the 0x2B tag, bounded
    p = start
    limit = min(p + 12, n)
    tag_pos = -1
    while p < limit:
        if blob[p] == 0x2B:
            tag_pos = p
            break
        p += 1
    if tag_pos < 0:
        return None

    p = tag_pos + 1
    if p >= n:
        return None

    # Read length encoding
    length: int = 0
    first = blob[p]; p += 1
    if first < 0x80:
        length = first
    elif first in (0x81, 0x82, 0x83):
        # typedstream integer prefixes: 0x81 = int16, 0x82 = int32,
        # 0x83 = int64, all little-endian. A 200-char message is encoded
        # as 0x81 followed by two bytes; reading one byte here truncated
        # every message of 128 bytes or more (seen live: a 443-byte reply
        # cut at 187 with a stray leading byte).
        width = {0x81: 2, 0x82: 4, 0x83: 8}[first]
        if p + width > n:
            return None
        length = int.from_bytes(blob[p:p + width], "little"); p += width
    else:
        return None

    if length <= 0 or length > 65536:  # sanity
        return None
    if p + length > n:
        return None

    text_bytes = bytes(blob[p:p + length])
    try:
        return text_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ── self-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path

    _repo = Path(__file__).resolve().parents[4]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from core.engine.comms import scope  # noqa: E402

    db = Path.home() / "Library" / "Messages" / "chat.db"
    conn = scope.open_chat_db(db, source="attributedbody-selftest")
    rows = conn.execute("""
        SELECT rowid, text, attributedBody FROM message
        WHERE is_from_me=1 AND rowid IN (220210, 220209, 220208, 220187, 220150, 220149)
        ORDER BY rowid
    """).fetchall()
    for rowid, text, ab in rows:
        decoded = extract_text(ab)
        print(f"rowid={rowid}")
        print(f"  text col: {text!r}")
        print(f"  decoded:  {decoded!r}")
        print()
