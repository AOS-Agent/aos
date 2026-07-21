#!/usr/bin/env python3
"""
Iṣlāḥ — Apple Notes *folder* importer (text + screenshots + write-back).

Pulls every note in a folder (default: "dev.hish.am") into the bug ledger:

  1. TEXT   — reads each note's body via AppleScript, strips HTML, parses the
              Bugs/Features bullets into ledger items (reuses importer.py).
  2. IMAGES — pasted screenshots live on disk under
              ~/Library/Group Containers/group.com.apple.notes/Accounts/*/Media/<UUID>/*/<file>
              and the note→attachment→media links live in NoteStore.sqlite. We
              join note→attachment→media, resolve each file, and — because the
              note body's inline base64 is byte-identical to the Media original —
              we map each image to the exact bug it sits under by content hash
              (document order in the body → owning bullet). Precise per-line.
  3. DEDUP  — source_ref = importer.import_key(slug, line). Exact-match skip,
              plus a conservative fuzzy safety-net (>=0.65 vs existing same-app
              title/symptom) so a lightly-reworded line doesn't double a bug.
  4. WRITE-BACK — appends/updates a "— Iṣlāḥ —" section at the end of each note
              listing what was tracked. Preserves existing content (images and
              all); replaces the old block on re-runs. Idempotent.

CLI:
    python3 notes_folder.py sync                    # every note in the folder
    python3 notes_folder.py sync --note "QG iOS"    # one note
    python3 notes_folder.py sync --dry-run          # show, write nothing
"""
from __future__ import annotations

import argparse
import base64
import difflib
import glob
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.aos/islah/app"))
import importer  # noqa: E402
from store import MEDIA_DIR, Ledger  # noqa: E402

FOLDER = "dev.hish.am"

# Explicit note-title → app. Fallback to importer.app_from_title for unknowns.
NOTE_APP = {
    "QG iOS": "quran-garden",
    "Deen Over Dunya": "deenoverdunya",
    "study.hish.am": "qg-web",   # a web project (registry prefix qgw)
}

NOTES_ROOT = os.path.expanduser(
    "~/Library/Group Containers/group.com.apple.notes")
NOTE_STORE = os.path.join(NOTES_ROOT, "NoteStore.sqlite")
MEDIA_GLOB = os.path.join(NOTES_ROOT, "Accounts", "*", "Media")

BACKUP_DIR = Path(os.path.expanduser("~/.aos/islah/.backups"))
MARK = "— Iṣlāḥ —"
FUZZY_DUP = 0.65          # >= this vs existing same-app title/symptom => near-dup
BULLETS = "-*•–—▪◦"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip().rstrip(".")


# ── AppleScript ──────────────────────────────────────────────────────────────

def osa(script: str, timeout: int = 60) -> tuple[int, str, str]:
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def list_notes(folder: str = FOLDER, include_companions: bool = True) -> list[str]:
    rc, out, err = osa(f'tell application "Notes" to get name of notes of folder "{folder}"')
    if rc != 0 or not out.strip():
        raise RuntimeError(f"could not enumerate notes of folder {folder!r}: {err.strip()}")
    names = [n.strip() for n in out.strip().split(", ") if n.strip()]
    if not include_companions:
        # never re-ingest our own tracking notes ("<note> — Iṣlāḥ")
        names = [n for n in names if not n.endswith("Iṣlāḥ")]
    return names


def get_body(name: str, folder: str = FOLDER) -> str:
    esc = name.replace('"', '\\"')
    rc, out, err = osa(
        f'tell application "Notes" to get body of note "{esc}" of folder "{folder}"')
    if rc != 0:
        raise RuntimeError(f"could not read body of {name!r}: {err.strip()}")
    return out


def set_body_from_file(name: str, path: str, folder: str = FOLDER) -> None:
    """Set a note body from a UTF-8 file (avoids putting a huge body on argv)."""
    esc = name.replace('"', '\\"')
    script = (
        f'set theFile to POSIX file "{path}"\n'
        'set fh to open for access theFile\n'
        'set theText to (read fh as «class utf8»)\n'
        'close access fh\n'
        f'tell application "Notes" to set body of note "{esc}" of folder "{folder}" to theText'
    )
    rc, out, err = osa(script)
    if rc != 0:
        raise RuntimeError(f"write-back failed for {name!r}: {err.strip()}")


