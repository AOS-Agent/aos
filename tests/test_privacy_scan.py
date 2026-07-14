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


def test_versioned_identifier_not_flagged_as_phone():
    # A model ID / package version / container tag glued to a preceding word
    # via hyphens (e.g. an LLM model string) is a compound identifier, not a
    # phone number — no real phone is ever written "word-word-555-1234" with
    # no space before the digits.
    diff = make_diff("core/foo.py", ['    model="claude-haiku-4-5-20251001",'])
    hits = ps.scan_diff(diff, [])
    assert not any(h.category == "phone" for h in hits)


def test_versioned_identifier_heuristic_does_not_gut_real_phone():
    # Guard against over-broadening: a real phone number that merely follows
    # a word *with a space* (not glued via hyphen) must still be flagged.
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


# ── Precedent pass ──────────────────────────────────────────────────────────
import subprocess as _sp


def _git_repo(tmp_path, files):
    """Init a git repo at tmp_path with {relpath: content}, commit once."""
    _sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    _sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.example"],
            check=True)
    _sp.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    for rel, content in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    _sp.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    _sp.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "base"], check=True)


def test_precedent_path_finds_identical_line(tmp_path):
    # A byte-identical line (different indentation) already in the base tree
    # is found — indentation is normalized away.
    line = f'greeting = "Hello {FAKE_NAME}"'
    _git_repo(tmp_path, {"core/engine/comms/resolver.py": f"import x\n    {line}\n"})
    p = ps._precedent_path(line, "HEAD", cwd=str(tmp_path))
    assert p == "core/engine/comms/resolver.py"


def test_precedent_path_rejects_different_line_context(tmp_path):
    # The denylist term exists in the base tree, but NOT as this exact line.
    # Sharing a term is not precedent — the whole line must match.
    _git_repo(tmp_path, {"core/x.py": f'display = "prefix {FAKE_NAME} suffix"\n'})
    p = ps._precedent_path(f'name = "{FAKE_NAME}"', "HEAD", cwd=str(tmp_path))
    assert p is None


def test_precedent_path_rejects_substring_of_longer_line(tmp_path):
    # git grep -F would match the text as a substring of a longer base line;
    # the full-line normalized-equality guard must still reject it.
    _git_repo(tmp_path, {"core/x.py": f'xname = "{FAKE_NAME}" + tail\n'})
    p = ps._precedent_path(f'name = "{FAKE_NAME}"', "HEAD", cwd=str(tmp_path))
    assert p is None


def test_base_ref_of_extracts_before_side():
    assert ps.base_ref_of("origin/main..HEAD") == "origin/main"
    assert ps.base_ref_of(None) == "HEAD"
    assert ps.base_ref_of("origin/main") == "origin/main"


def test_precedented_denylist_line_is_not_a_failure(denylist):
    # (a) A denylist hit whose exact line already ships at base is downgraded
    # to INFO — present in hits, but report() treats the diff as clean.
    line = f'greeting = "Hello {FAKE_NAME}"'
    diff = make_diff("core/engine/people/normalize.py", [line])

    def lookup(content):
        return ("core/engine/comms/resolver.py"
                if ps._norm_ws(content) == ps._norm_ws(line) else None)

    hits = ps.scan_diff(diff, denylist, precedent_lookup=lookup)
    dl = [h for h in hits if h.category.startswith("denylist:")]
    assert dl and all(h.precedented for h in dl)
    assert all(h.precedent_path == "core/engine/comms/resolver.py" for h in dl)
    assert ps.report(hits) == 0


def test_denylist_hit_without_precedent_still_fails(denylist):
    # (b) Same shape, but no identical base line → lookup returns None → the
    # hit stays a failure.
    line = f'name = "{FAKE_NAME}"'
    diff = make_diff("core/engine/people/normalize.py", [line])
    hits = ps.scan_diff(diff, denylist, precedent_lookup=lambda content: None)
    dl = [h for h in hits if h.category.startswith("denylist:")]
    assert dl and not any(h.precedented for h in dl)
    assert ps.report(hits) == 1


