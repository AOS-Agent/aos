#!/usr/bin/env python3
"""
Dev board — a real, mobile-first, Linear-style issue tracker for your apps.

Self-contained (Python stdlib only). Charcoal/light themes with a toggle, a
slide-in mobile drawer, grouped issue list, issue detail with inline editing,
a New-issue composer, search, and keyboard shortcuts. Every action writes to
the live ledger.

    python3 board.py                     # http://127.0.0.1:7610
    python3 board.py --snapshot a.html   # static list snapshot
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from http import cookies as _cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import ALL_STATES, MEDIA_DIR, Ledger  # noqa: E402

PORT = 7610
APPS = [("quran-garden", "Quran Garden"), ("deenoverdunya", "Deen Over Dunya"), ("qg-web", "QG Web")]

ASSETS_DIR = Path(os.path.expanduser("~/.aos/islah/app/assets"))
BRAIN_DIR = Path(os.path.expanduser("~/.aos/islah/braindump"))
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif"}
VID_EXT = {".mp4", ".mov", ".webm", ".m4v"}


def _retry_io(fn, attempts=4):
    """AOS-X can throw transient EPERM — retry with backoff before giving up."""
    for i in range(attempts - 1):
        try:
            return fn()
        except PermissionError:
            time.sleep(0.15 * (i + 1))
    return fn()

# --- auth: operator passcode (2-3 trusted people). Public gets /submit only. ---
PASSCODE = os.environ.get("ISLAH_PASSCODE", "6777")
_SECRET_FILE = Path(os.path.expanduser("~/.aos/islah/.secret"))
PUBLIC_PATHS = ("/login", "/submit", "/thanks", "/manifest.json", "/icon-180.png", "/icon-512.png")


def _secret():
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _SECRET_FILE.exists():
        _SECRET_FILE.write_text(os.urandom(24).hex())
        os.chmod(_SECRET_FILE, 0o600)
    return _SECRET_FILE.read_text().strip()


def _op_token():
    return hmac.new(_secret().encode(), b"operator", hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- multipart
def parse_multipart(ctype, body):
    """Minimal, robust multipart/form-data parser (Python 3.13+ has no cgi).

    Returns (fields, files): fields is a parse_qs-shaped dict of name -> [str],
    files is a list of (field_name, original_filename, raw_bytes).
    """
    fields, files = {}, []
    m = re.search(r'boundary="?([^";,]+)"?', ctype or "")
    if not m:
        return fields, files
    delim = b"--" + m.group(1).encode()
    for chunk in body.split(delim)[1:]:
        if chunk[:2] == b"--":          # closing marker
            break
        if chunk[:2] == b"\r\n":
            chunk = chunk[2:]
        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        name = fname = None
        for line in head.decode("utf-8", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                nm = re.search(r'\bname="([^"]*)"', line)
                fn = re.search(r'\bfilename="([^"]*)"', line)
                if nm:
                    name = nm.group(1)
                if fn:
                    fname = fn.group(1)
        if name is None:
            continue
        if fname is not None:
            if fname and data:
                files.append((name, fname, data))
        else:
            fields.setdefault(name, []).append(data.decode("utf-8", "replace"))
    return fields, files


def save_uploads(files):
    """Persist uploaded image/video parts to the media dir with safe unique names.

    Returns [(saved_filename, proof_kind)] where proof_kind is screenshot|video.
    """
    saved = []
    if not files:
        return saved
    _retry_io(lambda: MEDIA_DIR.mkdir(parents=True, exist_ok=True))
    for field, fname, data in files:
        if field != "files" or not fname or not data:
            continue
        ext = re.sub(r"[^a-z0-9.]", "", os.path.splitext(fname)[1].lower())[:8]
        if ext in IMG_EXT:
            kind = "screenshot"
        elif ext in VID_EXT:
            kind = "video"
        else:
            continue
        out = f"up-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}{ext}"
        p = MEDIA_DIR / out
        _retry_io(lambda p=p, data=data: p.write_bytes(data))
        saved.append((out, kind))
    return saved


# ---------------------------------------------------------------- brain dump
def pending_dumps():
    try:
        return sorted(BRAIN_DIR.glob("*.md"), reverse=True)
    except OSError:
        return []


def save_dump(text, saved_files):
    _retry_io(lambda: BRAIN_DIR.mkdir(parents=True, exist_ok=True))
    now = datetime.now(timezone.utc)
    stem = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    p = BRAIN_DIR / f"{stem}.md"
    if p.exists():
        p = BRAIN_DIR / f"{stem}-{secrets.token_hex(2)}.md"
    lines = ["---", "type: braindump", f"captured: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}", "source: board"]
    if saved_files:
        lines.append("attachments:")
        lines += [f"  - {n}" for n, _ in saved_files]
    lines += ["---", "", text.strip(), ""]
    _retry_io(lambda: p.write_text("\n".join(lines)))
    return p


def _dump_meta(p):
    """(display timestamp, first-line preview) for a queued dump file."""
    stem = p.stem
    ts = stem[:16].replace("T", " · ").replace("-", "/", 2) if len(stem) >= 16 else stem
    try:
        txt = _retry_io(p.read_text)
    except OSError:
        return ts, ""
    body = re.sub(r"^---.*?---\s*", "", txt, flags=re.S)
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "(attachments only)")
    return ts, first[:110]


# ---------------------------------------------------------------- app icon (Pillow)
def _icon_font(px):
    from PIL import ImageFont
    for path in ("/System/Library/Fonts/SFArabic.ttf",
                 "/System/Library/Fonts/GeezaPro.ttc",
                 "/System/Library/Fonts/Supplemental/AlBayan.ttc",
                 "/System/Library/Fonts/Supplemental/Damascus.ttc"):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, px)
            except Exception:
                continue
    return None


def make_icon(size, full_bleed=False):
    """Refined amber rounded-square icon with the D mark, supersampled 4×.

    full_bleed=True fills the whole square (iOS masks apple-touch-icons itself);
    otherwise corners are rounded and transparent (manifest / Android).
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    S = size * 4
    # vertical amber gradient, slightly eased
    top, bot = (243, 198, 127), (154, 94, 22)
    grad = Image.new("RGB", (1, S))
    gpx = grad.load()
    for y in range(S):
        t = (y / (S - 1)) ** 1.15
        gpx[0, y] = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
    img = grad.resize((S, S))
    # soft radial highlight from the top
    hl = Image.new("L", (S, S), 0)
    ImageDraw.Draw(hl).ellipse([-S * 0.35, -S * 0.78, S * 1.35, S * 0.52], fill=85)
    hl = hl.filter(ImageFilter.GaussianBlur(S * 0.09))
    img.paste(Image.new("RGB", (S, S), (255, 236, 200)), (0, 0), hl)
    # warm vignette pooling at the bottom
    vg = Image.new("L", (S, S), 0)
    ImageDraw.Draw(vg).ellipse([-S * 0.4, S * 0.58, S * 1.4, S * 1.9], fill=70)
    vg = vg.filter(ImageFilter.GaussianBlur(S * 0.12))
    img.paste(Image.new("RGB", (S, S), (108, 60, 8)), (0, 0), vg)
    img = img.convert("RGBA")
    # the D mark
    glyph = Image.new("L", (S, S), 0)
    gd = ImageDraw.Draw(glyph)
    font = _icon_font(int(S * 0.54))
    drew = False
    if font:
        try:
            try:  # SF Arabic is a variable font — ask for a semibold cut
                font.set_variation_by_axes([640])
            except Exception:
                pass
            sw = max(1, int(S * 0.004))
            bb = gd.textbbox((0, 0), "D", font=font, stroke_width=sw)
            w, h = bb[2] - bb[0], bb[3] - bb[1]
            gd.text((S / 2 - w / 2 - bb[0], S / 2 - h / 2 - bb[1] - S * 0.012), "D",
                    font=font, fill=255, stroke_width=sw, stroke_fill=255)
            drew = True
        except Exception:
            pass
    if not drew:  # hand-drawn alif + hamza fallback
        bw = S * 0.07
        gd.rounded_rectangle([S / 2 - bw / 2, S * 0.30, S / 2 + bw / 2, S * 0.66], radius=bw / 2, fill=255)
        gd.ellipse([S / 2 - S * 0.052, S * 0.71, S / 2 + S * 0.052, S * 0.815], fill=255)
    # soft cast shadow under the glyph, then the ink itself
    sh = glyph.filter(ImageFilter.GaussianBlur(S * 0.013)).point(lambda a: a * 50 // 255)
    shadow = Image.new("RGBA", (S, S), (58, 31, 4, 0))
    shadow.putalpha(sh)
    img = Image.alpha_composite(img, ImageChops.offset(shadow, 0, int(S * 0.014)))
    ink = Image.new("RGBA", (S, S), (26, 17, 6, 0))
    ink.putalpha(glyph)
    img = Image.alpha_composite(img, ink)
    # hairline inner highlight so the square reads as a surface, not a flat fill
    r = 0 if full_bleed else int(S * 0.225)
    stroke = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(stroke).rounded_rectangle(
        [S * 0.008, S * 0.008, S * 0.992, S * 0.992],
        radius=max(r - int(S * 0.008), 0), outline=(255, 245, 225, 58), width=max(2, int(S * 0.006)))
    img = Image.alpha_composite(img, stroke)
    if r:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=255)
        img.putalpha(mask)
    out = img.resize((size, size), Image.LANCZOS)
    return out.convert("RGB") if full_bleed else out


def ensure_icons():
    try:
        _retry_io(lambda: ASSETS_DIR.mkdir(parents=True, exist_ok=True))
        for fname, size, full in (("icon-180.png", 180, True), ("icon-512.png", 512, False)):
            p = ASSETS_DIR / fname
            if p.exists() and p.stat().st_size > 0:
                continue
            img = make_icon(size, full_bleed=full)
            tmp = p.with_suffix(".tmp")
            _retry_io(lambda img=img, tmp=tmp: img.save(tmp, "PNG"))
            _retry_io(lambda tmp=tmp, p=p: tmp.replace(p))
    except Exception as e:  # never let icon art take the board down
        print(f"[islah] icon generation failed: {e}", file=sys.stderr)