def upsert_companion(note_name: str, body_html: str, folder: str = FOLDER) -> str:
    """Create/replace a companion tracking note "<note> — Iṣlāḥ" in the folder.

    Headless and image-safe: the image-bearing source note is NEVER mutated
    (AppleScript `set body` strips inline images), so we keep the durable
    tracking marker in a sibling note that has no images of its own.
    """
    comp = f"{note_name} — Iṣlāḥ"
    tmp = BACKUP_DIR / f".comp-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    tmp.write_text(body_html)
    existing = list_notes(folder)
    if comp in existing:
        set_body_from_file(comp, str(tmp), folder)
    else:
        # make new note in the folder, then set its body from file
        esc = comp.replace('"', '\\"')
        rc, out, err = osa(
            f'tell application "Notes" to make new note at folder "{folder}" '
            f'with properties {{body:"<div>{esc}</div>"}}')
        if rc != 0:
            raise RuntimeError(f"could not create companion {comp!r}: {err.strip()}")
        set_body_from_file(comp, str(tmp), folder)
    tmp.unlink(missing_ok=True)
    return comp


# ── Images: note → media, keyed by content hash ─────────────────────────────

def note_media_by_hash(note_title: str) -> dict[str, str]:
    """Return {md5[:10]: absolute_path} for every image attached to the note.

    Joins ZICCLOUDSYNCINGOBJECT (attachment) → (media) on ZMEDIA, resolves the
    file under Accounts/*/Media/<ZIDENTIFIER>/*/<ZFILENAME>.
    """
    if not os.path.exists(NOTE_STORE):
        return {}
    esc = note_title.replace("'", "''")
    q = (
        "SELECT media.ZIDENTIFIER, media.ZFILENAME "
        "FROM ZICCLOUDSYNCINGOBJECT att "
        "JOIN ZICCLOUDSYNCINGOBJECT media ON att.ZMEDIA = media.Z_PK "
        f"WHERE att.ZNOTE = (SELECT Z_PK FROM ZICCLOUDSYNCINGOBJECT "
        f"WHERE ZTITLE1='{esc}') AND media.ZFILENAME IS NOT NULL;"
    )
    r = subprocess.run(["sqlite3", NOTE_STORE, q], capture_output=True, text=True)
    out = {}
    for line in r.stdout.strip().splitlines():
        if "|" not in line:
            continue
        uuid, fn = line.split("|", 1)
        hits = glob.glob(os.path.join(MEDIA_GLOB, uuid, "*", fn))
        if not hits:
            hits = glob.glob(os.path.join(MEDIA_GLOB, uuid, "**", fn), recursive=True)
        if hits:
            try:
                h = hashlib.md5(open(hits[0], "rb").read()).hexdigest()[:10]
                out[h] = hits[0]
            except OSError:
                pass
    return out


def body_image_assoc(body: str) -> list[tuple[str, str | None]]:
    """Walk the body in document order; return [(md5[:10], owning_line)] per <img>.

    owning_line is the nearest preceding text bullet (normalised, raw text).
    """
    assoc, cur, pos = [], None, 0
    for m in re.finditer(r'<img[^>]*>', body):
        seg = body[pos:m.start()]
        lines = [ln.strip().lstrip(BULLETS + " ").strip()
                 for ln in importer.strip_html(seg).splitlines() if ln.strip()]
        lines = [ln for ln in lines if ln]
        if lines:
            cur = lines[-1]
        h = None
        src = re.search(r'src="data:image/[^;]+;base64,([^"]+)"', m.group(0))
        if src:
            try:
                h = hashlib.md5(base64.b64decode(src.group(1))).hexdigest()[:10]
            except Exception:
                h = None
        assoc.append((h, cur))
        pos = m.end()
    return assoc


# ── Write-back section ──────────────────────────────────────────────────────

