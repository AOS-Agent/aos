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
    elif first == 0x81:
        if p >= n:
            return None
        length = blob[p]; p += 1
    elif first == 0x82:
        if p + 1 >= n:
            return None
        length = blob[p] | (blob[p + 1] << 8); p += 2
    elif first == 0x83:
        if p + 2 >= n:
            return None
        length = blob[p] | (blob[p + 1] << 8) | (blob[p + 2] << 16); p += 3
    elif first == 0x84:
        if p + 3 >= n:
            return None
        length = (blob[p] | (blob[p + 1] << 8)
                  | (blob[p + 2] << 16) | (blob[p + 3] << 24)); p += 4
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
    import sqlite3
    from pathlib import Path
    db = Path.home() / "Library" / "Messages" / "chat.db"
    uri = f"file:{db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
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
