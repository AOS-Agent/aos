"""Tests for the Vault signal adapter.

Builds a tiny fake vault under tmp_path, populates it with daily logs and
session exports, and exercises the adapter end-to-end. No external files
are touched — the adapter is instantiated with an explicit vault_root.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.engine.people.intel.sources.vault import (
    STOPWORDS,
    VaultAdapter,
    _name_variants,
    _strip_frontmatter,
)
from core.engine.people.intel.types import SignalType


# ── Fixture helpers ─────────────────────────────────────────────────────

def _write(path: Path, body: str, title: str = "note") -> None:
    """Write a markdown file with minimal YAML frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"---\ntitle: {title}\n---\n\n{body}\n"
    path.write_text(content, encoding="utf-8")


def _make_vault(tmp_path: Path) -> Path:
    """Build a small fake vault structure; returns the vault root."""
    root = tmp_path / "vault"
    log = root / "log"
    sessions = log / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)

    _write(log / "2026-01-01.md", "Met with Alice Smith today. Good chat.")
    _write(log / "2026-01-02.md", "Alice again. Also Bob stopped by.")
    _write(log / "2026-02-15.md", "AliceSmith came by with news.")
    _write(sessions / "2026-02-15-abc.md",
           "Quick chat with Alice Smith about the project.")
    return root


# ── Helper-function tests ───────────────────────────────────────────────

def test_name_variants_camelcase_split() -> None:
    assert sorted(_name_variants("SamTaylor")) == sorted(["SamTaylor", "Sam Taylor"])


def test_name_variants_spaced_unchanged() -> None:
    assert _name_variants("Sam Taylor") == ["Sam Taylor"]


def test_name_variants_empty() -> None:
    assert _name_variants("") == []
    assert _name_variants("   ") == []


def test_strip_frontmatter_removes_block() -> None:
    text = "---\ntitle: foo\ntags: [a,b]\n---\n\nbody text"
    assert _strip_frontmatter(text) == "\nbody text"


def test_strip_frontmatter_passthrough_when_absent() -> None:
    assert _strip_frontmatter("body only") == "body only"


def test_strip_frontmatter_malformed_preserved() -> None:
    # No closing --- fence — leave untouched.
    text = "---\ntitle: foo\nbody without close"
    assert _strip_frontmatter(text) == text


# ── is_available ────────────────────────────────────────────────────────

def test_is_available_true_when_dir_exists(tmp_path: Path) -> None:
    root = _make_vault(tmp_path)
    adapter = VaultAdapter(vault_root=str(root))
    assert adapter.is_available() is True


def test_is_available_false_when_missing(tmp_path: Path) -> None:
    adapter = VaultAdapter(vault_root=str(tmp_path / "nope"))
    assert adapter.is_available() is False


# ── Extract basics ──────────────────────────────────────────────────────

def test_extract_returns_dict(tmp_path: Path) -> None:
    root = _make_vault(tmp_path)
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({})
    assert isinstance(out, dict)
    assert out == {}


def test_extract_returns_empty_when_vault_missing(tmp_path: Path) -> None:
    adapter = VaultAdapter(vault_root=str(tmp_path / "missing"))
    out = adapter.extract_all({"p1": {"name": "Alice Smith"}})
    assert out == {}


def test_matches_spaced_name_in_daily_log(tmp_path: Path) -> None:
    root = _make_vault(tmp_path)
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "Alice Smith"}})
    assert "p1" in out
    ps = out["p1"]
    assert ps.person_name == "Alice Smith"
    assert ps.source_coverage == ["vault"]
    assert len(ps.mentions) == 1
    m = ps.mentions[0]
    assert m.source == "vault"
    assert m.total_mentions >= 2  # 2026-01-01 + session export
    assert m.daily_log_mentions >= 1
    assert m.session_mentions >= 1


def test_matches_concatenated_canonical_name(tmp_path: Path) -> None:
    root = _make_vault(tmp_path)
    adapter = VaultAdapter(vault_root=str(root))
    # Canonical name stored in camelCase form — should match both
    # "AliceSmith" (file 2026-02-15) and "Alice Smith" (via variant).
    out = adapter.extract_all({"p1": {"name": "AliceSmith"}})
    assert "p1" in out
    m = out["p1"].mentions[0]
    # 2026-01-01 has "Alice Smith", 2026-02-15 has "AliceSmith", session has "Alice Smith"
    assert m.total_mentions >= 3


def test_daily_and_session_split(tmp_path: Path) -> None:
    root = _make_vault(tmp_path)
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "Alice Smith"}})
    m = out["p1"].mentions[0]
    assert m.daily_log_mentions > 0
    assert m.session_mentions > 0
    assert m.total_mentions == m.daily_log_mentions + m.session_mentions