def strip_islah(body: str) -> str:
    idx = body.find(MARK)
    if idx < 0:
        return body.rstrip()
    start = body.rfind("<div", 0, idx)
    if start < 0:
        start = idx
    sep = body.rfind("<div><br></div>", 0, start)
    if sep >= 0 and body[sep + len("<div><br></div>"):start].strip() == "":
        start = sep
    return body[:start].rstrip()


def islah_rows(entries: list[tuple[str, str]]) -> list[str]:
    """entries: [(display_id, bug_id)]  e.g. ('QG-1','qg#1')."""
    day = datetime.now().strftime("%Y-%m-%d")
    rows = [f"<div>{MARK}</div>"]
    for disp, bid in entries:
        rows.append(f"<div>✅ {disp} · tracked {day} · dev.hish.am/issue/{bid}</div>")
    return rows


def islah_section(entries: list[tuple[str, str]]) -> str:
    """Trailing section appended to an image-free source note."""
    return "\n<div><br></div>\n" + "\n".join(islah_rows(entries))


def companion_body(note_name: str, entries: list[tuple[str, str]]) -> str:
    """Full body for the sibling tracking note (image-safe)."""
    head = [
        f"<div><h1>{note_name} — Iṣlāḥ</h1></div>",
        f"<div>Tracked from the {note_name} note into the Iṣlāḥ ledger "
        f"(~/.aos/islah/bugs.yaml). Auto-maintained; safe to ignore.</div>",
        "<div><br></div>",
    ]
    return "\n".join(head + islah_rows(entries))


def display_id(bug_id: str) -> str:
    if "#" in bug_id:
        pfx, num = bug_id.split("#", 1)
        return f"{pfx.upper()}-{num}"
    return bug_id.upper()


# ── Core sync ───────────────────────────────────────────────────────────────

def sync_note(name: str, lg: Ledger, dry: bool = False) -> dict:
    slug = NOTE_APP.get(name, importer.app_from_title(name))
    body = get_body(name)
    items = importer.parse_items(importer.strip_html(body))

    # Existing state for dedup + attachment targeting.
    by_ref = {b.source_ref: b.id for b in lg.all() if b.source_ref}
    same_app = [b for b in lg.all() if b.app == slug]

    def fuzzy_hit(line: str):
        best_id, best = None, 0.0
        n = _norm(line)
        for b in same_app:
            for cand in (b.title, b.symptom):
                if not cand:
                    continue
                r = difflib.SequenceMatcher(None, n, _norm(cand)).ratio()
                if r > best:
                    best, best_id = r, b.id
        return (best_id, best) if best >= FUZZY_DUP else (None, best)

    new, dup, line_to_bug = [], [], {}
    for kind, line in items:
        key = importer.import_key(slug, line)
        title = line if len(line) <= 80 else line[:78] + "…"
        if key in by_ref:                          # exact source_ref match
            line_to_bug[_norm(line)] = by_ref[key]
            dup.append((kind, title, by_ref[key], "exact"))
            continue
        fid, score = fuzzy_hit(line)               # reworded-line safety net
        if fid:
            line_to_bug[_norm(line)] = fid
            dup.append((kind, title, fid, f"fuzzy {score:.2f}"))
            continue
        if dry:
            new.append((kind, title, "(new)"))
            continue
        bug = lg.add(title, app=slug, kind=kind, source="apple-notes",
                     source_ref=key, reporter="self", symptom=line, status="new")
        by_ref[key] = bug.id
        same_app.append(bug)
        line_to_bug[_norm(line)] = bug.id
        new.append((kind, title, bug.id))

    # Images: document-order body imgs → hash → exact Media original → owning bug.
    media = note_media_by_hash(name)
    assoc = body_image_assoc(body)
    attached, missing = [], 0
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    for h, line in assoc:
        path = media.get(h) if h else None
        if not path:
            missing += 1
            continue
        bug_id = line_to_bug.get(_norm(line or "")) if line else None
        ext = os.path.splitext(path)[1].lower() or ".img"
        stable = f"{(slug or 'x')}-{h}{ext}"
        dest = MEDIA_DIR / stable
        ref = f"/media?f={stable}"
        if dry:
            attached.append((stable, bug_id, line))
            continue
        if not dest.exists():
            dest.write_bytes(open(path, "rb").read())
        if bug_id:
            bug = lg.get(bug_id)
            atts = list(bug.attachments)
            if stable not in atts:
                atts.append(stable)
            proofs = list(bug.proof)
            if not any(p.get("ref") == ref for p in proofs):
                proofs.append({"kind": "screenshot", "ref": ref, "at": _now()})
            lg.update(bug_id, attachments=atts, proof=proofs)
        attached.append((stable, bug_id, line))

    # Write-back: list every item this note tracks (new + already-tracked).
    tracked_ids, seen = [], set()
    for _n, _t, bid in new:
        if bid not in ("(new)",) and bid not in seen:
            tracked_ids.append(bid); seen.add(bid)
    for _k, _t, bid, _how in dup:
        if bid not in seen:
            tracked_ids.append(bid); seen.add(bid)
    tracked_ids.sort(key=lambda b: (b.split("#")[0], int(b.split("#")[1]) if "#" in b else 0))
    entries = [(display_id(b), b) for b in tracked_ids]

    # Write-back — headless and image-safe.
    #   AppleScript `set body` is the only note-mutation primitive and it STRIPS
    #   inline images. So we only append into the source note when it has no
    #   images; otherwise the tracking marker goes into a sibling note whose body
    #   we can safely (re)write. Either way: fully headless, never corrupts.
    has_images = body.count("<img") > 0
    wrote_back = False
    wb_target = None
    if not dry and entries:
        if has_images:
            wb_target = upsert_companion(name, companion_body(name, entries))
            wrote_back = True
        else:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            (BACKUP_DIR / f"{name.replace('/', '_')}-{ts}.html").write_text(body)
            tmp = BACKUP_DIR / f".wb-{ts}.html"
            tmp.write_text(strip_islah(body) + islah_section(entries))
            set_body_from_file(name, str(tmp))
            tmp.unlink(missing_ok=True)
            wb_target = name
            wrote_back = True

    return {
        "note": name, "slug": slug, "total": len(items),
        "new": new, "dup": dup, "attached": attached,
        "media_found": len(media), "img_missing": missing,
        "has_images": has_images, "wrote_back": wrote_back,
        "wb_target": wb_target, "tracked": entries,
    }


