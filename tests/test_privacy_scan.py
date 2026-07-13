"""Tests for the privacy-scan ship gate.

Fixture-based: every planted value here is OBVIOUSLY FAKE. Using real operator
data in a test file would defeat the entire purpose of the scanner.

This file also must not itself trip the privacy gate — privacy-scan runs over the
whole framework diff, including this file. There is deliberately NO test-file
exemption (real leaks hide in test fixtures — see the people-intelligence tests).
Instead, every trigger-shaped value is ASSEMBLED from fragments at runtime, so the
source contains no verbatim email/phone/key/real-name literal, while the assembled
values still exercise the scanner exactly as real data would.
"""

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

CLI = Path(__file__).parent.parent / "core" / "bin" / "cli" / "privacy-scan"


def _load_module():
    loader = importlib.machinery.SourceFileLoader("privacy_scan", str(CLI))
    spec = importlib.util.spec_from_loader("privacy_scan", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ps = _load_module()


# ── Fake data, assembled so no verbatim trigger literal lives in this source ──
_AT = "@"
FAKE_NAME = "John Fakename"          # names don't match generic patterns; safe literal
FAKE_NICK = "Johnny Notreal"
FAKE_CONTACT = "Jane Madeup"
FAKE_DOMAIN = "notarealdomain-xyz.example"
FAKE_TOKEN_WORD = "Zorptek"          # fictional; used for boundary matching

FAKE_EMAIL = "planted" + _AT + "example.com"
FAKE_EMAIL_B = "someone" + _AT + "elsewhere.example"
FAKE_EMAIL_C = "hit" + _AT + "sample.example"
FAKE_EMAIL_D = "removed" + _AT + "sample.example"

FAKE_PHONE = "+1 555 " + "867 " + "5309"
FAKE_PHONE_DIGITS = "1555" + "8675309"

# A 41-char key, split so neither source fragment reaches the 40-char threshold.
FAKE_KEY = "AbC0dEf1GhIj2KlMn3Op" + "Qr4StUv5WxYz6AbC7dEf8"

# A phone-shaped digit run as it appears inside a go.sum hash.
FAKE_LOCK_DIGITS = "12345" + "67890123"


@pytest.fixture
def denylist(tmp_path):
    """A denylist file with fake operator data, parsed into scanner entries."""
    text = (
        "# fake denylist for tests\n"
        "# == category: name ==\n"
        f"{FAKE_NAME}\n{FAKE_NICK}\nFakename\n"
        "# == category: email ==\n"
        f"{FAKE_EMAIL}\n"
        "# == category: phone ==\n"
        f"{FAKE_PHONE_DIGITS}\n"
        "# == category: domain ==\n"
        f"{FAKE_DOMAIN}\n"
        "# == category: contact ==\n"
        f"{FAKE_CONTACT}\n"
    )
    p = tmp_path / "privacy-denylist.txt"
    p.write_text(text)
    entries, present = ps.load_denylist(p)
    assert present
    return entries


def make_diff(path, added_lines, start=1):
    """Build a minimal unified diff adding *added_lines* to *path*."""
    body = "".join(f"+{l}\n" for l in added_lines)
    n = len(added_lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +{start},{n} @@\n"
        f"{body}"
    )


# ── Denylist matching ───────────────────────────────────────────────────────
def test_planted_name_is_caught(denylist):
    diff = make_diff("core/foo.py", [f'greeting = "Hello {FAKE_NAME}"'])
    hits = ps.scan_diff(diff, denylist)
    assert "denylist:name" in {h.category for h in hits}


def test_planted_contact_is_caught(denylist):
    diff = make_diff("core/bar.py", [f"# thanks to {FAKE_CONTACT}"])
    hits = ps.scan_diff(diff, denylist)
    assert any(h.category == "denylist:contact" for h in hits)


def test_planted_domain_is_caught(denylist):
    diff = make_diff("core/baz.py", [f'url = "https://{FAKE_DOMAIN}/api"'])
    hits = ps.scan_diff(diff, denylist)
    assert any(h.category == "denylist:domain" for h in hits)


def test_matched_string_is_never_printed(denylist, capsys):
    diff = make_diff("core/foo.py", [f'x = "{FAKE_NAME}"', f'y = "{FAKE_EMAIL}"'])
    hits = ps.scan_diff(diff, denylist)
    ps.report(hits)
    out = capsys.readouterr().out
    assert FAKE_NAME not in out          # the whole point: secrets stay redacted
    assert FAKE_EMAIL not in out
    assert "sha256:" in out


def test_short_terms_are_skipped(tmp_path):
    p = tmp_path / "dl.txt"
    p.write_text("# == category: name ==\nJoe\nAmy\n")  # both < 4 chars
    entries, _ = ps.load_denylist(p)
    assert entries == []


def test_word_boundary_avoids_substring_false_positive(tmp_path):
    p = tmp_path / "dl.txt"
    p.write_text(f"# == category: name ==\n{FAKE_TOKEN_WORD}\n")
    entries, _ = ps.load_denylist(p)
    # Must not match inside a larger word...
    clean = make_diff("core/x.py", [f"word = 'un{FAKE_TOKEN_WORD}able other{FAKE_TOKEN_WORD}s'"])
    assert ps.scan_diff(clean, entries) == []
    # ...but must match as a standalone token.
    dirty = make_diff("core/x.py", [f"name = '{FAKE_TOKEN_WORD}'"])
    assert any(h.category == "denylist:name" for h in ps.scan_diff(dirty, entries))


# ── Generic patterns (work even with an empty denylist) ─────────────────────
def test_generic_email_without_denylist():
    diff = make_diff("core/foo.py", [f'contact = "{FAKE_EMAIL_B}"'])
    hits = ps.scan_diff(diff, [])
    assert any(h.category == "email" for h in hits)


def test_generic_phone():
    diff = make_diff("core/foo.py", [f'phone = "{FAKE_PHONE}"'])
    hits = ps.scan_diff(diff, [])
    assert any(h.category == "phone" for h in hits)


def test_bare_numeric_code_constant_not_flagged_as_phone():
    # A bare, unformatted 10-digit run in an arithmetic expression (e.g. a
    # PRNG's 2**32 normalization divisor) is a numeric constant, not a phone
    # number — it has no '+' prefix and no separators.
    two_to_the_32 = "4294967296"
    diff = make_diff(
        "core/foo.py",
        [f"return ((t = t ^ (t >>> 15)) >>> 0) / {two_to_the_32}"],
    )
    hits = ps.scan_diff(diff, [])
    assert not any(h.category == "phone" for h in hits)


def test_hex_literal_and_uuid_segment_not_flagged_as_phone():
    # A 0x-prefixed hex literal never matches (word-boundary blocks it), and
    # a bare all-decimal segment lifted from inside a dash-separated UUID
    # must also not be flagged — same "bare digit run" shape as a phone
    # number, but it's a UUID fragment, not a formatted phone.
    diff = make_diff(
        "core/foo.py",
        [
            "seed = 0x1234567890",
            'request_id = "123e4567-e89b-12d3-a456-426614174000"',
        ],
    )
    hits = ps.scan_diff(diff, [])
    assert not any(h.category == "phone" for h in hits)


def test_real_looking_phone_still_flagged():
    # A realistically-formatted phone number (international prefix, grouped
    # digits) must still trip the gate — the fix above narrows the pattern,
    # it doesn't gut it.
    fake_real_phone = "+1-416-" + "555-0199"
    diff = make_diff("core/foo.py", [f'support_line = "{fake_real_phone}"'])
    hits = ps.scan_diff(diff, [])
    assert any(h.category == "phone" for h in hits)


def test_bare_instance_path_is_not_flagged():
    # A path reference is not personal data; framework code uses these roots.
    diff = make_diff("core/foo.py", ['db = open("~/.aos/data/people.db")'])
    hits = ps.scan_diff(diff, [])
    assert hits == []


def test_apikey_shaped_constant_flagged():
    diff = make_diff("core/foo.py", [f'TOKEN = "{FAKE_KEY}"'])
    hits = ps.scan_diff(diff, [])
    assert any(h.category == "api-key" for h in hits)


def test_lockfile_hashes_not_flagged_as_keys():
    sha = "a" * 64  # sha256-shaped, all hex, no case mix
    diff = make_diff("uv.lock", [f'hash = "{sha}"'])
    hits = ps.scan_diff(diff, [])
    assert not any(h.category == "api-key" for h in hits)


def test_lockfile_digit_runs_not_flagged_as_phone():
    # go.sum base64 hashes contain long digit runs that look phone-shaped.
    diff = make_diff("core/services/x/go.sum", [f"h1:{FAKE_LOCK_DIGITS} v1.2.3"])
    hits = ps.scan_diff(diff, [])
    assert not any(h.category in ("phone", "email", "api-key") for h in hits)


def test_git_sha_context_not_flagged():
    sha = "0123456789abcdef0123456789abcdef01234567"
    diff = make_diff("core/foo.py", [f'revision = "{sha}"'])
    hits = ps.scan_diff(diff, [])
    # "revision" is a hash-context word and the token is all-lowercase-hex.
    assert not any(h.category == "api-key" for h in hits)


# ── Diff parsing ────────────────────────────────────────────────────────────
def test_only_added_lines_scanned():
    # A removed line containing an email must NOT be flagged.
    diff = (
        "diff --git a/core/foo.py b/core/foo.py\n"
        "--- a/core/foo.py\n"
        "+++ b/core/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        f'-old = "{FAKE_EMAIL_D}"\n'
        '+new = "clean_value"\n'
        " unchanged = 1\n"
    )
    hits = ps.scan_diff(diff, [])
    assert hits == []


def test_line_numbers_are_new_file_positions():
    diff = (
        "diff --git a/core/foo.py b/core/foo.py\n"
        "--- a/core/foo.py\n"
        "+++ b/core/foo.py\n"
        "@@ -10,0 +10,1 @@\n"
        f'+email = "{FAKE_EMAIL_C}"\n'
    )
    hits = ps.scan_diff(diff, [])
    assert len(hits) == 1
    assert hits[0].line == 10


def test_clean_diff_is_clean(denylist):
    diff = make_diff("core/foo.py", ["def add(a, b):", "    return a + b"])
    assert ps.scan_diff(diff, denylist) == []