def test_mention_contexts_have_file_snippet_date(tmp_path: Path) -> None:
    root = _make_vault(tmp_path)
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "Alice Smith"}})
    m = out["p1"].mentions[0]
    assert len(m.mention_contexts) > 0
    ctx = m.mention_contexts[0]
    assert "file" in ctx and "snippet" in ctx and "date" in ctx
    # Date should be parseable from the filename.
    assert ctx["date"] is None or ctx["date"].startswith("202")
    # Snippet should contain Alice.
    assert "Alice" in ctx["snippet"] or "alice" in ctx["snippet"].lower()
    # Snippet should have no embedded newlines.
    assert "\n" not in ctx["snippet"]


# ── Frontmatter is stripped before scanning ─────────────────────────────

def test_yaml_frontmatter_stripped(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    # "Alice Smith" appears ONLY in frontmatter — not the body.
    (log / "2026-03-01.md").write_text(
        "---\ntitle: meeting with Alice Smith\n---\n\nNothing to note.\n",
        encoding="utf-8",
    )
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "Alice Smith"}})
    assert out == {}


# ── Skipping rules ──────────────────────────────────────────────────────

def test_skips_short_names(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    _write(log / "2026-03-02.md", "Talked to Ali today.")
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "Sam"}})
    assert out == {}


def test_skips_stopword_names(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    _write(log / "2026-03-03.md", "Love is in the air. Love love love.")
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "Love"}})
    assert out == {}


def test_all_stopwords_skipped(tmp_path: Path) -> None:
    # Sanity: every stopword triggers the skip.
    root = tmp_path / "vault"
    (root / "log").mkdir(parents=True)
    adapter = VaultAdapter(vault_root=str(root))
    index = {f"p{i}": {"name": word.title()} for i, word in enumerate(STOPWORDS)}
    out = adapter.extract_all(index)
    assert out == {}


# ── Unmatched persons ───────────────────────────────────────────────────

def test_unmatched_person_absent(tmp_path: Path) -> None:
    root = _make_vault(tmp_path)
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({
        "p1": {"name": "Alice Smith"},
        "p2": {"name": "Zaphod Beeblebrox"},
    })
    assert "p1" in out
    assert "p2" not in out


# ── Context cap ─────────────────────────────────────────────────────────

def test_mention_contexts_capped_at_20(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    # 25 mentions in the same file.
    body = " ".join(["Alice Smith came by."] * 25)
    _write(log / "2026-04-01.md", body)
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "Alice Smith"}})
    m = out["p1"].mentions[0]
    assert m.total_mentions == 25
    assert len(m.mention_contexts) == 20


# ── Scan caps ───────────────────────────────────────────────────────────

def test_daily_scan_cap_respected(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)

    # Create 100 daily files. Only the 90 most recent (by filename sort
    # descending) should be scanned. Put Alice in the OLDEST 10 files —
    # she should be invisible to the adapter.
    for i in range(100):
        # Filenames 2025-01-01 ... 2025-04-10 style; we just need 100
        # distinct names sortable by string. Use YYYY-NNN shape.
        year = 2025 + (i // 50)
        idx = i % 50
        name = f"{year}-{idx:02d}-01.md"
        # The 10 oldest files (smallest filenames) mention Alice.
        mentions_alice = i < 10
        body = "Alice Smith said hi." if mentions_alice else "nothing here."
        _write(log / name, body)

    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "Alice Smith"}})
    # All 10 Alice mentions live in the oldest 10 files, which fall
    # outside the 90-file cap → adapter must not see them.
    assert out == {}


def test_daily_scan_cap_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Tighten the cap and verify newest-first selection works.
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    _write(log / "2026-01-01.md", "nothing")
    _write(log / "2026-02-01.md", "nothing")
    _write(log / "2026-03-01.md", "Alice Smith visited.")

    adapter = VaultAdapter(vault_root=str(root))
    # Cap at 1 → only 2026-03-01 (the newest) is scanned → Alice found.
    monkeypatch.setattr(VaultAdapter, "DAILY_SCAN_LIMIT", 1)
    out = adapter.extract_all({"p1": {"name": "Alice Smith"}})
    assert "p1" in out

    # Move Alice into the OLDEST file. With cap 1 she's invisible.
    _write(log / "2026-01-01.md", "Alice Smith visited.")
    _write(log / "2026-03-01.md", "nothing")
    out = adapter.extract_all({"p1": {"name": "Alice Smith"}})
    assert out == {}


# ── Variant dedup ───────────────────────────────────────────────────────