def main():
    global FOLDER
    ap = argparse.ArgumentParser(prog="notes_folder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="import notes from the folder")
    s.add_argument("--note", help="only this note (default: all notes in folder)")
    s.add_argument("--folder", default=FOLDER)
    s.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    FOLDER = a.folder
    lg = Ledger()
    try:
        names = [a.note] if a.note else list_notes(a.folder, include_companions=False)
    except RuntimeError as e:
        print(f"⚠ {e}")
        sys.exit(2)

    tag = "would import" if a.dry_run else "imported"
    for name in names:
        try:
            r = sync_note(name, lg, dry=a.dry_run)
        except RuntimeError as e:
            print(f"\n=== {name} ===\n⚠ {e}")
            continue
        print(f"\n=== {name}  →  app: {r['slug'] or '(unset)'}  ·  "
              f"{r['total']} items · {r['media_found']} images in store ===")
        for kind, title, bid in r["new"]:
            print(f"  + [{kind}] {title}   ({tag} {bid})")
        for kind, title, bid, how in r["dup"]:
            print(f"  = [{kind}] {title}   (already tracked → {bid}, {how})")
        if r["attached"]:
            print(f"  screenshots ({len(r['attached'])}):")
            for stable, bug_id, line in r["attached"]:
                tgt = bug_id or "(unassigned)"
                print(f"      {stable}  → {tgt}  · {(line or '')[:48]}")
        if r["img_missing"]:
            print(f"  ⚠ {r['img_missing']} body image(s) had no Media match")
        if a.dry_run:
            wb = f"dry ({'companion note' if r['has_images'] else 'inline append'})"
        elif r["wrote_back"]:
            where = "companion note" if r["has_images"] else "inline"
            wb = f"yes → {r['wb_target']!r} ({where})"
        else:
            wb = "no"
        print(f"  {len(r['new'])} new · {len(r['dup'])} already tracked · write-back: {wb}")


if __name__ == "__main__":
    main()