MANIFEST = {
    "name": "Dev", "short_name": "Dev",
    "description": "Report a bug or idea for Quran Garden & Deen Over Dunya.",
    "start_url": "/submit", "scope": "/", "display": "standalone",
    "background_color": "#0d0d0f", "theme_color": "#0d0d0f",
    "icons": [
        {"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
    ],
}

GROUPS = [
    ("needs-decision", "Decide"), ("reopened", "Reopened"),
    ("awaiting-approval", "Approve"), ("confirmed", "Go-ahead"),
    ("fixing", "In progress"), ("verifying", "Verifying"),
    ("triaging", "Triaging"), ("needs-info", "Needs info"), ("new", "To triage"),
    ("approved", "Approved"), ("shipped", "Shipped"),
    ("wont-fix", "Cancelled"), ("duplicate", "Duplicate"),
]
GROUP_LABEL = dict(GROUPS)
STATUS_ORDER = [s for s, _ in GROUPS]
SEV = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low", 0: "No priority"}

QUESTION = {
    "needs-decision": "Should we do this?",
    "confirmed": "Ready to start the fix?",
    "awaiting-approval": "Approve this fix and ship it?",
    "reopened": "This came back \u2014 fix it again?",
    "new": "Start triage on this?",
    "triaging": "Anything to send back?",
}
CONSEQUENCE = {
    "needs-decision": "Yes \u2192 it joins the fix queue and an agent picks it up. Keep as-is \u2192 it\u2019s closed, current behaviour stays.",
    "confirmed": "Go ahead \u2192 an agent starts the fix and brings you the diff to approve.",
    "awaiting-approval": "Approve \u2192 the fix ships in the next build. Send back \u2192 it returns to the agent with your note.",
    "reopened": "Yes \u2192 an agent re-opens the fix with the failure context attached.",
    "new": "Start triage \u2192 an agent investigates and fills in the root cause and proposed fix.",
}

ACTIONS = {
    "needs-decision": [("Yes — do it", "greenlight", "primary"),
                       ("Keep as-is", "dismiss", "ghost"), ("Send back", "sendback", "ghost")],
    "confirmed": [("Go ahead, fix it", "greenlight", "primary"), ("Not now", "dismiss", "ghost")],
    "awaiting-approval": [("Approve &amp; ship", "approve", "primary"), ("Send back", "sendback", "ghost")],
    "reopened": [("Fix it again", "greenlight", "primary")],
    "new": [("Start triage", "triage", "primary"), ("Send back", "sendback", "ghost")],
    "triaging": [("Send back", "sendback", "ghost")],
}


def esc(x):
    return html.escape(str(x)) if x is not None else ""


def disp_id(bid):
    if "#" in bid:
        p, n = bid.split("#", 1)
        return f"{p.upper()}-{n}"
    return bid.upper()


# ---------------------------------------------------------------- glyphs
def status_icon(status):
    m, a, g = "var(--muted2)", "var(--accent)", "var(--done)"

    def ring(c, d=""):
        return f'<circle cx="7" cy="7" r="5" fill="none" stroke="{c}" stroke-width="1.6"{d}/>'

    def w(inner):
        return f'<svg class="sicon" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">{inner}</svg>'
    if status == "new":
        return w(ring(m, ' stroke-dasharray="1.6 1.9"'))
    if status in ("triaging", "needs-info"):
        return w(ring(m))
    if status == "confirmed":
        return w(ring(a) + f'<circle cx="7" cy="7" r="2" fill="{a}"/>')
    if status in ("fixing", "verifying"):
        return w(ring(a) + f'<path d="M7 7 L7 2 A5 5 0 0 1 12 7 Z" fill="{a}"/>')
    if status == "needs-decision":
        return w(ring(a) + f'<rect x="6.2" y="3.6" width="1.6" height="4" rx=".8" fill="{a}"/>'
                 f'<rect x="6.2" y="8.7" width="1.6" height="1.7" rx=".8" fill="{a}"/>')
    if status == "awaiting-approval":
        return w(ring(g, ' stroke-dasharray="1.6 1.9"') + f'<path d="M7 7 L7 2 A5 5 0 0 1 12 7 Z" fill="{g}"/>')
    if status in ("approved", "shipped"):
        return w(f'<circle cx="7" cy="7" r="6" fill="{g}"/><path d="M4.3 7.2 L6.2 9 L9.7 5.1" fill="none" '
                 f'stroke="var(--on-accent)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>')
    if status in ("wont-fix", "duplicate"):
        return w(f'<circle cx="7" cy="7" r="6" fill="{m}" opacity=".5"/>'
                 f'<path d="M5 5 L9 9 M9 5 L5 9" stroke="var(--on-accent)" stroke-width="1.4" stroke-linecap="round"/>')
    return w(ring(m))


def prio_glyph(sev):
    if sev == 1:
        return ('<svg class="pico" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">'
                '<rect x="1.5" y="1.5" width="11" height="11" rx="2.5" fill="var(--urgent)"/>'
                '<rect x="6.2" y="3.8" width="1.6" height="4.2" rx=".8" fill="var(--on-accent)"/>'
                '<rect x="6.2" y="9" width="1.6" height="1.6" rx=".8" fill="var(--on-accent)"/></svg>')
    lvl = {2: 3, 3: 2, 4: 1}.get(sev, 0)
    if lvl == 0:
        dashes = "".join(f'<rect x="{1.5+i*4.2:.1f}" y="6.3" width="2.6" height="1.4" rx=".7" fill="var(--muted2)"/>'
                         for i in range(3))
        return f'<svg class="pico" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">{dashes}</svg>'
    bars = ""
    for i, h in enumerate([4, 7, 10]):
        on = i < lvl
        col = "var(--ink2)" if on else "var(--faint)"
        bars += f'<rect x="{1.5+i*4.2:.1f}" y="{12.5-h:.1f}" width="2.6" height="{h}" rx="1" fill="{col}"/>'
    return f'<svg class="pico" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">{bars}</svg>'


def app_dot(app):
    col = {"quran-garden": "#5aa17f", "deenoverdunya": "var(--accent)", "qg-web": "#c08457"}.get(app, "var(--muted2)")
    return f'<span class="adot" style="background:{col}"></span>'


CSS = r"""
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}body{margin:0}
:root{
 --bg:#0d0d0f;--main:#131315;--sidebar:#0a0a0c;--elev:#1c1c20;--rowhover:#191a1d;
 --line:rgba(255,255,255,.075);--line2:rgba(255,255,255,.13);
 --ink:#f5f5f6;--ink2:#cfcfd4;--muted:#9a9aa3;--muted2:#7f7f89;--faint:#4b4b54;
 --accent:#e0a44c;--accent2:#f0be74;--on-accent:#14100a;--done:#6cc08a;--urgent:#e5674a;
 --ring:color-mix(in srgb,var(--accent) 55%,transparent);
 --font:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",system-ui,sans-serif;
 --mono:ui-monospace,"SF Mono",Menlo,monospace;--sh:0 12px 40px -12px rgba(0,0,0,.6);color-scheme:dark;}
@media(prefers-color-scheme:light){:root{
 --bg:#f6f6f7;--main:#ffffff;--sidebar:#f0f0f1;--elev:#ffffff;--rowhover:#f2f2f4;
 --line:rgba(0,0,0,.085);--line2:rgba(0,0,0,.14);--ink:#18181b;--ink2:#3f3f46;
 --muted:#5f5f68;--muted2:#74747d;--faint:#c6c6cc;--accent:#b57117;--accent2:#986014;--on-accent:#fff;
 --done:#3f8f5c;--urgent:#c24a2e;--sh:0 12px 40px -14px rgba(20,20,25,.28);color-scheme:light;}}
:root[data-theme=light]{--bg:#f6f6f7;--main:#ffffff;--sidebar:#f0f0f1;--elev:#ffffff;--rowhover:#f2f2f4;
 --line:rgba(0,0,0,.085);--line2:rgba(0,0,0,.14);--ink:#18181b;--ink2:#3f3f46;--muted:#5f5f68;
 --muted2:#74747d;--faint:#c6c6cc;--accent:#b57117;--accent2:#986014;--on-accent:#fff;--done:#3f8f5c;--urgent:#c24a2e;
 --sh:0 12px 40px -14px rgba(20,20,25,.28);color-scheme:light;}
:root[data-theme=dark]{--bg:#0d0d0f;--main:#131315;--sidebar:#0a0a0c;--elev:#1c1c20;--rowhover:#191a1d;
 --line:rgba(255,255,255,.075);--line2:rgba(255,255,255,.13);--ink:#f5f5f6;--ink2:#cfcfd4;--muted:#9a9aa3;
 --muted2:#7f7f89;--faint:#4b4b54;--accent:#e0a44c;--accent2:#f0be74;--on-accent:#14100a;--done:#6cc08a;--urgent:#e5674a;
 --sh:0 12px 40px -12px rgba(0,0,0,.6);color-scheme:dark;}

body{font-family:var(--font);background:var(--bg);color:var(--ink2);font-size:13.5px;line-height:1.5;
 letter-spacing:-.011em;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
button{font-family:inherit}
::placeholder{color:var(--muted2);opacity:1}
::selection{background:color-mix(in srgb,var(--accent) 28%,transparent)}
:focus-visible{outline:2px solid var(--ring);outline-offset:1px}
.row:focus-visible,.nav:focus-visible{outline-offset:-2px;border-radius:0}
.nav:focus-visible{border-radius:7px}
textarea.f:focus,.note-in:focus,.sel:focus,.search:focus-within{border-color:var(--ring);outline:none;
 box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 12%,transparent)}
.search input:focus{outline:none}
.app{display:flex;height:100vh;height:100dvh;overflow:hidden}

/* ---- mobile top bar (hidden on desktop) ---- */
.topbar{display:none}
@media(max-width:760px){.topbar{display:flex;align-items:center;gap:10px;height:52px;padding:0 12px;
 position:sticky;top:0;z-index:30;background:var(--main);border-bottom:.5px solid var(--line)}
 .topbar .brand{font-weight:560;color:var(--ink);letter-spacing:-.02em;font-size:15px;display:flex;gap:8px;align-items:center}
 .topbar .brand .mark{width:22px;height:22px;border-radius:6px;background:linear-gradient(150deg,var(--accent),#a5691f);
  display:grid;place-items:center;color:var(--on-accent);font-weight:700;font-size:12px}
 .topbar .grow{flex:1}.iconbtn{width:36px;height:36px;border-radius:8px;border:.5px solid var(--line2);
  background:var(--elev);color:var(--ink2);display:grid;place-items:center;font-size:16px;cursor:pointer}}

/* ---- sidebar ---- */
.side{width:238px;flex:none;background:var(--sidebar);border-right:.5px solid var(--line);
 padding:14px 10px;display:flex;flex-direction:column;gap:2px;color:var(--muted);overflow-y:auto}
.ws{display:flex;align-items:center;gap:9px;padding:6px 8px 12px}
.ws .mark{width:22px;height:22px;border-radius:6px;background:linear-gradient(150deg,var(--accent),#a5691f);
 display:grid;place-items:center;color:var(--on-accent);font-weight:700;font-size:12px}
.ws .name{color:var(--ink);font-weight:560;letter-spacing:-.02em;font-size:14px}
.ws .tg{margin-left:auto;cursor:pointer;color:var(--muted2);font-size:15px;background:none;border:none;padding:4px}
.ws .tg:hover{color:var(--ink2)}
.navlabel{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted2);padding:14px 8px 5px;font-weight:560}
.nav{display:flex;align-items:center;gap:9px;padding:7px 8px;border-radius:7px;color:var(--muted);font-weight:460;
 cursor:pointer;letter-spacing:-.01em}
.nav:hover{background:var(--rowhover);color:var(--ink2)}
.nav:active{background:var(--elev)}
.nav.active{background:var(--rowhover);color:var(--ink)}
.nav .ic{width:15px;height:15px;flex:none;opacity:.85}
.nav .ct{margin-left:auto;font-size:11.5px;color:var(--muted2);font-variant-numeric:tabular-nums}
.side .newbtn{margin:12px 4px 4px;display:flex;align-items:center;justify-content:center;gap:7px;padding:8px;
 border-radius:8px;border:.5px solid var(--line2);background:var(--elev);color:var(--ink);font-weight:520;
 font-size:13px;cursor:pointer;transition:border-color .12s}
.side .newbtn:hover{border-color:var(--muted)}
.side .newbtn:active{background:var(--rowhover)}
.side .spacer{flex:1}
.side .foot{font-family:var(--mono);font-size:10px;color:var(--muted2);padding:8px;letter-spacing:.02em}

/* mobile drawer behaviour */
@media(max-width:760px){
 .side{position:fixed;top:0;bottom:0;left:0;z-index:50;transform:translateX(-100%);
  transition:transform .22s ease;box-shadow:var(--sh);width:264px;
  padding-bottom:calc(14px + env(safe-area-inset-bottom))}
 body.nav-open .side{transform:translateX(0)}
 .nav{padding:11px 10px}
 .scrim{position:fixed;inset:0;background:rgba(0,0,0,.5);opacity:0;pointer-events:none;transition:opacity .2s;z-index:40}
 body.nav-open .scrim{opacity:1;pointer-events:auto}}
@media(min-width:761px){.scrim{display:none}}

/* ---- main ---- */
.main{flex:1;background:var(--main);min-width:0;min-height:0;display:flex;flex-direction:column}
.hdr{min-height:48px;flex:none;display:flex;align-items:center;gap:12px;padding:8px 20px;
 border-bottom:.5px solid var(--line);background:var(--main);z-index:20;flex-wrap:wrap}
@media(max-width:760px){.hdr{padding:8px 12px}}
.hdr .title{color:var(--ink);font-weight:540;letter-spacing:-.02em;font-size:14.5px}
.hdr .ct{color:var(--muted2);font-size:12px;font-variant-numeric:tabular-nums}
.hdr .grow{flex:1}
.search{display:flex;align-items:center;gap:7px;background:var(--bg);border:.5px solid var(--line2);border-radius:8px;
 padding:6px 10px;min-width:150px;max-width:280px;flex:1;transition:border-color .12s,box-shadow .12s}
.search input{border:none;background:none;outline:none;color:var(--ink);font-family:inherit;font-size:13px;width:100%}
.search .k{color:var(--muted2);display:grid;place-items:center}
.hdr .newtop{display:inline-flex;align-items:center;gap:6px;background:var(--accent);color:var(--on-accent);
 border:none;border-radius:8px;padding:7px 12px;font-weight:560;font-size:13px;cursor:pointer;white-space:nowrap}
.hdr .newtop:hover{background:var(--accent2)}
.hdr .newtop:active{transform:translateY(.5px)}
.hdr .tg{background:none;border:.5px solid var(--line2);border-radius:8px;width:34px;height:32px;color:var(--muted);
 cursor:pointer;display:grid;place-items:center}
.hdr .tg:hover{color:var(--ink2)}
@media(max-width:760px){.hdr .newtop,.hdr .tg{display:none}.search input{font-size:16px}}

.scroll{overflow-y:auto;flex:1;min-height:0}

/* ---- group + rows ---- */
.group-h{display:flex;align-items:center;gap:9px;padding:8px 20px;background:color-mix(in srgb,var(--main) 84%,var(--bg));
 border-bottom:.5px solid var(--line);position:sticky;top:0;z-index:8}
@media(max-width:760px){.group-h{padding:8px 12px}}
.group-h{padding:16px 20px 7px!important;background:var(--main)!important}
.group-h .lbl{color:var(--muted);font-weight:650;letter-spacing:.09em;font-size:11px;text-transform:uppercase}
.group-h .ct{color:var(--muted2);font-size:11px;font-variant-numeric:tabular-nums;font-weight:600}
@media(max-width:760px){.group-h{padding:18px 16px 7px!important}}
.fv{font-size:14.5px;line-height:1.62;color:var(--ink2);white-space:pre-wrap;word-break:break-word}
.fv.clamped{display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}
.showmore{background:none;border:0;color:var(--accent);font-size:12.5px;font-weight:600;
 cursor:pointer;padding:7px 0;font-family:inherit}
.showmore:hover{text-decoration:underline}
/* ---- Review: immersive full-screen flow (no board chrome) ---- */
html:has(.rvwrap),body:has(.rvwrap){height:100dvh;overflow:hidden;overscroll-behavior:none}
body:has(.rvwrap) .topbar,body:has(.rvwrap) .side,body:has(.rvwrap) .scrim{display:none!important}
body:has(.rvwrap) .app{display:block;height:100dvh;overflow:hidden;padding:0;margin:0}
body:has(.rvwrap) .main{display:block;height:100dvh;overflow:hidden;padding:0;margin:0;width:100%;max-width:100%;min-width:0}
.rvwrap{display:flex;flex-direction:column;height:100dvh;width:100%;max-width:680px;margin:0 auto;background:var(--main);overflow-x:hidden}
.rvwrap *{max-width:100%;box-sizing:border-box}
.rvnote,.rvtitle,.rvq,.fv{overflow-wrap:anywhere}
.rvhdr{flex:none;display:flex;align-items:center;gap:12px;flex-wrap:wrap;
 padding:calc(10px + env(safe-area-inset-top)) 16px 0;background:var(--main)}
.rvback,.rvexit{color:var(--muted2);text-decoration:none;font-size:19px;line-height:1;
 min-width:44px;min-height:44px;display:grid;place-items:center}
.rvback:hover,.rvexit:hover{color:var(--ink)}
.rvback.ghosted{opacity:.22;pointer-events:none}
.rvexit{margin-left:0}
.rvcount{margin-left:auto;font-size:14.5px;color:var(--ink);font-weight:650;font-variant-numeric:tabular-nums}
.rvcount i{color:var(--muted2);font-style:normal;font-weight:500}
.rvid{font-family:var(--mono);font-size:12px;color:var(--muted2)}
.rvbar{flex:1 1 100%;height:3px;background:var(--line);border-radius:2px;overflow:hidden;margin:9px 0 0}
.rvfill{height:100%;background:var(--accent);transition:width .35s ease}
.rvscroll{flex:1;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
.rvstage{position:relative;min-height:100%}
.rvsw{position:absolute;inset:0;display:flex;align-items:flex-start;padding:40px 26px;opacity:0;
 pointer-events:none;transition:opacity .1s;font-weight:650;font-size:17px;color:#fff;z-index:1}
.rvsw.pos{background:var(--done);justify-content:flex-start}
.rvsw.neg{background:var(--urgent);justify-content:flex-end}
.rvcard{position:relative;z-index:2;background:var(--main);padding:18px 18px 30px;
 min-height:100%;transition:transform .2s ease}
.rv.swiping .rvcard{transition:none}
.rvnote{margin:0 0 20px;padding:15px 17px;border-left:3px solid var(--accent);
 background:color-mix(in srgb,var(--accent) 7%,var(--main));border-radius:0 12px 12px 0;
 font-size:16px;line-height:1.6;color:var(--ink2)}
.rvnote cite{display:block;margin-top:9px;font-size:10.5px;font-style:normal;color:var(--muted2);
 text-transform:uppercase;letter-spacing:.09em;font-weight:600}
.rvtitle{font-size:24px;font-weight:640;letter-spacing:-.022em;line-height:1.26;margin:0 0 16px;color:var(--ink)}
.rvq{font-size:18px;font-weight:640;color:var(--ink);letter-spacing:-.015em;line-height:1.35;
 margin:0 0 22px;padding:15px 0 0;border-top:.5px solid var(--line)}
.rvshots{display:flex;flex-direction:column;gap:10px}
.rvshot{width:100%;border-radius:12px;border:1px solid var(--line2);cursor:zoom-in;display:block}
.rvstrip{display:flex;gap:10px;overflow-x:auto;padding:2px 0 8px;-webkit-overflow-scrolling:touch;
 scroll-snap-type:x proximity}
.rvpick{margin:0;flex:none;scroll-snap-align:start}
.rvpickbtn{position:relative;padding:0;border:1px solid var(--line2);background:none;border-radius:11px;
 overflow:hidden;cursor:pointer;display:block}
.rvpickbtn:active{transform:scale(.97)}
.rvpickbtn img{display:block;height:150px;width:auto;max-width:210px;object-fit:cover}
.rvplus{position:absolute;right:6px;bottom:6px;width:27px;height:27px;border-radius:50%;
 background:var(--accent);color:var(--on-accent);font-weight:700;font-size:17px;
 display:grid;place-items:center;line-height:1}
.rvfull{display:inline-block;margin-top:22px;color:var(--muted2);text-decoration:none;font-size:13px}
.rvact{flex:none;background:var(--main);border-top:.5px solid var(--line);
 padding:13px 16px calc(13px + env(safe-area-inset-bottom));box-shadow:0 -10px 28px rgba(0,0,0,.14)}
.rvbtns{display:flex;gap:10px;flex-wrap:wrap}
.rvbtns .btn{flex:1 1 0;min-width:0;min-height:50px;font-size:15.5px;justify-content:center;font-weight:600}
.rvcons{margin-top:10px;font-size:12.5px;color:var(--muted);line-height:1.5}
.rvskip{display:block;text-align:center;margin-top:10px;color:var(--muted);text-decoration:none;
 font-size:14px;font-weight:560;min-height:44px;line-height:44px}
.rvdone{text-align:center;padding:80px 20px}
.rvdone .big{font-size:26px;font-weight:640;color:var(--ink);margin-bottom:8px}
.rvdone p{color:var(--muted);margin-bottom:22px}
.lb{position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.94);display:none;
 align-items:center;justify-content:center;padding:16px;cursor:zoom-out}
.lb.on{display:flex}
.lb img{max-width:100%;max-height:100%;border-radius:8px}
@media(max-width:760px){.rvtitle{font-size:23px}.rvcard{padding:16px 16px 28px}}

/* ---- board-wide mobile pass ---- */
html,body{overflow-x:hidden}
@media(max-width:760px){
 .topbar{padding-top:env(safe-area-inset-top)}
 .main{padding-bottom:calc(24px + env(safe-area-inset-bottom))}
 .nav{min-height:44px}
 .detail{display:block}
 .dprops{margin-top:26px;padding-top:20px;border-top:.5px solid var(--line)}
 .modal{position:fixed;inset:auto 0 0 0;max-height:92dvh;border-radius:18px 18px 0 0;
  padding-bottom:calc(16px + env(safe-area-inset-bottom));overflow-y:auto}
 .sel,.note-in,.f,input[type=text]{font-size:16px}
}
.dtitle{font-size:26px;font-weight:640;letter-spacing:-.022em;line-height:1.22;color:var(--ink);margin:8px 0 18px}
@media(max-width:760px){.dtitle{font-size:23px;margin:6px 0 16px}}
.decide{border:1px solid var(--line2);background:color-mix(in srgb,var(--accent) 7%,var(--main));
 border-radius:14px;padding:17px 17px 15px;margin:0 0 24px}
.dq{font-size:17.5px;font-weight:640;color:var(--ink);letter-spacing:-.015em;margin-bottom:14px;line-height:1.3}
.dacts{display:flex;gap:9px;flex-wrap:wrap}
.dacts .btn{flex:1 1 auto;min-width:148px;justify-content:center}
.dacts .note-in{flex:1 1 100%;margin-top:6px}
@media(max-width:760px){.decide{padding:16px 14px 14px;border-radius:13px}
 .dacts{flex-direction:column}.dacts .btn{width:100%;min-height:46px;font-size:15px}
 .dq{font-size:18px}}
.dcons{margin-top:12px;font-size:12.5px;color:var(--muted);line-height:1.55}
.more{margin:2px 0 18px}
.more>summary{cursor:pointer;font-size:12.5px;color:var(--muted2);padding:8px 0;list-style:none;user-select:none}
.more>summary::-webkit-details-marker{display:none}
.more>summary::before{content:"+ "}
.more[open]>summary::before{content:"\u2212 "}
.more>summary:hover{color:var(--ink2)}
.row{position:relative;overflow:hidden;border-bottom:.5px solid var(--line)}
.rowinner{display:flex;align-items:center;gap:12px;padding:13px 20px;background:var(--main);position:relative;
 z-index:1;transition:transform .2s ease;text-decoration:none;color:inherit;cursor:pointer}
.rowinner:hover{background:var(--rowhover)}
.row.swiping .rowinner{transition:none}
.swipebg{position:absolute;top:0;bottom:0;left:0;right:0;display:flex;align-items:center;opacity:0;
 transition:opacity .1s;font-weight:650;font-size:15px;color:#fff;padding:0 24px}
.swipebg.pos{background:var(--done);justify-content:flex-start}
.swipebg.neg{background:var(--urgent);justify-content:flex-end}
.rmain{display:flex;flex-direction:column;gap:3px;flex:1;min-width:0}
.rsub{font-size:12.5px;color:var(--muted);display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.utag{flex:none;font-size:11.5px;font-weight:600;color:var(--urgent);white-space:nowrap;
 background:color-mix(in srgb,var(--urgent) 14%,transparent);
 border:.5px solid color-mix(in srgb,var(--urgent) 40%,var(--line2));padding:4px 10px;border-radius:999px}
.row:active{background:var(--elev)}
.row:hover .rid{color:var(--muted)}
@media(max-width:760px){.rowinner{padding:16px 16px;gap:12px}.rsub{font-size:13px}}
.sicon,.pico{flex:none;display:block}
.row .rid{font-family:var(--mono);font-size:11.5px;color:var(--muted2);flex:none;width:54px;letter-spacing:-.02em}
@media(max-width:520px){.row .rid{display:none}}
.row .rtitle{color:var(--ink);font-weight:500;font-size:15px;line-height:1.32;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media(max-width:760px){.row .rtitle{font-size:16.5px;white-space:normal;overflow:visible;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}}
.row .rtitle .cf{color:var(--urgent);font-weight:600;font-size:11px;margin-left:9px;letter-spacing:.02em}
.row .meta{display:flex;align-items:center;gap:12px;flex:none;color:var(--muted2)}
.chip{font-size:10.5px;color:var(--muted);border:.5px solid var(--line2);padding:2px 7px;border-radius:5px;white-space:nowrap}
@media(max-width:520px){.chip{display:none}}
.adot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none}
.rdate{font-size:11px;color:var(--muted2);font-variant-numeric:tabular-nums;width:46px;text-align:right;white-space:nowrap}
@media(max-width:400px){.rdate{display:none}}
.empty{padding:96px 20px;text-align:center;color:var(--muted2)}
.empty .big{font-size:15px;color:var(--muted);margin-bottom:6px}
@media(max-width:760px){.deskhint{display:none}}

/* ---- detail ---- */
.detail{display:flex;min-width:0;flex:1;min-height:0}
.dmain{flex:1;padding:26px 32px;min-width:0;max-width:760px;overflow-y:auto}
.dprops{width:264px;flex:none;margin-left:auto;border-left:.5px solid var(--line);padding:22px 20px;
 background:color-mix(in srgb,var(--main) 90%,var(--bg));overflow-y:auto}
@media(max-width:820px){.detail{display:block;overflow-y:auto}.dmain{max-width:none;padding:20px 16px;overflow:visible}
 .dprops{width:auto;margin-left:0;border-left:none;border-top:.5px solid var(--line);overflow:visible;
  padding-bottom:calc(22px + env(safe-area-inset-bottom))}}
.back{color:var(--muted);font-size:12.5px;display:inline-flex;gap:6px;align-items:center;margin-bottom:16px}
.back:hover{color:var(--ink2)}
.dtop{display:flex;align-items:center;gap:9px;margin-bottom:8px}
.dtop .rid{font-family:var(--mono);font-size:12px;color:var(--muted2)}
.dtop .dst{font-size:11.5px;color:var(--muted);border:.5px solid var(--line);border-radius:999px;padding:1px 9px}
.etitle{font-size:22px;color:var(--ink);font-weight:560;letter-spacing:-.022em;line-height:1.28;width:100%;
 border:none;border-bottom:1px solid transparent;background:none;outline:none;font-family:inherit;
 padding:2px 0;margin:0 0 18px;resize:none;overflow:hidden;field-sizing:content}
.etitle:focus{border-bottom-color:var(--line2)}
.field{margin:16px 0}
.field .k{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted2);margin-bottom:6px;font-weight:560}
textarea.f,.field .v{font-size:14px;color:var(--ink2);line-height:1.55}
textarea.f{width:100%;background:var(--bg);border:.5px solid var(--line);border-radius:8px;padding:9px 11px;
 font-family:inherit;outline:none;resize:vertical;min-height:52px;transition:border-color .12s,box-shadow .12s}
@media(max-width:760px){textarea.f{font-size:16px;min-height:76px}}
.conflict{margin:18px 0;border:.5px solid color-mix(in srgb,var(--accent) 40%,var(--line2));
 background:color-mix(in srgb,var(--accent) 8%,transparent);border-radius:8px;padding:11px 13px;font-size:13px;
 color:var(--ink2);display:flex;gap:9px;align-items:flex-start;line-height:1.45}
.conflict b{color:var(--accent);font-weight:560}
.conflict .wico{flex:none;margin-top:1px}
.attempts{display:grid;gap:6px}
.att{display:flex;gap:9px;align-items:baseline;font-size:12.5px;color:var(--ink2)}
.att .an{font-family:var(--mono);font-size:11px;color:var(--muted2);flex:none;width:20px}
.att .ah{flex:1;min-width:0}
.att .asha{font-family:var(--mono);font-size:11px;color:var(--muted2);flex:none}
.att .ares{flex:none;font-size:10px;font-weight:560;letter-spacing:.06em;text-transform:uppercase;
 border-radius:999px;padding:1px 8px;border:.5px solid var(--line2);color:var(--muted)}
.att .ares.pass{color:var(--done);border-color:color-mix(in srgb,var(--done) 40%,var(--line2))}
.att .ares.fail{color:var(--urgent);border-color:color-mix(in srgb,var(--urgent) 40%,var(--line2))}
.tech{margin-top:16px;font-family:var(--mono);font-size:11.5px;color:var(--muted2)}
.proof{margin:18px 0;display:flex;gap:10px;flex-wrap:wrap}
.proof img{max-height:200px;border-radius:8px;border:.5px solid var(--line2)}
.proof .ph{font-size:12px;color:var(--muted2);border:.5px dashed var(--line2);border-radius:8px;padding:22px 26px}
.savebar{display:flex;gap:9px;align-items:center;margin:22px 0 4px}
.prop{margin-bottom:15px}
.prop .k{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted2);margin-bottom:6px;font-weight:560}
.prop .v{font-size:13px;color:var(--ink2);display:flex;align-items:center;gap:7px}
.sel{width:100%;background:var(--bg);border:.5px solid var(--line2);border-radius:7px;padding:7px 26px 7px 9px;color:var(--ink);
 font-family:inherit;font-size:13px;outline:none;appearance:none;-webkit-appearance:none;cursor:pointer;
 background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='9' height='6' viewBox='0 0 9 6'%3E%3Cpath d='M1 1.2 4.5 4.8 8 1.2' fill='none' stroke='%2385858e' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
 background-repeat:no-repeat;background-position:right 9px center;transition:border-color .12s,box-shadow .12s}
.sel:hover{border-color:var(--muted2)}
@media(max-width:760px){.sel{font-size:16px;padding-top:10px;padding-bottom:10px}}
.acts{display:grid;gap:8px;margin-top:6px}

/* buttons + inputs shared */
.btn{font-family:var(--font);font-size:13px;font-weight:520;padding:9px 14px;border-radius:8px;border:.5px solid var(--line2);
 background:var(--elev);color:var(--ink2);cursor:pointer;text-align:center;letter-spacing:-.01em;transition:border-color .12s,background .12s}
.btn:hover{border-color:var(--muted);background:var(--rowhover)}
.btn:active{transform:translateY(.5px)}
.btn.primary{background:var(--accent);color:var(--on-accent);border-color:var(--accent);font-weight:560}
.btn.primary:hover{background:var(--accent2)}
.btn.block{width:100%}
.note-in{width:100%;font-size:12.5px;padding:8px 10px;border-radius:7px;border:.5px solid var(--line2);
 background:var(--bg);color:var(--ink);font-family:inherit;margin-bottom:2px;transition:border-color .12s,box-shadow .12s}
@media(max-width:760px){.note-in{font-size:16px;padding:10px 12px}}

/* ---- modal (New issue) ---- */
.scrim2{position:fixed;inset:0;background:rgba(0,0,0,.55);opacity:0;pointer-events:none;transition:opacity .18s;z-index:60}
body.modal-open .scrim2{opacity:1;pointer-events:auto}
.modal{position:fixed;z-index:61;left:50%;top:64px;transform:translate(-50%,-8px);width:min(560px,94vw);
 background:var(--main);border:.5px solid var(--line2);border-radius:14px;box-shadow:var(--sh);
 opacity:0;pointer-events:none;transition:opacity .18s,transform .18s}
body.modal-open .modal{opacity:1;pointer-events:auto;transform:translate(-50%,0)}
@media(max-width:560px){.modal{left:0;top:0;transform:none;width:100vw;height:100dvh;border-radius:0;border:none}
 body.modal-open .modal{transform:none}}
.modal .mh{display:flex;align-items:center;gap:8px;padding:14px 18px;border-bottom:.5px solid var(--line)}
.modal .mh .t{font-weight:540;color:var(--ink);letter-spacing:-.01em}
.modal .mh .x{margin-left:auto;background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer;line-height:1;
 padding:8px 10px;margin-right:-10px;border-radius:7px}
.modal .mh .x:hover{color:var(--ink2);background:var(--rowhover)}
body.modal-open{overflow:hidden}
.modal .mb{padding:16px 18px;display:grid;gap:12px;max-height:calc(100dvh - 150px);overflow-y:auto}
@media(max-width:560px){.modal .mb{max-height:none;flex:1;align-content:start}
 .modal form{display:flex;flex-direction:column;height:100%}
 .modal .mf{margin-top:auto;padding-bottom:calc(12px + env(safe-area-inset-bottom))}}
.modal .ct-title{font-size:17px;font-weight:520;color:var(--ink);width:100%;border:none;background:none;outline:none;font-family:inherit}
.modal .rowf{display:flex;gap:10px;flex-wrap:wrap}
.modal .rowf .cell{flex:1;min-width:130px}
.modal label{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted2);display:block;margin-bottom:5px;font-weight:560}
.modal .mf{padding:12px 18px;border-top:.5px solid var(--line);display:flex;gap:9px;justify-content:flex-end;align-items:center}
.modal .hintk{margin-right:auto;font-family:var(--mono);font-size:10.5px;color:var(--muted2)}
.kbd{font-family:var(--mono);font-size:10px;color:var(--muted2);border:.5px solid var(--line2);border-radius:4px;padding:1px 5px}
/* development / git */
.dev{display:grid;grid-template-columns:minmax(0,1fr);gap:6px;border:.5px solid var(--line);border-radius:9px;
 padding:10px 12px;background:var(--bg)}
.devrow{min-width:0}
.devrow{display:flex;align-items:center;gap:9px;font-size:12.5px;color:var(--ink2)}
.devrow.commit{padding:5px 6px;margin:0 -6px;border-radius:6px}
.devrow.commit:hover{background:var(--rowhover)}
.gicon{color:var(--muted);width:14px;flex:none;display:grid;place-items:center}
.devrow code{font-family:var(--mono);font-size:11.5px;color:var(--accent);background:none;border:none;padding:0}
.csub{color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
.cstat{font-family:var(--mono);font-size:11px;flex:none}.cstat .add{color:var(--done)}.cstat .del{color:var(--urgent)}
.viewdiff{color:var(--accent);font-size:12px;margin-top:2px}
.viewdiff:hover{color:var(--accent2)}
.bstat{display:flex;gap:8px;align-items:center;font-size:12.5px;margin-top:2px;flex-wrap:wrap}
.pill{font-size:10.5px;padding:2px 8px;border-radius:999px;border:.5px solid var(--line2);color:var(--muted)}
.pill.ship{color:var(--done);border-color:color-mix(in srgb,var(--done) 40%,var(--line2))}
/* diff view */
.diffwrap{padding:20px 24px;max-width:960px}
@media(max-width:760px){.diffwrap{padding:16px 12px}}
.diffwrap h1{font-size:17px;color:var(--ink);font-weight:560;letter-spacing:-.02em;margin:10px 0 4px}
.diffhead{font-family:var(--mono);font-size:12px;color:var(--muted);margin:16px 0 6px}
.diff{font-family:var(--mono);font-size:12px;line-height:1.5;border:.5px solid var(--line);border-radius:8px;
 overflow-x:auto;background:var(--bg)}
.diff .ln{display:block;padding:0 12px;white-space:pre;width:max-content;min-width:100%}
.diff .h{background:color-mix(in srgb,var(--accent) 12%,var(--bg));color:var(--muted)}
.diff .a{background:color-mix(in srgb,var(--done) 13%,transparent);color:var(--ink2)}
.diff .d{background:color-mix(in srgb,var(--urgent) 13%,transparent);color:var(--ink2)}
.diff .c{color:var(--muted2)}

/* ---- attach / drop zone ---- */
.attach{border:1.5px dashed var(--line2);border-radius:10px;padding:14px 12px;text-align:center;color:var(--muted);
 cursor:pointer;transition:border-color .12s,background .12s;font-size:12.5px;user-select:none;-webkit-user-select:none}
.attach:hover,.attach.drag{border-color:color-mix(in srgb,var(--accent) 65%,var(--line2));
 background:color-mix(in srgb,var(--accent) 6%,transparent);color:var(--ink2)}
.attach input{display:none}
.attach .aico{display:block;margin:0 auto 6px;color:var(--muted2)}
.filelist{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;justify-content:center}
.filelist:empty{margin-top:0}
.fitem{display:inline-flex;align-items:center;gap:7px;font-size:11.5px;color:var(--ink2);border:.5px solid var(--line2);
 border-radius:7px;padding:4px 9px;background:var(--elev);max-width:100%}
.fitem img{width:26px;height:26px;object-fit:cover;border-radius:5px;flex:none}
.fitem span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ---- toast ---- */
.toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,10px);background:var(--elev);color:var(--ink);
 border:.5px solid var(--line2);border-radius:9px;padding:9px 16px;font-size:12.5px;box-shadow:var(--sh);
 opacity:0;pointer-events:none;transition:opacity .18s,transform .18s;z-index:99;white-space:nowrap}
.toast.on{opacity:1;transform:translate(-50%,0)}

/* ---- command palette ---- */
.ck-scrim{position:fixed;inset:0;background:rgba(0,0,0,.45);opacity:0;pointer-events:none;transition:opacity .15s;z-index:70}
body.ck-open .ck-scrim{opacity:1;pointer-events:auto}
.cmdk{position:fixed;z-index:71;left:50%;top:13vh;transform:translateX(-50%) scale(.98);width:min(560px,94vw);
 background:var(--main);border:.5px solid var(--line2);border-radius:12px;box-shadow:var(--sh);overflow:hidden;
 opacity:0;pointer-events:none;transition:opacity .14s,transform .14s}
body.ck-open .cmdk{opacity:1;pointer-events:auto;transform:translateX(-50%) scale(1)}
.cmdk input{width:100%;border:none;background:none;outline:none;padding:14px 16px;font-family:inherit;font-size:15px;
 color:var(--ink);border-bottom:.5px solid var(--line)}
.ck-list{max-height:min(380px,52vh);overflow-y:auto;padding:6px}
.ck-it{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:7px;cursor:pointer;color:var(--ink2);font-size:13px}
.ck-it .cid{font-family:var(--mono);font-size:10.5px;color:var(--muted2);width:64px;flex:none;text-transform:uppercase;letter-spacing:.03em}
.ck-it .ckt{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.ck-it.sel,.ck-it:hover{background:var(--rowhover);color:var(--ink)}
.ck-empty{padding:22px;text-align:center;color:var(--muted2);font-size:12.5px}

/* ---- filter bar ---- */
.fbar{display:flex;gap:8px;align-items:center;padding:8px 20px;border-bottom:.5px solid var(--line);
 overflow-x:auto;flex:none;background:var(--main);scrollbar-width:none}
.fbar::-webkit-scrollbar{display:none}
@media(max-width:760px){.fbar{padding:8px 12px}}
.fsel{width:auto;min-width:0;flex:none;font-size:12px;padding:5px 24px 5px 10px;border-radius:999px;color:var(--muted);background-color:var(--main)}
.fsel.on{color:var(--ink);border-color:color-mix(in srgb,var(--accent) 55%,var(--line2))}
@media(max-width:760px){.fsel{font-size:13px}}
.fct{font-size:11.5px;color:var(--muted2);font-variant-numeric:tabular-nums;white-space:nowrap;margin-left:2px}

/* ---- issue detail extras ---- */
.linkbtn{margin-left:auto;display:inline-flex;gap:6px;align-items:center;background:none;border:.5px solid var(--line2);
 border-radius:7px;padding:4px 10px;color:var(--muted);font-size:11.5px;cursor:pointer;font-family:inherit;white-space:nowrap}
.linkbtn:hover{color:var(--ink2);border-color:var(--muted)}
.proof video{max-height:220px;max-width:100%;border-radius:8px;border:.5px solid var(--line2);background:#000}

/* ---- brain dump ---- */
.bd-wrap{max-width:640px;margin:0 auto;padding:26px 20px calc(60px + env(safe-area-inset-bottom))}
@media(max-width:760px){.bd-wrap{padding:18px 14px calc(60px + env(safe-area-inset-bottom))}}
.bd-wrap h1{font-size:20px;color:var(--ink);font-weight:560;letter-spacing:-.02em;margin:0 0 6px}
.bd-sub{color:var(--muted);font-size:13.5px;margin-bottom:18px;line-height:1.5}
textarea.bd{min-height:200px}
@media(max-width:760px){textarea.bd{min-height:240px}}
.bd-ok{display:flex;gap:9px;align-items:flex-start;border:.5px solid color-mix(in srgb,var(--done) 40%,var(--line2));
 background:color-mix(in srgb,var(--done) 9%,transparent);color:var(--ink2);border-radius:9px;padding:11px 14px;
 font-size:13px;margin-bottom:16px;line-height:1.45}
.bd-ok b{color:var(--done);font-weight:560}
.bd-q{margin-top:30px}
.bd-qh{display:flex;align-items:baseline;gap:8px;font-size:12.5px;color:var(--ink2);font-weight:540;margin-bottom:8px}
.bd-qh .n{color:var(--accent);font-variant-numeric:tabular-nums}
.bd-item{display:flex;gap:12px;align-items:baseline;padding:9px 2px;border-bottom:.5px solid var(--line);font-size:12.5px;min-width:0}
.bd-item .ts{font-family:var(--mono);font-size:11px;color:var(--muted2);flex:none;white-space:nowrap}
.bd-item .pv{color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.pend-badge{color:var(--accent)!important;font-weight:600}
.bd-link{margin-left:auto;color:var(--muted);font-size:12px;padding:4px 8px;border-radius:6px;white-space:nowrap}
.bd-link:hover{color:var(--accent);background:var(--rowhover)}
.mh .bd-link~.x{margin-left:0}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""

PWA_HEAD = (
    '<link rel="manifest" href="/manifest.json">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    '<meta name="apple-mobile-web-app-title" content="Dev">'
    '<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0d0d0f">'
    '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#f6f6f7">'
    '<link rel="apple-touch-icon" href="/icon-180.png">'
)

JS = r"""
(function(){
 var root=document.documentElement;
 try{var s=localStorage.getItem('islah-theme');if(s)root.setAttribute('data-theme',s);}catch(e){}
 var MOON='<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M11.6 8.6A4.9 4.9 0 1 1 5.4 2.4a3.9 3.9 0 1 0 6.2 6.2Z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';
 var SUN='<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="7" cy="7" r="2.5" stroke="currentColor" stroke-width="1.2"/><path d="M7 1v1.7M7 11.3V13M13 7h-1.7M2.7 7H1M11.2 2.8 10 4M4 10l-1.2 1.2M11.2 11.2 10 10M4 4 2.8 2.8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>';
 function curTheme(){return root.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');}
 function paintTheme(){var n=curTheme();
  Array.prototype.forEach.call(document.querySelectorAll('.theme-ico'),function(el){el.innerHTML=n==='dark'?MOON:SUN;});}
 window.toggleTheme=function(){var n=curTheme()==='dark'?'light':'dark';root.setAttribute('data-theme',n);
  try{localStorage.setItem('islah-theme',n);}catch(e){}
  paintTheme();};
 window.toggleNav=function(){document.body.classList.toggle('nav-open');};
 window.openNew=function(){document.body.classList.add('modal-open');var t=document.getElementById('nf-title');if(t){setTimeout(function(){t.focus();},60);}};
 window.closeNew=function(){document.body.classList.remove('modal-open');};
 window.toast=function(m){var t=document.getElementById('toast');if(!t)return;t.textContent=m;
  t.classList.add('on');clearTimeout(t._h);t._h=setTimeout(function(){t.classList.remove('on');},1900);};
 window.copyLink=function(u){u=u||location.href;
  function fb(){var ta=document.createElement('textarea');ta.value=u;ta.style.position='fixed';ta.style.opacity='0';
   document.body.appendChild(ta);ta.select();try{document.execCommand('copy');toast('Link copied');}catch(e){}
   document.body.removeChild(ta);}
  if(navigator.clipboard&&navigator.clipboard.writeText){
   navigator.clipboard.writeText(u).then(function(){toast('Link copied');},fb);}else fb();};
 /* ---- command palette ---- */
 var ckItems=[],ckSel=0;
 function escH(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
 function ckScore(q,s){s=s.toLowerCase();var ix=s.indexOf(q);if(ix>=0)return 200-Math.min(ix,90);
  var i=0;for(var j=0;j<s.length&&i<q.length;j++){if(s[j]===q[i])i++;}
  return i===q.length&&q.length>1?20:-1;}
 window.ckClose=function(){document.body.classList.remove('ck-open');};
 window.ckOpen=function(){var i=document.getElementById('ck-in');if(!i)return;
  document.body.classList.add('ck-open');i.value='';ckRender('');setTimeout(function(){i.focus();},40);};
 function ckRender(qv){
  var list=document.getElementById('ck-list'),CK=window.__CK__;if(!list||!CK)return;
  qv=(qv||'').toLowerCase().trim();
  var items=[];
  CK.nav.forEach(function(n,i){items.push({t:n.t,d:'Go to',h:n.h,s:qv?ckScore(qv,n.t):60-i});});
  items.push({t:'New issue',d:'Action',fn:'new',s:qv?ckScore(qv,'new issue create'):61});
  items.push({t:'Toggle theme',d:'Action',fn:'theme',s:qv?ckScore(qv,'toggle theme dark light'):40});
  CK.issues.forEach(function(x,i){
   items.push({t:x.t,d:x.id,h:'/issue/'+encodeURIComponent(x.raw),
    s:qv?ckScore(qv,x.id+' '+x.t+' '+(x.a||'')+' '+x.s):(30-Math.min(i,25))});});
  items=items.filter(function(x){return x.s>0;}).sort(function(a,b){return b.s-a.s;}).slice(0,12);
  ckItems=items;ckSel=0;
  list.innerHTML=items.length?items.map(function(x,i){
   return '<div class="ck-it'+(i===0?' sel':'')+'" data-i="'+i+'"><span class="cid">'+escH(x.d)+
    '</span><span class="ckt">'+escH(x.t)+'</span></div>';}).join(''):
   '<div class="ck-empty">Nothing matches</div>';}
 function ckGo(i){var it=ckItems[i];if(!it)return;ckClose();
  if(it.h)location.href=it.h;
  else if(it.fn==='new')openNew();
  else if(it.fn==='theme')toggleTheme();}
 function ckMove(d){if(!ckItems.length)return;ckSel=(ckSel+d+ckItems.length)%ckItems.length;
  var list=document.getElementById('ck-list');
  Array.prototype.forEach.call(list.querySelectorAll('.ck-it'),function(el,i){
   el.classList.toggle('sel',i===ckSel);if(i===ckSel&&el.scrollIntoView)el.scrollIntoView({block:'nearest'});});}
 function autoGrow(el){el.style.height='auto';el.style.height=el.scrollHeight+'px';}
 function wire(){
  paintTheme();
  var et=document.querySelector('.etitle');
  if(et){autoGrow(et);et.addEventListener('input',function(){autoGrow(et);});}
  var q=document.getElementById('search');
  var fS=document.getElementById('f-status'),fP=document.getElementById('f-sev'),fA=document.getElementById('f-app');
  function applyFilters(){
   var v=q?q.value.toLowerCase():'',hit=0,total=0;
   Array.prototype.forEach.call(document.querySelectorAll('.row'),function(r){
     total++;
     var on=!v||(r.dataset.t||'').indexOf(v)>=0;
     if(on&&fS&&fS.value&&r.dataset.status!==fS.value)on=false;
     if(on&&fP&&fP.value&&r.dataset.sev!==fP.value)on=false;
     if(on&&fA&&fA.value&&r.dataset.app!==fA.value)on=false;
     r.style.display=on?'':'none';if(on)hit++;});
   Array.prototype.forEach.call(document.querySelectorAll('.group'),function(g){
     var any=Array.prototype.some.call(g.querySelectorAll('.row'),function(r){return r.style.display!=='none';});
     g.style.display=any?'':'none';});
   [fS,fP,fA].forEach(function(el){if(el)el.classList.toggle('on',!!el.value);});
   var active=!!(v||(fS&&fS.value)||(fP&&fP.value)||(fA&&fA.value));
   var ne=document.getElementById('noresults');
   if(ne)ne.style.display=(hit||!active)?'none':'';
   var fc=document.getElementById('fcount');
   if(fc)fc.textContent=active?hit+' of '+total:'';}
  window.applyFilters=applyFilters;
  if(q)q.addEventListener('input',applyFilters);
  /* attach zones */
  Array.prototype.forEach.call(document.querySelectorAll('.attach'),function(z){
   var inp=z.querySelector('input[type=file]'),list=z.querySelector('.filelist'),hint=z.querySelector('.ahint');
   if(!inp)return;
   function render(){if(!list)return;list.innerHTML='';
    Array.prototype.forEach.call(inp.files,function(f){
     var it=document.createElement('span');it.className='fitem';
     if(f.type&&f.type.indexOf('image')===0){var im=document.createElement('img');
      im.src=URL.createObjectURL(f);it.appendChild(im);}
     var nm=document.createElement('span');
     nm.textContent=(f.name.length>26?f.name.slice(0,23)+'…':f.name);it.appendChild(nm);
     list.appendChild(it);});
    if(hint)hint.textContent=inp.files.length?
     (inp.files.length+' file'+(inp.files.length>1?'s':'')+' attached — tap to change'):z.dataset.hint;}
   z.addEventListener('click',function(e){if(e.target!==inp)inp.click();});
   inp.addEventListener('change',render);
   ['dragover','dragenter'].forEach(function(ev){z.addEventListener(ev,function(e){
     e.preventDefault();z.classList.add('drag');});});
   ['dragleave','dragend'].forEach(function(ev){z.addEventListener(ev,function(){z.classList.remove('drag');});});
   z.addEventListener('drop',function(e){e.preventDefault();z.classList.remove('drag');
    if(e.dataTransfer&&e.dataTransfer.files.length){try{inp.files=e.dataTransfer.files;}catch(err){}render();}});});
  /* palette list interaction */
  var ckl=document.getElementById('ck-list'),cki=document.getElementById('ck-in');
  if(ckl)ckl.addEventListener('click',function(e){var it=e.target.closest('.ck-it');if(it)ckGo(+it.dataset.i);});
  if(cki){cki.addEventListener('input',function(){ckRender(this.value);});
   cki.addEventListener('keydown',function(e){
    if(e.key==='ArrowDown'){e.preventDefault();ckMove(1);}
    else if(e.key==='ArrowUp'){e.preventDefault();ckMove(-1);}
    else if(e.key==='Enter'){e.preventDefault();ckGo(ckSel);}
    else if(e.key==='Escape'){ckClose();}});}
  document.addEventListener('keydown',function(e){
    if((e.metaKey||e.ctrlKey)&&(e.key==='k'||e.key==='K')){e.preventDefault();
      if(document.body.classList.contains('ck-open'))ckClose();else ckOpen();return;}
    if((e.metaKey||e.ctrlKey)&&e.key==='Enter'&&document.body.classList.contains('modal-open')){
      var f=document.querySelector('.modal form');
      if(f){e.preventDefault();f.requestSubmit?f.requestSubmit():f.submit();}return;}
    var tag=(e.target.tagName||'');
    if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT'||e.metaKey||e.ctrlKey){if(e.key==='Escape')e.target.blur&&e.target.blur();return;}
    if(e.key==='c'){e.preventDefault();openNew();}
    else if(e.key==='/'){e.preventDefault();if(q)q.focus();}
    else if(e.key==='t'){toggleTheme();}
    else if(e.key==='Escape'){closeNew();ckClose();document.body.classList.remove('nav-open');}});
 }
 if(document.readyState!=='loading')wire();else document.addEventListener('DOMContentLoaded',wire);
})();
"""

ICONS = {
    "inbox": '<svg class="ic" viewBox="0 0 16 16" fill="none"><path d="M2 9h3l1 2h4l1-2h3M2 9l2-5h8l2 5M2 9v3a1 1 0 001 1h10a1 1 0 001-1V9" stroke="currentColor" stroke-width="1.2"/></svg>',
    "issues": '<svg class="ic" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5.2" stroke="currentColor" stroke-width="1.2"/><circle cx="8" cy="8" r="2" fill="currentColor"/></svg>',
    "app": '<svg class="ic" viewBox="0 0 16 16" fill="none"><rect x="2.5" y="2.5" width="11" height="11" rx="2.6" stroke="currentColor" stroke-width="1.2"/></svg>',
    "search": '<svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="6" cy="6" r="4.1" stroke="currentColor" stroke-width="1.3"/><path d="m9.2 9.2 3 3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    "menu": '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    "plus": '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M7 2.2v9.6M2.2 7h9.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
    "branch": '<svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="4" cy="3.4" r="1.6" stroke="currentColor" stroke-width="1.2"/><circle cx="4" cy="10.6" r="1.6" stroke="currentColor" stroke-width="1.2"/><circle cx="10.2" cy="4.8" r="1.6" stroke="currentColor" stroke-width="1.2"/><path d="M4 5v4M10.2 6.4c0 2.4-3 2.7-4.6 3.4" stroke="currentColor" stroke-width="1.2"/></svg>',
    "commit": '<svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="7" cy="7" r="2.5" stroke="currentColor" stroke-width="1.3"/><path d="M7 .8v3.7M7 9.5v3.7" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    "warn": '<svg class="wico" width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M7 1.6 13 12H1Z" stroke="var(--accent)" stroke-width="1.2" stroke-linejoin="round"/><path d="M7 5.4v3" stroke="var(--accent)" stroke-width="1.3" stroke-linecap="round"/><circle cx="7" cy="10.2" r=".8" fill="var(--accent)"/></svg>',
    "pulse": '<svg class="ic" viewBox="0 0 16 16" fill="none"><path d="M1.5 8.2h3l1.8-4.6 3.4 8.8 1.8-4.2h3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "bulb": '<svg class="ic" viewBox="0 0 16 16" fill="none"><path d="M8 1.8a4.5 4.5 0 0 0-2.6 8.2c.5.4.8.9.8 1.5v.3h3.6v-.3c0-.6.3-1.1.8-1.5A4.5 4.5 0 0 0 8 1.8Z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M6.6 14.2h2.8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
    "link": '<svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M5.8 8.2a2.8 2.8 0 0 0 4 .2l1.6-1.6a2.8 2.8 0 1 0-4-4l-.9.9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M8.2 5.8a2.8 2.8 0 0 0-4-.2L2.6 7.2a2.8 2.8 0 1 0 4 4l.9-.9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
    "clip": '<svg class="aico" width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><path d="M12.8 8 8.6 12.2a3 3 0 0 1-4.3-4.3l5-5a2 2 0 0 1 2.9 2.9l-5 5a1 1 0 0 1-1.5-1.4l4.3-4.3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
}


def attach_zone(hint="Attach screenshots or a video — tap or drop files"):
    return (f'<div class="attach" data-hint="{esc(hint)}" role="button" tabindex="0" aria-label="{esc(hint)}">'
            f'<input type="file" name="files" accept="image/*,video/*" multiple>'
            f'{ICONS["clip"]}<div class="ahint">{esc(hint)}</div><div class="filelist"></div></div>')


def sel_options(options, current):
    return "".join(f'<option value="{esc(v)}"{" selected" if v == current else ""}>{esc(lbl)}</option>'
                   for v, lbl in options)


def composer_modal():
    app_opts = sel_options([("", "Select app…")] + APPS, "")
    kind_opts = sel_options([("bug", "Bug"), ("feature", "Feature / idea")], "bug")
    sev_opts = sel_options([(str(k), v) for k, v in [(0, "No priority"), (1, "Urgent"), (2, "High"), (3, "Medium"), (4, "Low")]], "0")
    return f"""
<div class="scrim2" onclick="closeNew()"></div>
<div class="modal" role="dialog" aria-modal="true" aria-label="New issue">
 <form method="post" action="/create" enctype="multipart/form-data">
  <div class="mh"><span class="t">New issue</span><a class="bd-link" href="/braindump" title="Paste free-form thoughts for AI triage">Brain dump →</a><button type="button" class="x" onclick="closeNew()" aria-label="Close">×</button></div>
  <div class="mb">
   <input class="ct-title" id="nf-title" name="title" placeholder="Issue title" required autocomplete="off">
   <textarea class="f" name="symptom" placeholder="What's happening? Plain language is fine."></textarea>
   <div class="rowf">
    <div class="cell"><label for="nf-app">App</label><select class="sel" id="nf-app" name="app">{app_opts}</select></div>
    <div class="cell"><label for="nf-kind">Type</label><select class="sel" id="nf-kind" name="kind">{kind_opts}</select></div>
    <div class="cell"><label for="nf-sev">Priority</label><select class="sel" id="nf-sev" name="severity">{sev_opts}</select></div>
   </div>
   <div class="rowf">
    <div class="cell"><label for="nf-screen">Screen (optional)</label><input class="note-in" id="nf-screen" name="screen" placeholder="e.g. Study mode"></div>
   </div>
   {attach_zone()}
  </div>
  <div class="mf"><span class="hintk deskhint"><span class="kbd">esc</span> close · <span class="kbd">⌘⏎</span> create</span>
   <button type="button" class="btn" onclick="closeNew()">Cancel</button>
   <button type="submit" class="btn primary">Create issue</button></div>
 </form>
</div>"""


def sidebar(active=""):
    lg = Ledger()
    n_inbox = sum(len(lg.list(status=s)) for s in NEEDS_YOU)
    n_all = len([b for b in lg.all() if b.is_open()])
    n_dumps = len(pending_dumps())
    pend = (f'<span class="ct pend-badge" title="Pending triage · {n_dumps}">{n_dumps}</span>'
            if n_dumps else '')
    s = ['<div class="side">',
         '<div class="ws"><span class="mark">D</span><span class="name">Dev</span>'
         '<button class="tg" onclick="toggleTheme()" title="Toggle theme (t)"><span class="theme-ico">☾</span></button></div>',
         '<button class="newbtn" onclick="openNew()">+ New issue</button>',
         f'<a href="/" class="nav{" active" if active=="inbox" else ""}">{ICONS["inbox"]}Inbox<span class="ct">{n_inbox or ""}</span></a>',
         f'<a href="/review" class="nav{" active" if active=="review" else ""}">'
         f'<svg class="ic" viewBox="0 0 16 16" fill="none"><rect x="2.5" y="4.2" width="11" height="8.3" rx="1.6" '
         f'stroke="currentColor" stroke-width="1.2"/><path d="M4.8 2.5h6.4" stroke="currentColor" stroke-width="1.2" '
         f'stroke-linecap="round"/></svg>Review<span class="ct">{len(review_queue(Ledger())) or ""}</span></a>',
         f'<a href="/?view=all" class="nav{" active" if active=="all" else ""}">{ICONS["issues"]}All issues<span class="ct">{n_all}</span></a>',
         f'<a href="/activity" class="nav{" active" if active=="activity" else ""}">{ICONS["pulse"]}Activity</a>',
         f'<a href="/braindump" class="nav{" active" if active=="braindump" else ""}">{ICONS["bulb"]}Brain dump{pend}</a>',
         '<div class="navlabel">Apps</div>']
    for slug, name in APPS:
        n = len([b for b in lg.list(app=slug) if b.is_open()])
        s.append(f'<a href="/?app={slug}" class="nav{" active" if active==slug else ""}">{ICONS["app"]}{esc(name)}<span class="ct">{n or ""}</span></a>')
    s.append('<div class="spacer"></div><div class="foot">live ledger · local only</div></div>')
    return "".join(s)


def topbar():
    return ('<div class="topbar">'
            f'<button class="iconbtn" onclick="toggleNav()" aria-label="Menu">{ICONS["menu"]}</button>'
            '<span class="brand"><span class="mark">D</span>Dev</span><span class="grow"></span>'
            '<button class="iconbtn" onclick="toggleTheme()" aria-label="Toggle theme"><span class="theme-ico">☾</span></button>'
            f'<button class="iconbtn" onclick="openNew()" aria-label="New issue">{ICONS["plus"]}</button></div>')


def _ck_data():
    """Navigation + issue index for the ⌘K palette, embedded per page."""
    lg = Ledger()
    nav = ([{"t": "Inbox", "h": "/"}, {"t": "All issues", "h": "/?view=all"},
            {"t": "Activity", "h": "/activity"}, {"t": "Brain dump", "h": "/braindump"}]
           + [{"t": name, "h": f"/?app={slug}"} for slug, name in APPS])
    issues = [{"raw": b.id, "id": disp_id(b.id), "t": (b.title or "")[:90], "a": b.app or "", "s": b.status}
              for b in sorted(lg.all(), key=lambda x: x.updated or x.created or "", reverse=True)]
    return json.dumps({"nav": nav, "issues": issues}, ensure_ascii=False).replace("</", "<\\/")


def palette_markup():
    return ('<div class="ck-scrim" onclick="ckClose()"></div>'
            '<div class="cmdk" role="dialog" aria-modal="true" aria-label="Command palette">'
            '<input id="ck-in" placeholder="Type a command or search issues…" autocomplete="off" spellcheck="false">'
            '<div class="ck-list" id="ck-list"></div></div>'
            '<div class="toast" id="toast" role="status" aria-live="polite"></div>')


def page(title, body, active=""):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<title>{esc(title)} · Dev</title>{PWA_HEAD}<style>{CSS}</style></head><body>'
            f'<div class="scrim" onclick="toggleNav()"></div>'
            f'<div class="app">{sidebar(active)}<div class="main">{topbar()}{body}</div></div>'
            f'{composer_modal()}{palette_markup()}'
            f'<script>window.__CK__={_ck_data()}</script><script>{JS}</script>'
            f'<script>{SWIPE_JS}</script><script>{REVIEW_JS}</script></body></html>')


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_date(iso):
    if not iso or len(iso) < 10:
        return ""
    try:
        return f"{_MONTHS[int(iso[5:7]) - 1]} {int(iso[8:10])}"
    except (ValueError, IndexError):
        return esc(iso[5:10])


def render_row(b):
    href = f"/issue/{quote(b.id)}"
    cf = '<span class="cf">CONFLICT</span>' if b.conflict else ''
    search_t = esc(f"{disp_id(b.id)} {b.title} {b.app or ''} {b.kind or ''} {b.status}".lower())
    applbl = {"quran-garden": "Quran Garden", "deenoverdunya": "Deen Over Dunya",
              "qg-web": "QG Web"}.get(b.app, b.app or "")
    urgent = '<span class="utag">Urgent</span>' if (b.severity or 0) == 1 else ''
    sub = " · ".join(x for x in [esc(disp_id(b.id)), esc(applbl), fmt_date(b.reported)] if x)
    sw = SWIPE.get(b.status)
    bg, data_sw = "", ""
    if sw:
        pa, pl, na, nl = sw
        data_sw = f' data-id="{esc(b.id)}"'
        if pa:
            data_sw += f' data-pos="{esc(pa)}"'
            bg += f'<div class="swipebg pos">✓ {esc(pl)}</div>'
        if na:
            data_sw += f' data-neg="{esc(na)}"'
            bg += f'<div class="swipebg neg">{esc(nl)} →</div>'
    return (f'<div class="row" data-t="{search_t}" data-status="{esc(b.status)}" '
            f'data-sev="{b.severity or 0}" data-app="{esc(b.app or "")}"{data_sw}>{bg}'
            f'<a class="rowinner" href="{href}">'
            f'<span class="rmain"><span class="rtitle">{esc(b.title)}{cf}</span>'
            f'<span class="rsub">{sub}</span></span>{urgent}</a></div>')


NEEDS_YOU = ["needs-decision", "reopened", "awaiting-approval", "confirmed"]

STATUS_COLOR = {
    "needs-decision": "var(--accent)", "reopened": "var(--urgent)",
    "awaiting-approval": "var(--done)", "confirmed": "#5a8fc7",
    "fixing": "var(--accent)", "verifying": "var(--accent)",
    "approved": "var(--done)", "shipped": "var(--done)",
}

# Swipe gestures per status: (right_action, right_label, left_action, left_label)
SWIPE = {
    "needs-decision":    ("greenlight", "Yes, do it", "dismiss", "Set aside"),
    "confirmed":         ("greenlight", "Fix it", "dismiss", "Not now"),
    "awaiting-approval": ("approve", "Approve", "sendback", "Send back"),
    "reopened":          ("greenlight", "Fix again", None, None),
}

REVIEW_JS = r"""
function lightbox(src){var l=document.getElementById('lb');if(!l)return;
 document.getElementById('lbimg').src=src;l.classList.add('on');}
(function(){
 var rv=document.querySelector('.rv'); if(!rv||!rv.dataset.id) return;
 var card=document.getElementById('rvcard'), form=document.getElementById('rvform');
 if(!card||!form) return;
 var pos=rv.dataset.pos, neg=rv.dataset.neg, COMMIT=110;
 var sx=0,sy=0,dx=0,mode=0;
 function bg(d){var p=rv.querySelector('.rvsw.pos'),n=rv.querySelector('.rvsw.neg');
  if(p)p.style.opacity=d>6?Math.min(1,d/COMMIT):0; if(n)n.style.opacity=d<-6?Math.min(1,-d/COMMIT):0;}
 function fire(action){
  var inp=document.createElement('input');
  inp.type='hidden'; inp.name='action'; inp.value=action; form.appendChild(inp);
  card.style.transition='transform .2s'; card.style.transform='translateX('+(action===pos?'110%':'-110%')+')';
  setTimeout(function(){form.submit();},170);
 }
 card.addEventListener('touchstart',function(e){var t=e.touches[0];sx=t.clientX;sy=t.clientY;dx=0;mode=0;},{passive:true});
 card.addEventListener('touchmove',function(e){
  var t=e.touches[0]; dx=t.clientX-sx; var dy=t.clientY-sy;
  if(mode===0){ if(Math.abs(dx)>12&&Math.abs(dx)>Math.abs(dy)+4){mode=1;rv.classList.add('swiping');}
                else if(Math.abs(dy)>12){mode=2;} }
  if(mode===1){ e.preventDefault();
   if(dx>0&&!pos)dx*=0.15; if(dx<0&&!neg)dx*=0.15;
   card.style.transform='translateX('+dx+'px)'; bg(dx); }
 },{passive:false});
 card.addEventListener('touchend',function(){
  var act = dx>0?pos:neg;
  if(mode===1&&Math.abs(dx)>=COMMIT&&act){ fire(act); }
  else { card.style.transform=''; rv.classList.remove('swiping'); bg(0); }
  mode=0; dx=0;
 });
 document.addEventListener('keydown',function(e){
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
  if((e.key==='y'||e.key==='Y')&&pos) fire(pos);
  if((e.key==='n'||e.key==='N')&&neg) fire(neg);
 });
})();
"""

SWIPE_JS = r"""
(function(){
 var COMMIT=104, cur=null, sx=0, sy=0, dx=0, mode=0; // mode 0 undecided,1 horiz,2 vert
 function inner(r){return r.querySelector('.rowinner');}
 function bgs(r,d){var p=r.querySelector('.swipebg.pos'),n=r.querySelector('.swipebg.neg');
   if(p)p.style.opacity=d>4?1:0; if(n)n.style.opacity=d<-4?1:0;}
 document.addEventListener('touchstart',function(e){
   var r=e.target.closest?e.target.closest('.row'):null;
   if(!r||!r.dataset.id){cur=null;return;}
   cur=r; var t=e.touches[0]; sx=t.clientX; sy=t.clientY; dx=0; mode=0;
 },{passive:true});
 document.addEventListener('touchmove',function(e){
   if(!cur)return; var t=e.touches[0]; dx=t.clientX-sx; var dy=t.clientY-sy;
   if(mode===0){ if(Math.abs(dx)>12&&Math.abs(dx)>Math.abs(dy)+4){mode=1;cur.classList.add('swiping');}
                 else if(Math.abs(dy)>12){mode=2;} }
   if(mode===1){
     e.preventDefault();
     if(dx>0&&!cur.dataset.pos)dx=Math.max(dx*0.15,0);
     if(dx<0&&!cur.dataset.neg)dx=Math.min(dx*0.15,0);
     inner(cur).style.transform='translateX('+dx+'px)'; bgs(cur,dx);
   }
 },{passive:false});
 document.addEventListener('touchend',function(){
   if(!cur){return;} var r=cur, i=inner(r);
   var act = dx>0?r.dataset.pos:r.dataset.neg;
   if(mode===1&&Math.abs(dx)>=COMMIT&&act){
     i.style.transition='transform .2s'; i.style.transform='translateX('+(dx>0?'110%':'-110%')+')';
     var b=new URLSearchParams(); b.set('id',r.dataset.id); b.set('action',act);
     b.set('ret',location.pathname+location.search);
     fetch('/act',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:b.toString()})
       .then(function(){setTimeout(function(){location.reload();},200);})
       .catch(function(){location.reload();});
   } else { i.style.transform=''; r.classList.remove('swiping'); bgs(r,0); }
   cur=null; mode=0; dx=0;
 });
})();
"""


def render_list(app_filter=None, status_filter=None, inbox=False):
    lg = Ledger()
    bugs = lg.list(app=app_filter, status=status_filter)
    by = {}
    for b in bugs:
        by.setdefault(b.status, []).append(b)
    group_order = [g for g in GROUPS if g[0] in NEEDS_YOU] if inbox else GROUPS
    n_open = (sum(len(by.get(s, [])) for s in NEEDS_YOU) if inbox
              else len([b for b in bugs if b.is_open()]))
    title = "Inbox" if inbox else dict(APPS).get(app_filter, "All issues")
    count_lbl = "need you" if inbox else "open"
    hdr = (f'<div class="hdr"><span class="title">{esc(title)}</span><span class="ct">{n_open} {count_lbl}</span>'
           f'<span class="grow"></span>'
           f'<label class="search"><span class="k">{ICONS["search"]}</span>'
           f'<input id="search" placeholder="Search issues" autocomplete="off" aria-label="Search issues"></label>'
           f'<button class="newtop" onclick="openNew()">+ New</button>'
           f'<button class="tg" onclick="toggleTheme()" title="Toggle theme (t)" aria-label="Toggle theme">'
           f'<span class="theme-ico">☾</span></button></div>')
    fbar = ['<div class="fbar">',
            f'<select class="sel fsel" id="f-status" aria-label="Filter by status" onchange="applyFilters()">'
            f'{sel_options([("", "All statuses")] + [(s, l) for s, l in GROUPS], "")}</select>',
            f'<select class="sel fsel" id="f-sev" aria-label="Filter by priority" onchange="applyFilters()">'
            f'{sel_options([("", "Any priority")] + [(str(k), v) for k, v in SEV.items()], "")}</select>']
    if not app_filter:
        fbar.append(f'<select class="sel fsel" id="f-app" aria-label="Filter by app" onchange="applyFilters()">'
                    f'{sel_options([("", "All apps")] + APPS, "")}</select>')
    fbar.append('<span class="fct" id="fcount"></span></div>')
    if not inbox:
        hdr += "".join(fbar)
    rows = ['<div class="scroll">']
    shown = False
    for status, label in group_order:
        items = by.get(status, [])
        if not items:
            continue
        shown = True
        rows.append(f'<div class="group"><div class="group-h"><span class="lbl" style="color:{STATUS_COLOR.get(status, "var(--muted)")}">{esc(label)}</span>'
                    f'<span class="ct">{len(items)}</span></div>')
        for b in items:
            rows.append(render_row(b))
        rows.append('</div>')
    if not shown:
        if inbox:
            rows.append('<div class="empty"><div class="big">Nothing needs you right now</div>'
                        'Items appear here when they need a decision or your approval.</div>')
        else:
            rows.append('<div class="empty"><div class="big">No issues yet</div>'
                        'New reports land here automatically.'
                        '<span class="deskhint"> Press <span class="kbd">c</span> to add one yourself.</span></div>')
    if inbox:
        triage = len(by.get("new", [])) + len(by.get("triaging", []))
        prog = len(by.get("fixing", [])) + len(by.get("verifying", []))
        info = len(by.get("needs-info", []))
        parts = []
        if triage:
            parts.append(f"{triage} waiting to be triaged")
        if prog:
            parts.append(f"{prog} in progress")
        if info:
            parts.append(f"{info} waiting on info")
        summ = " · ".join(parts) if parts else "nothing else in the pipeline"
        rows.append('<div style="padding:14px 20px;color:var(--muted);font-size:12.5px;'
                    'border-top:.5px solid var(--line)">Behind the scenes: '
                    f'{summ}. <a style="color:var(--accent)" href="/?view=all">View all issues →</a></div>')
    rows.append('<div class="empty" id="noresults" style="display:none"><div class="big">No matching issues</div>'
                'Try a shorter search — titles, IDs, and apps are searchable.</div>')
    rows.append('</div>')
    active = "inbox" if inbox else (app_filter if app_filter else "all")
    return page(title, hdr + "".join(rows), active=active)



REVIEW_STATUSES = NEEDS_YOU + ["new", "triaging", "needs-info"]


def review_queue(lg):
    q = [b for b in lg.all() if b.is_open() and b.status in REVIEW_STATUSES]
    q.sort(key=lambda b: (0 if b.status in NEEDS_YOU else 1, int(b.id.split("#")[1]) if "#" in b.id else 0))
    return q


def note_images():
    try:
        return sorted(p.name for p in MEDIA_DIR.glob("note-*.png"))
    except Exception:
        return []


def render_review(lg, i):
    q = review_queue(lg)
    if not q:
        return page("Review", '<div class="rvdone"><div class="big">All clear</div>'
                    '<p>Every card has been decided.</p>'
                    '<a class="btn primary" href="/">Back to inbox</a></div>')
    i = max(0, min(i, len(q) - 1))
    b = q[i]
    total = len(q)
    pct = int(i / total * 100)
    acts = ACTIONS.get(b.status, [])
    sw = SWIPE.get(b.status)
    pa, pl, na, nl = sw if sw else (None, None, None, None)

    prev = f'/review?i={i-1}' if i > 0 else None
    r = [f'<div class="rvwrap rv" data-i="{i}" data-id="{esc(b.id)}"'
         + (f' data-pos="{esc(pa)}"' if pa else "")
         + (f' data-neg="{esc(na)}"' if na else "") + '>']

    r.append('<div class="rvhdr">'
             + (f'<a class="rvback" href="{prev}" aria-label="Previous card">\u2190</a>'
                if prev else '<span class="rvback ghosted">\u2190</span>')
             + f'<span class="rvcount">{i+1} <i>/</i> {total}</span>'
             f'<span class="rvid">{esc(disp_id(b.id))}</span>'
             f'<a class="rvexit" href="/" aria-label="Exit review">\u2715</a>'
             f'<div class="rvbar"><div class="rvfill" style="width:{pct}%"></div></div></div>')

    r.append('<div class="rvscroll"><div class="rvstage">')
    if pa:
        r.append(f'<div class="rvsw pos"><span>\u2713 {esc(pl)}</span></div>')
    if na:
        r.append(f'<div class="rvsw neg"><span>{esc(nl)}</span></div>')
    r.append('<div class="rvcard" id="rvcard">')
    if b.source_text:
        r.append(f'<blockquote class="rvnote">{esc(b.source_text)}'
                 f'<cite>your note, verbatim</cite></blockquote>')
    r.append(f'<h1 class="rvtitle">{esc(b.title)}</h1>')
    if acts:
        r.append(f'<div class="rvq">{esc(QUESTION.get(b.status, "What do you want to do?"))}</div>')

    for name, label in (("root_cause", "Why it happens"), ("fix_approach", "The fix")):
        v = getattr(b, name, None) or ""
        if not v:
            continue
        lng = len(v) > 260
        r.append(f'<div class="field"><div class="k">{label}</div>'
                 f'<div class="{"fv clamped" if lng else "fv"}">{esc(v)}</div>')
        if lng:
            r.append('<button type="button" class="showmore" onclick="var v=this.previousElementSibling;'
                     "v.classList.toggle('clamped');"
                     "this.textContent=v.classList.contains('clamped')?'Show more':'Show less';"
                     '">Show more</button>')
        r.append('</div>')

    IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic")
    attached, notes = set(), []
    for pr in (b.proof or []):
        ref = str(pr.get("ref", ""))
        if ref.lower().endswith(IMG_EXT) and (MEDIA_DIR / Path(ref).name).exists():
            attached.add(Path(ref).name)
        elif ref:
            notes.append((pr.get("kind") or "note", ref))
    if attached:
        r.append('<div class="field"><div class="k">Attached</div><div class="rvshots">')
        for fn in sorted(attached):
            r.append(f'<img class="rvshot" src="/media?f={quote(fn)}" alt="attached" '
                     f'onclick="lightbox(this.src)" loading="lazy">')
        r.append('</div></div>')
    for kind, ref in notes:
        r.append(f'<div class="field"><div class="k">{esc(kind)}</div>'
                 f'<div class="fv">{esc(ref)}</div></div>')

    imgs = [n for n in note_images() if n not in attached]
    if imgs:
        r.append('<div class="field"><div class="k">From your note \u2014 tap to attach</div>'
                 '<div class="rvstrip">')
        for fn in imgs:
            r.append(f'<form method="post" action="/attach" class="rvpick">'
                     f'<input type="hidden" name="id" value="{esc(b.id)}">'
                     f'<input type="hidden" name="img" value="{esc(fn)}">'
                     f'<input type="hidden" name="ret" value="/review?i={i}">'
                     f'<button type="submit" class="rvpickbtn">'
                     f'<img src="/media?f={quote(fn)}" alt="from note" loading="lazy">'
                     f'<span class="rvplus">+</span></button></form>')
        r.append('</div></div>')

    nxt = f'/review?i={i+1}' if i + 1 < total else '/'
    r.append(f'<a class="rvfull" href="/issue/{quote(b.id)}">Open full issue \u2192</a>')
    r.append('</div></div></div>')  # /rvcard /rvstage /rvscroll

    r.append('<div class="rvact">')
    if acts:
        r.append(f'<form class="rvbtns" method="post" action="/act" id="rvform">'
                 f'<input type="hidden" name="id" value="{esc(b.id)}">'
                 f'<input type="hidden" name="ret" value="/review?i={i}">')
        for label, action, style in acts:
            r.append(f'<button class="btn {style}" name="action" value="{action}">{label}</button>')
        r.append('</form>')
        cons = CONSEQUENCE.get(b.status, "")
        if cons:
            r.append(f'<div class="rvcons">{esc(cons)}</div>')
    r.append(f'<a class="rvskip" href="{nxt}">Skip this one \u2192</a>')
    r.append('</div></div>')
    r.append('<div class="lb" id="lb" onclick="this.classList.remove(\'on\')"><img id="lbimg" alt=""></div>')
    return page("Review", "".join(r))


def render_detail(b):
    dm = ['<div class="detail"><div class="dmain">',
          '<a class="back" href="/">\u2190 All issues</a>',
          f'<div class="dtop"><span class="rid">{esc(disp_id(b.id))}</span>'
          f'<span class="dst" style="color:{STATUS_COLOR.get(b.status, "var(--muted)")}">'
          f'{esc(GROUP_LABEL.get(b.status, b.status))}</span>'
          f'<button type="button" class="linkbtn" onclick="copyLink()" title="Copy issue link">'
          f'{ICONS["link"]}Copy link</button></div>',
          f'<h1 class="dtitle">{esc(b.title)}</h1>']

    # ---- the decision, above the fold ----
    acts = ACTIONS.get(b.status, [])
    if acts:
        dm.append(f'<div class="decide"><div class="dq">{esc(QUESTION.get(b.status, "What do you want to do?"))}</div>'
                  f'<form class="dacts" method="post" action="/act">'
                  f'<input type="hidden" name="id" value="{esc(b.id)}">'
                  f'<input type="hidden" name="ret" value="/issue/{quote(b.id)}">')
        for label, action, style in acts:
            dm.append(f'<button class="btn {style}" name="action" value="{action}">{label}</button>')
        if any(a == "sendback" for _, a, _ in acts):
            dm.append('<input class="note-in" type="text" name="note" placeholder="Add a note (optional)\u2026">')
        dm.append('</form>')
        cons = CONSEQUENCE.get(b.status, "")
        if cons:
            dm.append(f'<div class="dcons">{esc(cons)}</div>')
        dm.append('</div>')

    # ---- the brief: only what exists ----
    dm.append(f'<form method="post" action="/update" id="editform">'
              f'<input type="hidden" name="id" value="{esc(b.id)}">')
    FIELDS = [("symptom", "What happens", "Plain-language description"),
              ("root_cause", "Why it happens", "Filled in by triage"),
              ("fix_approach", "The fix", "What we will do")]
    for name, label, ph in FIELDS:
        val = getattr(b, name, None) or ""
        if not val:
            continue
        long = len(val) > 300
        dm.append(f'<div class="field"><div class="k">{label}</div>'
                  f'<div class="{"fv clamped" if long else "fv"}">{esc(val)}</div>')
        if long:
            dm.append('<button type="button" class="showmore" onclick="var v=this.previousElementSibling;'
                      "v.classList.toggle('clamped');"
                      "this.textContent=v.classList.contains('clamped')?'Show more':'Show less';"
                      '">Show more</button>')
        dm.append('</div>')
    dm.append('<details class="more"><summary>Edit details</summary>'
              f'<div class="field"><div class="k">Title</div>'
              f'<textarea class="f" name="title" rows="2">{esc(b.title)}</textarea></div>')
    for name, label, ph in FIELDS:
        val = getattr(b, name, None) or ""
        dm.append(f'<div class="field"><div class="k">{label}</div>'
                  f'<textarea class="f" name="{name}" placeholder="{ph}">{esc(val)}</textarea></div>')
    dm.append('</details>')

    if b.conflict:
        dm.append(f'<div class="conflict">{ICONS["warn"]}<div><b>Conflict</b> \u2014 {esc(b.conflict)}</div></div>')
    if b.attempts:
        rws = []
        for a in b.attempts:
            gate = a.get("gate_result") or "pending"
            sha = f'<span class="asha">{esc(a["sha"])}</span>' if a.get("sha") else ""
            rws.append(f'<div class="att"><span class="an">#{a["n"]}</span>'
                       f'<span class="ah">{esc(a["hypothesis"])}</span>{sha}'
                       f'<span class="ares {esc(gate)}">{esc(gate)}</span></div>')
        dm.append(f'<div class="field"><div class="k">Attempts</div><div class="attempts">{"".join(rws)}</div></div>')

    # ---- evidence: only when it exists ----
    if b.proof:
        dm.append('<div class="field"><div class="k">Evidence</div><div class="proof">')
        for p in b.proof:
            ref = str(p.get("ref", ""))
            if ref.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic")):
                dm.append(f'<figure style="margin:0;text-align:center">'
                          f'<img src="/media?f={quote(Path(ref).name)}" alt="{esc(p.get("kind"))}">'
                          f'<figcaption style="font-size:11px;color:var(--muted2);margin-top:5px;'
                          f'text-transform:uppercase;letter-spacing:.08em">{esc(p.get("kind"))}</figcaption></figure>')
            elif ref.lower().endswith(tuple(VID_EXT)):
                dm.append(f'<figure style="margin:0;text-align:center">'
                          f'<video controls playsinline preload="metadata" '
                          f'src="/media?f={quote(Path(ref).name)}"></video>'
                          f'<figcaption style="font-size:11px;color:var(--muted2);margin-top:5px;'
                          f'text-transform:uppercase;letter-spacing:.08em">{esc(p.get("kind"))}</figcaption></figure>')
            else:
                dm.append(f'<span class="ph">{esc(p.get("kind"))}: {esc(ref)}</span>')
        dm.append('</div></div>')

    if b.code_refs:
        dm.append(f'<div class="field"><div class="k">In the code</div>'
                  f'<div class="tech">{esc(", ".join(b.code_refs))}</div></div>')

    if b.branch or b.commits:
        dm.append('<div class="field"><div class="k">Development</div><div class="dev">')
        if b.branch:
            dm.append(f'<div class="devrow"><span class="gicon">{ICONS["branch"]}</span><code>{esc(b.branch)}</code></div>')
        for c in (b.commits or []):
            dm.append(f'<a class="devrow commit" href="/diff/{quote(b.id)}">'
                      f'<span class="gicon">{ICONS["commit"]}</span><code>{esc(c.get("sha"))}</code>'
                      f'<span class="csub">{esc(c.get("subject"))}</span>'
                      f'<span class="cstat"><span class="add">+{c.get("add",0)}</span> '
                      f'<span class="del">\u2212{c.get("del",0)}</span></span></a>')
        if b.commits:
            dm.append(f'<a class="viewdiff" href="/diff/{quote(b.id)}">View full diff \u2192</a>')
        if b.fixed_in_build or b.build_status or b.shipped_in:
            dm.append('<div class="bstat">')
            if b.build_status:
                dm.append(f'<span class="pill">build: {esc(b.build_status)}</span>')
            if b.fixed_in_build:
                dm.append(f'<span class="pill">fixed in build {esc(b.fixed_in_build)}</span>')
            if b.shipped_in:
                dm.append(f'<span class="pill ship">shipped {esc(b.shipped_in)}</span>')
            dm.append('</div>')
        dm.append('</div></div>')

    dm.append('<div class="savebar"><button class="btn primary" type="submit">Save changes</button>'
              '<a class="btn" href="/">Cancel</a></div></form></div>')

    # ---- right rail: metadata only (the decision moved up top) ----
    dm.append('<div class="dprops">')
    dm.append(f'<form method="post" action="/update"><input type="hidden" name="id" value="{esc(b.id)}">'
              f'<input type="hidden" name="ret" value="/issue/{quote(b.id)}">')
    dm.append(f'<div class="prop"><div class="k">Status</div>'
              f'<select class="sel" name="status" aria-label="Status" onchange="this.form.submit()">'
              f'{sel_options([(s, GROUP_LABEL[s]) for s in STATUS_ORDER], b.status)}</select></div>')
    dm.append(f'<div class="prop"><div class="k">Priority</div>'
              f'<select class="sel" name="severity" aria-label="Priority" onchange="this.form.submit()">'
              f'{sel_options([(str(k), v) for k, v in SEV.items()], str(b.severity or 0))}</select></div>')
    dm.append(f'<div class="prop"><div class="k">App</div>'
              f'<select class="sel" name="app" aria-label="App" onchange="this.form.submit()">'
              f'{sel_options([("", "No app")] + APPS, b.app or "")}</select></div>')
    dm.append(f'<div class="prop"><div class="k">Type</div>'
              f'<select class="sel" name="kind" aria-label="Type" onchange="this.form.submit()">'
              f'{sel_options([("bug", "Bug"), ("feature", "Feature / idea")], b.kind or "bug")}</select></div>')
    dm.append('</form>')
    for k, v in [("Reported", fmt_date(b.reported)), ("Source", b.source),
                 ("Reporter", b.reporter), ("Build", b.build)]:
        if v:
            dm.append(f'<div class="prop"><div class="k">{k}</div><div class="v">{esc(v)}</div></div>')
    dm.append('</div></div>')
    return page(disp_id(b.id), "".join(dm))


def shell(title, body):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<title>{esc(title)} · Dev</title>{PWA_HEAD}<style>{CSS}</style></head><body>'
            f'<div style="min-height:100dvh;background:radial-gradient(900px 400px at 50% -10%,'
            f'color-mix(in srgb,var(--accent) 8%,transparent),transparent 60%),var(--main)">{body}</div>'
            f'<script>{JS}</script></body></html>')


_MARK = ('<span style="width:26px;height:26px;border-radius:7px;background:linear-gradient(150deg,'
         'var(--accent),#a5691f);display:grid;place-items:center;color:var(--on-accent);font-weight:700">D</span>')


def render_login(err=False):
    e = ('<div role="alert" style="color:var(--urgent);font-size:12.5px;margin-top:10px">'
         'That code didn\'t work — try again.</div>') if err else ''
    body = (f'<div style="max-width:340px;margin:0 auto;padding:16vh 22px 40px">'
            f'<div style="display:flex;align-items:center;gap:9px;margin-bottom:22px">{_MARK}'
            f'<span style="color:var(--ink);font-weight:560;font-size:18px;letter-spacing:-.02em">Dev</span></div>'
            f'<div style="color:var(--muted);font-size:14px;margin-bottom:18px">Enter the access code to manage issues.</div>'
            f'<form method="post" action="/login">'
            f'<input class="note-in" name="passcode" type="password" inputmode="numeric" autocomplete="off" '
            f'placeholder="Access code" autofocus style="font-size:16px;padding:12px 14px">{e}'
            f'<button class="btn primary block" type="submit" style="margin-top:12px">Continue</button></form>'
            f'<div style="color:var(--muted2);font-size:12.5px;margin-top:22px">Just want to report something? '
            f'<a style="color:var(--accent)" href="/submit">File a report →</a></div></div>')
    return shell("Sign in", body)


def render_submit():
    app_opts = sel_options([("", "Choose an app…")] + APPS, "")
    kind_opts = sel_options([("bug", "Something's broken"), ("feature", "An idea or request")], "bug")
    lab = 'style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted2);display:block;margin-bottom:6px;font-weight:560"'
    body = (f'<div style="max-width:520px;margin:0 auto;'
            f'padding:calc(36px + env(safe-area-inset-top)) 20px calc(80px + env(safe-area-inset-bottom))">'
            f'<div style="display:flex;align-items:center;gap:9px;margin-bottom:14px">{_MARK}'
            f'<span style="color:var(--ink);font-weight:560;font-size:17px;letter-spacing:-.02em">Dev</span>'
            f'<button class="tg" onclick="toggleTheme()" style="margin-left:auto;background:none;border:.5px solid var(--line2);'
            f'border-radius:8px;width:34px;height:32px;color:var(--muted);cursor:pointer"><span class="theme-ico">☾</span></button></div>'
            f'<h1 style="font-size:23px;color:var(--ink);font-weight:560;letter-spacing:-.022em;margin:6px 0 6px">Report something</h1>'
            f'<div style="color:var(--muted);font-size:14px;margin-bottom:22px">Found a bug or have an idea? Tell us — it goes straight to the team.</div>'
            f'<form method="post" action="/submit" enctype="multipart/form-data" style="display:grid;gap:15px">'
            f'<div><label {lab}>Which app?</label><select class="sel" name="app" required>{app_opts}</select></div>'
            f'<div><label {lab}>Type</label><select class="sel" name="kind">{kind_opts}</select></div>'
            f'<div><label {lab}>Title</label><input class="note-in" name="title" required autocomplete="off" '
            f'placeholder="One line — what happened?" style="padding:11px 13px"></div>'
            f'<div><label {lab}>Details</label><textarea class="f" name="symptom" '
            f'placeholder="What did you see? What did you expect? Which screen?" style="min-height:96px"></textarea></div>'
            f'<div><label {lab}>Screenshots or video (optional)</label>{attach_zone()}</div>'
            f'<div><label {lab}>Your name (optional)</label><input class="note-in" name="reporter" '
            f'placeholder="So we can follow up" style="padding:11px 13px"></div>'
            f'<button class="btn primary block" type="submit" style="padding:12px">Send report</button></form></div>')
    return shell("Report something", body)


def render_thanks():
    body = ('<div style="max-width:420px;margin:0 auto;padding:18vh 20px;text-align:center">'
            '<div style="width:52px;height:52px;border-radius:50%;'
            'background:color-mix(in srgb,var(--done) 15%,transparent);'
            'border:.5px solid color-mix(in srgb,var(--done) 40%,transparent);'
            'display:grid;place-items:center;margin:0 auto 16px">'
            '<svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">'
            '<path d="M5.5 11.5 9.5 15.5 16.5 7" stroke="var(--done)" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg></div>'
            '<div style="color:var(--ink);font-size:19px;font-weight:560;margin-bottom:6px">Report received</div>'
            '<div style="color:var(--muted);font-size:14px">Thank you — the team has it.</div>'
            '<a class="btn" href="/submit" style="display:inline-block;margin-top:22px">Submit another</a></div>')
    return shell("Thanks", body)


def render_activity():
    lg = Ledger()
    bugs = sorted(lg.all(), key=lambda b: b.updated or b.created or "", reverse=True)[:80]
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    week = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    buckets = [("Today", []), ("Yesterday", []), ("This week", []), ("Earlier", [])]
    for b in bugs:
        d = (b.updated or b.created or "")[:10]
        if d == today:
            buckets[0][1].append(b)
        elif d == yesterday:
            buckets[1][1].append(b)
        elif d >= week:
            buckets[2][1].append(b)
        else:
            buckets[3][1].append(b)
    hdr = (f'<div class="hdr"><span class="title">Activity</span>'
           f'<span class="ct">{len(bugs)} recently touched</span><span class="grow"></span>'
           f'<label class="search"><span class="k">{ICONS["search"]}</span>'
           f'<input id="search" placeholder="Filter activity" autocomplete="off" aria-label="Filter activity"></label>'
           f'<button class="tg" onclick="toggleTheme()" title="Toggle theme (t)" aria-label="Toggle theme">'
           f'<span class="theme-ico">☾</span></button></div>')
    rows = ['<div class="scroll">']
    any_rows = False
    for label, items in buckets:
        if not items:
            continue
        any_rows = True
        rows.append(f'<div class="group"><div class="group-h"><span class="lbl">{esc(label)}</span>'
                    f'<span class="ct">{len(items)}</span></div>')
        rows += [render_row(b) for b in items]
        rows.append('</div>')
    if not any_rows:
        rows.append('<div class="empty"><div class="big">Nothing yet</div>'
                    'Issue changes show up here, newest first.</div>')
    rows.append('<div class="empty" id="noresults" style="display:none">'
                '<div class="big">No matching activity</div></div></div>')
    return page("Activity", hdr + "".join(rows), active="activity")


def render_braindump(queued=False):
    dumps = pending_dumps()
    hdr = ('<div class="hdr"><span class="title">Brain dump</span>'
           + (f'<span class="ct">Pending triage · {len(dumps)}</span>' if dumps else '')
           + '<span class="grow"></span>'
           '<button class="tg" onclick="toggleTheme()" title="Toggle theme (t)" aria-label="Toggle theme">'
           '<span class="theme-ico">☾</span></button></div>')
    ok = ('<div class="bd-ok"><svg width="15" height="15" viewBox="0 0 15 15" fill="none" aria-hidden="true" '
          'style="flex:none;margin-top:1px"><path d="M3.4 7.9 6.2 10.7 11.6 4.6" stroke="var(--done)" '
          'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
          '<div><b>Queued</b> — these will be triaged into issues shortly.</div></div>') if queued else ''
    body = [f'<div class="scroll"><div class="bd-wrap">{ok}',
            '<h1>Brain dump</h1>',
            '<div class="bd-sub">Paste a pile of thoughts — bugs, ideas, half-sentences, a whole voice-note '
            'transcript. Nothing needs structure. An agent triages the queue into proper issues later.</div>',
            '<form method="post" action="/braindump" enctype="multipart/form-data" style="display:grid;gap:14px">',
            '<textarea class="f bd" name="dump" required '
            'placeholder="Everything on your mind — one thought per line works great, but any shape is fine."></textarea>',
            attach_zone("Optional — attach screenshots or a video"),
            '<div style="display:flex;gap:10px;align-items:center">'
            '<button class="btn primary" type="submit" style="padding:10px 18px">Queue for triage</button>'
            '<span style="font-size:12px;color:var(--muted2)">Captured locally — nothing leaves this machine.</span>'
            '</div></form>']
    if dumps:
        body.append(f'<div class="bd-q"><div class="bd-qh">Pending triage <span class="n">· {len(dumps)}</span></div>')
        for p in dumps[:25]:
            ts, prev = _dump_meta(p)
            body.append(f'<div class="bd-item"><span class="ts">{esc(ts)}</span>'
                        f'<span class="pv">{esc(prev)}</span></div>')
        body.append('</div>')
    body.append('</div></div>')
    return page("Brain dump", hdr + "".join(body), active="braindump")


def render_diff(b):
    import subprocess
    body = [f'<div class="scroll"><div class="diffwrap"><a class="back" href="/issue/{quote(b.id)}">← {esc(disp_id(b.id))}</a>',
            f'<h1>Diff · {esc(b.title)}</h1>']
    if b.branch:
        body.append(f'<div class="diffhead">⎇ {esc(b.branch)}</div>')
    for c in (b.commits or []):
        body.append(f'<div class="diffhead">● {esc(c.get("sha"))}  {esc(c.get("subject"))}  '
                    f'(<span style="color:var(--done)">+{c.get("add",0)}</span>/'
                    f'<span style="color:var(--urgent)">−{c.get("del",0)}</span>)</div>')
        patch = ""
        if b.repo:
            for _ in range(3):
                try:
                    patch = subprocess.run(["git", "-C", b.repo, "show", "--format=", "--no-color", c.get("full", c.get("sha"))],
                                           capture_output=True, text=True, timeout=20).stdout
                    break
                except Exception:
                    patch = ""
        body.append('<div class="diff">')
        for line in patch.splitlines():
            cls = ""
            if line.startswith("@@"):
                cls = "h"
            elif line.startswith("+") and not line.startswith("+++"):
                cls = "a"
            elif line.startswith("-") and not line.startswith("---"):
                cls = "d"
            elif line.startswith(("diff ", "index ", "+++", "---", "new file", "deleted", "similarity", "rename")):
                cls = "c"
            body.append(f'<span class="ln {cls}">{esc(line) if line else "&nbsp;"}</span>')
        body.append('</div>')
    if not b.commits:
        body.append('<div class="empty"><div class="big">No linked commits yet</div>'
                    'Commits land here once a fix branch is pushed.</div>')
    body.append('</div></div>')
    return page(f"Diff {disp_id(b.id)}", "".join(body))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _html(self, body, code=200, cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _cookie_val(self, name):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = _cookies.SimpleCookie(raw)
            return c[name].value if name in c else None
        except Exception:
            return None

    def _authed(self):
        return self._cookie_val("islah_s") == _op_token()

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def _form(self):
        length = int(self.headers.get("Content-Length", 0))
        return parse_qs(self.rfile.read(length).decode("utf-8"))

    def _read_raw(self):
        """Read the raw request body (capped) for multipart parsing."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        length = min(length, 256 * 1024 * 1024)
        chunks, remaining = [], length
        while remaining > 0:
            c = self.rfile.read(min(remaining, 1 << 20))
            if not c:
                break
            chunks.append(c)
            remaining -= len(c)
        return b"".join(chunks)

    def _bytes(self, data, ctype, code=200, cache=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    _MIME_FALLBACK = {".heic": "image/heic", ".heif": "image/heif", ".webp": "image/webp",
                      ".mov": "video/quicktime", ".m4v": "video/x-m4v", ".webm": "video/webm",
                      ".mp4": "video/mp4"}

    def _serve_media(self, ref):
        import mimetypes
        import os
        base = os.path.realpath(os.path.expanduser("~/.aos/islah/media"))
        rp = os.path.realpath(os.path.join(base, os.path.basename(ref)))
        if not rp.startswith(base) or not os.path.isfile(rp):
            self.send_response(404); self.end_headers(); return
        ctype = (mimetypes.guess_type(rp)[0]
                 or self._MIME_FALLBACK.get(os.path.splitext(rp)[1].lower())
                 or "application/octet-stream")
        size = os.path.getsize(rp)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if rng and rng.startswith("bytes=") and size:
            # single-range only — enough for iOS Safari video scrubbing
            try:
                spec = rng[6:].split(",")[0].strip()
                s, _, e = spec.partition("-")
                if s:
                    start = int(s)
                    end = int(e) if e else size - 1
                else:
                    start = max(0, size - int(e))
                end = min(end, size - 1)
                partial = 0 <= start <= end
            except ValueError:
                partial = False
            if not partial or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        with open(rp, "rb") as fh:
            fh.seek(start)
            data = fh.read(end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/login":
            self._html(render_login("e" in parse_qs(u.query))); return
        if u.path == "/submit":
            self._html(render_submit()); return
        if u.path == "/thanks":
            self._html(render_thanks()); return
        if u.path == "/manifest.json":
            self._bytes(json.dumps(MANIFEST, ensure_ascii=False).encode("utf-8"),
                        "application/manifest+json; charset=utf-8", cache="public, max-age=3600")
            return
        if u.path in ("/icon-180.png", "/icon-512.png"):
            ensure_icons()
            p = ASSETS_DIR / u.path.lstrip("/")
            if p.is_file():
                self._bytes(_retry_io(p.read_bytes), "image/png", cache="public, max-age=86400")
            else:
                self.send_response(404); self.end_headers()
            return
        if not self._authed():
            self.send_response(303); self.send_header("Location", "/login"); self.end_headers(); return
        if u.path == "/activity":
            self._html(render_activity()); return
        if u.path == "/braindump":
            self._html(render_braindump(queued="q" in parse_qs(u.query))); return
        if u.path == "/review":
            try:
                i = int(parse_qs(u.query).get("i", ["0"])[0])
            except ValueError:
                i = 0
            self._html(render_review(Ledger(), i)); return
        if u.path == "/media":
            self._serve_media(parse_qs(u.query).get("f", [""])[0]); return
        if u.path.startswith("/diff/"):
            b = Ledger().get(unquote(u.path[len("/diff/"):]))
            if not b:
                self._html(page("Not found", '<div class="empty"><div class="big">Issue not found</div></div>'), 404)
                return
            self._html(render_diff(b)); return
        if u.path.startswith("/issue/"):
            b = Ledger().get(unquote(u.path[len("/issue/"):]))
            if not b:
                self._html(page("Not found", '<div class="empty"><div class="big">Issue not found</div></div>'), 404)
                return
            self._html(render_detail(b))
            return
        q = parse_qs(u.query)
        app = q.get("app", [None])[0] or None
        status = q.get("status", [None])[0] or None
        view = q.get("view", [None])[0]
        # Default home = Inbox (only what needs you). "?view=all" or any filter = full list.
        inbox = (view != "all" and not app and not status)
        self._html(render_list(app, status, inbox=inbox))

    def do_POST(self):
        u = urlparse(self.path)
        ctype = (self.headers.get("Content-Type") or "").lower()
        files = []
        if ctype.startswith("multipart/form-data"):
            d, files = parse_multipart(self.headers.get("Content-Type", ""), self._read_raw())
        else:
            d = self._form()
        def g(k, dv=""):
            return d.get(k, [dv])[0]

        if u.path == "/login":
            if g("passcode") == PASSCODE:
                self.send_response(303)
                self.send_header("Set-Cookie",
                                 f"islah_s={_op_token()}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
                self.send_header("Location", "/")
                self.end_headers()
            else:
                self.send_response(303); self.send_header("Location", "/login?e=1"); self.end_headers()
            return

        if u.path == "/submit":
            title = g("title").strip()
            if title:
                saved = save_uploads(files)
                lg = Ledger()
                bug = lg.add(title, app=g("app") or None, kind=g("kind", "bug"),
                             symptom=g("symptom") or None, reporter=g("reporter").strip() or "web visitor",
                             source="web", status="new",
                             attachments=[n for n, _ in saved] or None)
                for n, k in saved:
                    lg.add_proof(bug.id, k, n)
            self.send_response(303); self.send_header("Location", "/thanks"); self.end_headers()
            return

        if not self._authed():
            self.send_response(403); self.end_headers(); return

        lg = Ledger()
        if u.path == "/braindump":
            text = g("dump").strip()
            saved = save_uploads(files)
            if text or saved:
                save_dump(text, saved)
            self._redirect("/braindump?q=1"); return

        if u.path == "/create":
            title = g("title").strip()
            if not title:
                self._redirect("/"); return
            saved = save_uploads(files)
            kwargs = dict(app=g("app") or None, kind=g("kind", "bug"),
                          symptom=g("symptom") or None, screen=g("screen") or None,
                          source="manual", reporter="self", status="new",
                          attachments=[n for n, _ in saved] or None)
            sev = g("severity", "0")
            if sev.isdigit():
                kwargs["severity"] = int(sev)
            bug = lg.add(title, **{k: v for k, v in kwargs.items() if v is not None})
            for n, k in saved:
                lg.add_proof(bug.id, k, n)
            self._redirect(f"/issue/{quote(bug.id)}"); return

        if u.path == "/attach":
            bid, img = g("id"), g("img")
            ret = g("ret") or "/"
            if bid and img and "/" not in img and ".." not in img:
                try:
                    Ledger().add_proof(bid, "reference", str(MEDIA_DIR / img))
                except Exception:
                    pass
            self._redirect(ret); return
        if u.path == "/update":
            bid = g("id")
            if lg.get(bid):
                fields = {}
                for k in ("title", "status", "kind", "app", "symptom", "root_cause",
                          "fix_approach", "screen", "reporter"):
                    if k in d:
                        fields[k] = g(k)
                if "severity" in d and g("severity").isdigit():
                    fields["severity"] = int(g("severity"))
                if fields.get("status") and fields["status"] not in ALL_STATES:
                    fields.pop("status")
                lg.update(bid, **fields)
            self._redirect(g("ret") or f"/issue/{quote(bid)}"); return

        if u.path == "/act":
            bid, action, note, ret = g("id"), g("action"), g("note"), g("ret", "/")
            b = lg.get(bid)
            if b:
                if action == "approve":
                    lg.approve(bid)
                elif action == "greenlight":
                    lg.set_status(bid, "fixing")
                elif action == "triage":
                    lg.set_status(bid, "triaging")
                elif action == "dismiss":
                    lg.set_status(bid, "wont-fix")
                elif action == "sendback":
                    lg.update(bid, status="needs-info",
                              notes=((b.notes or "") + f"\n[sent back] {note}").strip())
            self._redirect(ret); return

        self._redirect("/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--snapshot")
    a = ap.parse_args()
    if a.snapshot:
        Path(a.snapshot).write_text(render_list(None))
        print(f"snapshot: {a.snapshot}")
        return
    ensure_icons()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"Dev board → http://127.0.0.1:{a.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