def test_multiple_variants_dont_double_count(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    # Body has one "SamTaylor" and one "Sam Taylor" — two distinct textual
    # mentions. Both variants regex-match, but each match lives at a
    # unique offset. Dedup by (start, end) should give exactly 2.
    _write(log / "2026-05-01.md", "SamTaylor dropped by. Later Sam Taylor called.")
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "SamTaylor"}})
    assert "p1" in out
    m = out["p1"].mentions[0]
    # "SamTaylor" matches once (the camelCase occurrence). "Sam Taylor"
    # matches once (the spaced occurrence). They don't overlap → 2.
    assert m.total_mentions == 2


def test_variant_dedup_same_span(tmp_path: Path) -> None:
    # When only ONE textual mention exists, the two variants must not
    # each increment the counter. "Sam Taylor" appears once; both
    # variants ["SamTaylor", "Sam Taylor"] are compiled, but only the
    # spaced variant matches — count must be 1.
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    _write(log / "2026-06-01.md", "Sam Taylor is the only mention here.")
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "SamTaylor"}})
    m = out["p1"].mentions[0]
    assert m.total_mentions == 1


# ── Component second-pass (context-confirmed) ──────────────────────────


def test_full_name_variant_baseline(tmp_path: Path) -> None:
    # Sanity: the existing full-name variant still catches a spaced mention.
    # Note: we use "Alex Taylor" instead of the spec's "Sam Taylor" so both
    # tokens clear the MIN_NAME_LENGTH=4 filter used by component_variants.
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    _write(log / "2026-07-01.md", "Met with Alex Taylor today.")
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "AlexTaylor"}})
    assert "p1" in out
    m = out["p1"].mentions[0]
    assert m.total_mentions == 1


def test_component_match_with_nearby_confirmation(tmp_path: Path) -> None:
    # "Alex" and "Taylor" mentioned separately but within 200 chars of
    # each other → component pass should count BOTH (confirmation passes).
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    _write(
        log / "2026-07-02.md",
        "Saw Alex yesterday. Good conversation with Taylor later.",
    )
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "AlexTaylor"}})
    assert "p1" in out
    m = out["p1"].mentions[0]
    # "Alex" confirms via nearby "Taylor", and vice versa.
    assert m.total_mentions == 2


def test_component_match_rejected_without_confirmation(tmp_path: Path) -> None:
    # "Alex" alone, with "Taylor" absent → the component match must be
    # rejected (no confirmation token within 200 chars).
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    _write(log / "2026-07-03.md", "Meeting with Alex at 3pm. Nothing else to report.")
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "AlexTaylor"}})
    # No Taylor present → confirmation fails → no mentions.
    assert out == {}


def test_single_component_name_skips_component_pass(tmp_path: Path) -> None:
    # A name that normalizes to a single component (e.g., "Kumar") must
    # skip the component pass entirely — confirmation impossible.
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    _write(log / "2026-07-04.md", "Kumar dropped by. Kumar again later.")
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all({"p1": {"name": "Kumar"}})
    # Primary pass still catches "Kumar" as a full-name variant (since
    # "Kumar" is its own full name), so mentions > 0 is fine — the key
    # guarantee is the adapter does not crash and single-component names
    # don't invoke the component pass. Assert it behaves normally via
    # the full-name pattern.
    assert "p1" in out
    m = out["p1"].mentions[0]
    assert m.total_mentions == 2


def test_component_pass_does_not_cross_contaminate_between_persons(tmp_path: Path) -> None:
    # Two distinct people share a surname. "Kumar" alone (1000 chars away
    # from any first name) must not inflate either person's count.
    root = tmp_path / "vault"
    log = root / "log"
    log.mkdir(parents=True)
    # Place "Alice" and "Bob" at the start, "Kumar" 1000+ chars later.
    filler = "x" * 1200
    body = f"Alice was here. Bob was here too.\n{filler}\nKumar unaccompanied."
    _write(log / "2026-07-05.md", body)
    adapter = VaultAdapter(vault_root=str(root))
    out = adapter.extract_all(
        {
            "p_alice": {"name": "Alice Kumar"},
            "p_bob": {"name": "Bob Kumar"},
        }
    )
    # Alice: the primary full-name pattern "Alice Kumar" does NOT match
    # (the tokens are not adjacent). The component pass finds "Alice" and
    # "Kumar", but they're 1000+ chars apart → no confirmation. So Alice's
    # count should come from nothing — she should be absent.
    # Bob: same reasoning.
    assert "p_alice" not in out
    assert "p_bob" not in out


# ── Adapter metadata ────────────────────────────────────────────────────

def test_adapter_metadata() -> None:
    assert VaultAdapter.name == "vault"
    assert VaultAdapter.display_name == "Vault Logs + Sessions"
    assert VaultAdapter.platform == "any"
    assert VaultAdapter.signal_types == [SignalType.MENTION]
    assert "dir:~/vault/log" in VaultAdapter.requires
