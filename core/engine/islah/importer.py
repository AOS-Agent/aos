#!/usr/bin/env python3
"""
Iṣlāḥ — Apple Notes importer.

Pulls a bug/feature note into the ledger, idempotently:
  - reads the note via AppleScript (osascript), or from --text / --file
  - maps the note's app from its title (Quran… → quran-garden, Deen… → deenoverdunya)
  - splits sections: "Bugs" → kind=bug, "Features"/"Ideas" → kind=feature
  - dedups on a stable per-line key, so polling the same note never doubles items

    islah import-notes --note "Quran App"            # live pull via AppleScript
    islah import-notes --note "Quran App" --dry-run   # show what it would import
    islah import-notes --note "Quran App" --file note.txt   # from text (no AppleScript)
"""
from __future__ import annotations

import argparse
import hashlib
import html as _html
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import Ledger  # noqa: E402

APP_HINTS = [("quran", "quran-garden"), ("garden", "quran-garden"),
             ("deen", "deenoverdunya"), ("dunya", "deenoverdunya")]
SECTION_RE = re.compile(r'^\s*(bugs?|features?|ideas?|to ?dos?)\s*:?\s*$', re.I)
BULLETS = "-*•–—▪◦"


def app_from_title(title):
    t = (title or "").lower()
    for k, slug in APP_HINTS:
        if k in t:
            return slug
    return None


def fetch_note(name):
    """Return the note body as text via AppleScript, or None if unavailable/blocked."""
    script = f'tell application "Notes" to get body of note "{name}"'
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return strip_html(r.stdout)
        return None
    except Exception:
        return None


def strip_html(h):
    h = re.sub(r'</(div|p|li|h[1-6])>|<br\s*/?>', '\n', h, flags=re.I)
    h = re.sub(r'<li[^>]*>', '- ', h, flags=re.I)
    h = re.sub(r'<[^>]+>', '', h)
    return _html.unescape(h)


def parse_items(text):
    """Yield (kind, line) for each bullet line, kind driven by the last section header."""
    kind, items = "bug", []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        m = SECTION_RE.match(s)
        if m:
            h = m.group(1).lower()
            kind = "feature" if h.startswith(("feature", "idea")) else "bug"
            continue
        if s[0] in BULLETS:
            line = s.lstrip(BULLETS + " ").strip()
            if line:
                items.append((kind, line))
    return items


def import_key(slug, line):
    norm = re.sub(r'\s+', ' ', line.lower()).strip().rstrip('.')
    return f"notes:{slug or 'x'}:{hashlib.md5(norm.encode()).hexdigest()[:8]}"


def run(note_name, app_override=None, text=None, dry=False):
    if text is None:
        text = fetch_note(note_name)
        if text is None:
            return {"error": "osascript-unavailable"}
    slug = app_override or app_from_title(note_name)
    items = parse_items(text)
    lg = Ledger()
    existing = {b.source_ref for b in lg.all() if b.source_ref}
    new, dup = [], []
    for kind, line in items:
        key = import_key(slug, line)
        title = line if len(line) <= 80 else line[:78] + "…"
        if key in existing:
            dup.append((kind, title))
            continue
        if not dry:
            lg.add(title, app=slug, kind=kind, source="apple-notes", source_ref=key,
                   reporter="self", symptom=line, status="new")
        new.append((kind, title))
    return {"slug": slug, "new": new, "dup": dup, "total": len(items)}


def main():
    ap = argparse.ArgumentParser(prog="islah import-notes")
    ap.add_argument("--note", default="Quran App")
    ap.add_argument("--app")
    ap.add_argument("--file")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    text = Path(a.file).read_text() if a.file else None
    res = run(a.note, app_override=a.app, text=text, dry=a.dry_run)
    if res.get("error") == "osascript-unavailable":
        print("⚠ Couldn't read Apple Notes via AppleScript (needs a one-time Automation "
              "permission grant, or pass --file). Nothing imported.")
        sys.exit(2)
    tag = "would import" if a.dry_run else "imported"
    print(f"Note: {a.note}  →  app: {res['slug'] or '(unknown)'}  ·  {res['total']} items found")
    for kind, title in res["new"]:
        print(f"  + [{kind}] {title}   ({tag})")
    for kind, title in res["dup"]:
        print(f"  = [{kind}] {title}   (already tracked — skipped)")
    print(f"\n{len(res['new'])} new · {len(res['dup'])} already tracked")


if __name__ == "__main__":
    main()