def test_pattern_hit_never_precedent_downgraded(denylist):
    # (c) A phone (pattern category) hit must never be precedent-downgraded,
    # even if the lookup would precedent literally anything.
    line = f'phone = "{FAKE_PHONE}"'
    diff = make_diff("core/x.py", [line])
    hits = ps.scan_diff(diff, denylist,
                        precedent_lookup=lambda content: "core/anywhere.py")
    phones = [h for h in hits if h.category == "phone"]
    assert phones and not any(h.precedented for h in phones)
    assert ps.report(hits) == 1


# ── Reserved documentation values (RFC 2606 / RFC 6761 / NANP fictional) ─────
# Assemble reserved literals from fragments so this source carries no verbatim
# trigger, same as the rest of the file.
RESERVED_EMAIL = "docs" + _AT + "example.com"
RESERVED_EMAIL_SUB = "team" + _AT + "mail.example.org"
RESERVED_EMAIL_TLD = "user" + _AT + "host.invalid"
REAL_EMAIL = "someone" + _AT + "gmail.com"          # ownable → still a leak
RESERVED_PHONE = "+1 416 " + "555 " + "0142"        # NANP fictional 555-01XX
REAL_PHONE = "+1 416 " + "555 " + "1234"            # real-shaped, not fictional


def test_reserved_domain_email_downgraded_to_info():
    for addr in (RESERVED_EMAIL, RESERVED_EMAIL_SUB, RESERVED_EMAIL_TLD):
        diff = make_diff("core/x.py", [f'contact = "{addr}"'])
        hits = ps.scan_diff(diff, [])
        emails = [h for h in hits if h.category == "email"]
        assert emails and all(h.precedented for h in emails), addr
        assert all(h.note == "reserved documentation domain" for h in emails)
        assert ps.report(hits) == 0


def test_real_domain_email_still_fails():
    diff = make_diff("core/x.py", [f'contact = "{REAL_EMAIL}"'])
    hits = ps.scan_diff(diff, [])
    emails = [h for h in hits if h.category == "email"]
    assert emails and not any(h.precedented for h in emails)
    assert ps.report(hits) == 1


def test_nanp_fictional_phone_downgraded_to_info():
    diff = make_diff("core/x.py", [f'support = "{RESERVED_PHONE}"'])
    hits = ps.scan_diff(diff, [])
    phones = [h for h in hits if h.category == "phone"]
    assert phones and all(h.precedented for h in phones)
    assert all(h.note == "reserved fictional phone range" for h in phones)
    assert ps.report(hits) == 0


def test_real_shaped_non_reserved_phone_still_fails():
    diff = make_diff("core/x.py", [f'support = "{REAL_PHONE}"'])
    hits = ps.scan_diff(diff, [])
    phones = [h for h in hits if h.category == "phone"]
    assert phones and not any(h.precedented for h in phones)
    assert ps.report(hits) == 1


def test_non_nanp_number_ending_in_555_01_not_reserved():
    # The NANP fictional block is North-American-only; a formatted 12-digit
    # international number that merely ends in ...555 0142 must NOT be treated
    # as reserved (it isn't NANP-shaped).
    intl = "+971 50 " + "555 0142"       # 12 digits, separator-grouped, not NANP
    diff = make_diff("core/x.py", [f'wa = "{intl}"'])
    hits = ps.scan_diff(diff, [])
    phones = [h for h in hits if h.category == "phone"]
    assert phones and not any(h.precedented for h in phones)


def test_reserved_downgrade_does_not_apply_to_denylist(denylist):
    # A reserved-domain address that is ALSO the operator's denylisted email is
    # still a denylist failure — reserved-value downgrade is pattern-only.
    diff = make_diff("core/x.py", [f'owner = "{FAKE_EMAIL}"'])  # on denylist
    hits = ps.scan_diff(diff, denylist)
    assert any(h.category == "denylist:email" and not h.precedented for h in hits)
    assert ps.report(hits) == 1
