#!/usr/bin/env python3
"""
Iṣlāḥ — App Store Connect intake.

Pulls TestFlight beta feedback (tester screenshots + comments) and beta crash
submissions for the operator's iOS apps, symbolicates crashes with xcsym, and
files each into the Iṣlāḥ ledger — idempotently (dedup on source_ref, so this is
safe to poll on a schedule).

Auth (App Store Connect API, ES256 JWT — no MCP server required):
  - issuer id  : keychain secret ASC_ISSUER_ID
  - key id     : keychain secret ASC_KEY_ID
  - private key: ~/.appstoreconnect/private_keys/AuthKey_<KEYID>.p8
                 (falls back to ~/.private_keys, ~/.aos/config/keys)

CLI:
  python3 ascbuild.py sync     # pull feedback + crashes into the ledger
  python3 ascbuild.py builds   # show the latest build per app
  python3 ascbuild.py sync --app quran-garden   # scope to one app
  python3 ascbuild.py sync --dry-run            # show what would be filed

Data model mapping:
  TestFlight screenshot feedback -> source="testflight", classification="ux"
  Beta crash submission          -> source="asc-crash",  classification="crash", severity=1
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    import jwt  # PyJWT
    import requests
except ImportError as e:  # pragma: no cover
    sys.stderr.write(f"missing dependency: {e} (need PyJWT + requests + cryptography)\n")
    raise

# --- ledger wiring (mirror importer.py) --------------------------------------
APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
from store import DATA_DIR, MEDIA_DIR, Ledger  # noqa: E402

# --- constants ---------------------------------------------------------------
ASC_BASE = "https://api.appstoreconnect.apple.com"
AGENT_SECRET = os.path.expanduser("~/aos/core/bin/cli/agent-secret")
XCSYM = os.path.expanduser(
    "~/.claude/plugins/cache/axiom-marketplace/axiom/3.4.0/bin/xcsym"
)
REGISTRY_PATHS = [
    "/Volumes/AOS-X/project/aos/config/islah-apps.yaml",
    os.path.expanduser("~/project/aos/config/islah-apps.yaml"),
]
AOSX_COPY = "/Volumes/AOS-X/project/aos/core/engine/islah/ascbuild.py"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _secret(key: str) -> str:
    try:
        r = subprocess.run([AGENT_SECRET, "get", key],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip()
    except Exception:
        return ""


# --- auth --------------------------------------------------------------------
class AuthError(RuntimeError):
    pass


def _find_p8(key_id: str) -> str | None:
    names = [f"AuthKey_{key_id}.p8", f"{key_id}.p8"] if key_id else []
    dirs = [
        "~/.appstoreconnect/private_keys",
        "~/.private_keys",
        "~/.aos/config/keys",
        "~/.aos",
    ]
    for d in dirs:
        dp = Path(os.path.expanduser(d))
        for n in names:
            p = dp / n
            if p.is_file():
                return str(p)
        # any .p8 in the dir as a last resort
        if dp.is_dir():
            hits = list(dp.glob("*.p8"))
            if hits:
                return str(hits[0])
    return None


def load_credentials() -> dict:
    """Return {issuer_id, key_id, p8_path, p8}. Raise AuthError naming what's missing."""
    issuer = _secret("ASC_ISSUER_ID")
    key_id = _secret("ASC_KEY_ID")
    p8_path = _find_p8(key_id)
    missing = []
    if not issuer:
        missing.append("issuer id (keychain secret ASC_ISSUER_ID)")
    if not key_id:
        missing.append("key id (keychain secret ASC_KEY_ID)")
    if not p8_path:
        looked = "~/.appstoreconnect/private_keys, ~/.private_keys, ~/.aos/config/keys, ~/.aos"
        missing.append(f"private .p8 (looked in: {looked})")
    if missing:
        raise AuthError("missing ASC credentials -> " + "; ".join(missing))
    return {"issuer_id": issuer, "key_id": key_id,
            "p8_path": p8_path, "p8": Path(p8_path).read_text()}


class ASC:
    """Thin App Store Connect REST client. Mints a fresh 20-min ES256 JWT."""

    def __init__(self, creds: dict):
        self.creds = creds
        self._token = None
        self._exp = 0

    def _jwt(self) -> str:
        now = int(time.time())
        if self._token and now < self._exp - 60:
            return self._token
        self._exp = now + 1200
        self._token = jwt.encode(
            {"iss": self.creds["issuer_id"], "iat": now, "exp": self._exp,
             "aud": "appstoreconnect-v1"},
            self.creds["p8"], algorithm="ES256",
            headers={"kid": self.creds["key_id"], "typ": "JWT"},
        )
        return self._token

    def get(self, path: str, **params):
        url = path if path.startswith("http") else ASC_BASE + path
        for attempt in range(4):
            r = requests.get(url, headers={"Authorization": "Bearer " + self._jwt()},
                             params=params or None, timeout=45)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"ASC {r.status_code} {path}: {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"ASC rate-limited: {path}")

    def paged(self, path: str, max_items: int = 200, **params):
        """Yield resources across pages, bounded by max_items."""
        params.setdefault("limit", 200)
        out, next_url = [], None
        while True:
            data = self.get(next_url or path, **({} if next_url else params))
            out.extend(data.get("data", []))
            if len(out) >= max_items:
                return out[:max_items]
            next_url = (data.get("links") or {}).get("next")
            if not next_url:
                return out

    def download(self, url: str, dest: Path) -> bool:
        try:
            r = requests.get(url, timeout=60)
            if r.status_code >= 400:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return True
        except Exception:
            return False


# --- app registry ------------------------------------------------------------
def load_registry() -> dict:
    # AOS-X can throw transient EPERM; retry across candidate paths before giving up.
    for attempt in range(4):
        for p in REGISTRY_PATHS:
            try:
                if os.path.isfile(p):
                    return (yaml.safe_load(Path(p).read_text()) or {}).get("apps", {})
            except OSError:
                continue
        time.sleep(1.0)
    raise RuntimeError("could not read islah-apps.yaml (AOS-X unavailable): "
                       + ", ".join(REGISTRY_PATHS))


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum())


def resolve_apps(asc: ASC, registry: dict, only: str | None = None) -> dict:
    """Map registry slug -> {app_id, name, bundle_id, cfg}. iOS apps only."""
    asc_apps = asc.paged("/v1/apps", max_items=200,
                         **{"fields[apps]": "name,bundleId"})
    by_bundle = {a["attributes"].get("bundleId"): a for a in asc_apps}
    resolved = {}
    for slug, cfg in registry.items():
        if only and slug != only:
            continue
        if (cfg or {}).get("platform") == "web":
            continue
        match = None
        # explicit overrides win
        if cfg.get("asc_app_id"):
            match = next((a for a in asc_apps if a["id"] == str(cfg["asc_app_id"])), None)
        if not match and cfg.get("bundle_id"):
            match = by_bundle.get(cfg["bundle_id"])
        # else fuzzy display-name match (contains, normalized)
        if not match:
            disp = _norm(cfg.get("display") or slug)
            for a in asc_apps:
                nm = _norm(a["attributes"].get("name"))
                if disp and (disp in nm or nm in disp):
                    match = a
                    break
        if match:
            resolved[slug] = {
                "app_id": match["id"],
                "name": match["attributes"].get("name"),
                "bundle_id": match["attributes"].get("bundleId"),
                "cfg": cfg,
            }
    return resolved


# --- build status ------------------------------------------------------------
def asc_build_status(asc: ASC, app_id: str) -> dict | None:
    """Latest build for an app: {build, version, processingState, uploadedDate, expired}."""
    data = asc.get("/v1/builds", **{
        "filter[app]": app_id, "sort": "-version", "limit": 1,
        "include": "preReleaseVersion",
        "fields[builds]": "version,processingState,uploadedDate,expired",
        "fields[preReleaseVersions]": "version",
    })
    builds = data.get("data", [])
    if not builds:
        return None
    b = builds[0]["attributes"]
    ver = None
    for inc in data.get("included", []):
        if inc["type"] == "preReleaseVersions":
            ver = inc["attributes"].get("version")
            break
    return {"build": b.get("version"), "version": ver,
            "processingState": b.get("processingState"),
            "uploadedDate": b.get("uploadedDate"), "expired": b.get("expired")}


# --- crash symbolication -----------------------------------------------------
def _top_app_frame(frames: list, app_name: str) -> dict | None:
    """First real app frame (the app's own binary), best-effort.

    Deliberately returns None rather than a system frame: on SIGABRT / watchdog
    crashes the crashed thread's top frames are __pthread_kill/abort, which say
    nothing useful. In that case the caller leads the title with the exception."""
    for f in frames:
        if f.get("image") == app_name and f.get("file") \
                and "compiler-generated" not in f.get("file", ""):
            return f
    for f in frames:  # any symbolicated frame in the app's own binary
        if f.get("image") == app_name and f.get("symbol"):
            return f
    return None


def symbolicate(crash_path: Path) -> dict:
    """Run xcsym; return {ok, exception, pattern, top_frame, build, thread_signal, raw}."""
    if not os.path.isfile(XCSYM):
        return {"ok": False, "error": f"xcsym not found at {XCSYM}"}
    try:
        r = subprocess.run([XCSYM, "crash", "-format", "summary", str(crash_path)],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return {"ok": False, "error": f"xcsym failed: {e}"}
    # xcsym uses non-zero exit codes to signal symbolication completeness (e.g.
    # partial / missing dSYM), NOT hard failure — so parse stdout regardless and
    # only treat unparseable output as an error.
    try:
        j = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": (r.stderr or r.stdout)[:300]}
    c = j.get("crash", {})
    app_name = (c.get("app") or {}).get("name") or ""
    thr = c.get("crashed_thread") or {}
    top = _top_app_frame(thr.get("frames", []), app_name)
    top_desc = None
    if top:
        loc = ""
        if top.get("file") and "compiler-generated" not in top["file"]:
            base = os.path.basename(top["file"])
            loc = f" ({base}:{top.get('line')})" if top.get("line") else f" ({base})"
        top_desc = f"{top.get('symbol', top.get('address', '?'))}{loc}"
    exc = c.get("exception") or {}
    return {
        "ok": True,
        "exception": exc.get("type"),
        "exception_subtype": exc.get("subtype"),
        "signal": exc.get("signal"),
        "pattern": c.get("pattern_tag"),
        "pattern_reason": c.get("pattern_reason"),
        "top_frame": top_desc,
        "app_version": (c.get("app") or {}).get("version"),
        "symbolicated": bool(top and top.get("symbolicated")),
    }


def _build_from_version(v: str | None) -> str | None:
    # "0.2.2 (49)" -> "49"
    if v and "(" in v and ")" in v:
        return v[v.rfind("(") + 1:v.rfind(")")].strip()
    return None


# --- intake ------------------------------------------------------------------
def _clip(s: str, n: int = 80) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _resolve_screenshot_urls(attrs: dict) -> list[tuple[str, str]]:
    """Return [(url, filename)] from a screenshot submission's attributes.

    ASC returns screenshots as an array of image-asset objects. Field names have
    drifted across API versions, so probe defensively for a direct URL or an
    imageAsset templateUrl (which needs {w}x{h}{f} substitution)."""
    out = []
    shots = attrs.get("screenshots") or attrs.get("feedbackScreenshots") or []
    for i, s in enumerate(shots):
        if not isinstance(s, dict):
            continue
        fn = s.get("fileName") or f"shot{i}.png"
        url = s.get("url") or s.get("downloadUrl")
        if not url:
            ia = s.get("imageAsset") or {}
            tmpl = ia.get("templateUrl")
            if tmpl:
                w = ia.get("width", 1290)
                h = ia.get("height", 2796)
                url = tmpl.replace("{w}", str(w)).replace("{h}", str(h)).replace("{f}", "png")
        if url:
            out.append((url, fn))
    return out


def sync_screenshots(asc: ASC, slug: str, app: dict, ledger: Ledger,
                     existing: set, dry: bool) -> list:
    filed = []
    subs = asc.paged(f"/v1/apps/{app['app_id']}/betaFeedbackScreenshotSubmissions",
                     max_items=200)
    for sub in subs:
        sid = sub["id"]
        ref = f"testflight:{sid}"
        if ref in existing:
            continue
        a = sub.get("attributes", {})
        comment = (a.get("comment") or "").strip()
        title = _clip(comment) if comment else "TestFlight feedback (screenshot)"
        reporter = a.get("email") or "tester"
        attachments, proof = [], []
        for i, (url, fn) in enumerate(_resolve_screenshot_urls(a)):
            ext = os.path.splitext(fn)[1].lower() or ".png"
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".heic"):
                ext = ".png"
            name = f"{slug}-tf-{sid}-{i}{ext}"
            dest = MEDIA_DIR / name
            if not dry and asc.download(url, dest):
                attachments.append(name)  # bare filename (board serves from MEDIA_DIR)
                proof.append({"kind": "screenshot",
                              "ref": f"/media?f={name}", "at": _now()})
        if not dry:
            ledger.add(
                title, app=slug, kind="bug", source="testflight",
                source_ref=ref, reporter=reporter, status="new",
                classification="ux", symptom=comment or "(no comment)",
                device=a.get("deviceModel"), os=a.get("osVersion"),
                reported=a.get("createdDate"),
                attachments=attachments or None, proof=proof or None,
            )
        existing.add(ref)
        filed.append((ref, title))
    return filed


def sync_crashes(asc: ASC, slug: str, app: dict, ledger: Ledger,
                 existing: set, dry: bool) -> list:
    filed = []
    # Only surface crashes on the CURRENT build — a crash from an older build was
    # (presumably) already fixed by shipping the newer one. Window 0 = latest build only.
    RECENT_WINDOW = 0
    _ls = asc_build_status(asc, app["app_id"]) or {}
    try:
        latest_build = int(str(_ls.get("build"))) if _ls.get("build") else None
    except (ValueError, TypeError):
        latest_build = None
    subs = asc.paged(f"/v1/apps/{app['app_id']}/betaFeedbackCrashSubmissions",
                     max_items=200)
    for sub in subs:
        sid = sub["id"]
        ref = f"asc-crash:{sid}"
        if ref in existing:
            continue
        a = sub.get("attributes", {})
        comment = (a.get("comment") or "").strip()

        # pull + save the crash log, then symbolicate
        sym, crash_file = {}, None
        try:
            cl = asc.get(f"/v1/betaFeedbackCrashSubmissions/{sid}/crashLog")
            log_text = ((cl.get("data") or {}).get("attributes") or {}).get("logText")
        except Exception:
            log_text = None
        crash_name = f"{slug}-crash-{sid}.crash"
        if log_text:
            crash_file = MEDIA_DIR / crash_name
            if not dry:
                crash_file.parent.mkdir(parents=True, exist_ok=True)
                crash_file.write_text(log_text)
                sym = symbolicate(crash_file)
            else:
                # symbolicate from a temp copy in dry-run too (read-only insight)
                tmp = Path(DATA_DIR) / f".dry_{sid}.crash"
                tmp.write_text(log_text)
                sym = symbolicate(tmp)
                tmp.unlink(missing_ok=True)

        exc = sym.get("exception") or "crash"
        pattern = sym.get("pattern") or ""
        top = sym.get("top_frame")
        build = _build_from_version(sym.get("app_version"))
        # Only file crashes on the current build. Older builds are presumed fixed;
        # an unknown/unparseable build can't be verified as current, so skip it too.
        try:
            _cur = int(str(build)) if build else None
        except (ValueError, TypeError):
            _cur = None
        if latest_build is not None and (_cur is None or _cur < latest_build - RECENT_WINDOW):
            existing.add(ref)
            continue
        # title: prefer the crashing app frame; else lead with the exception
        # (a symbolicated __pthread_kill is meaningless), then the tester comment.
        if top:
            title = _clip(f"Crash: {top}")
        else:
            head = None
            if exc and exc != "crash":
                head = f"{exc} ({pattern})" if pattern else exc
            elif pattern:
                head = pattern
            if head:
                title = _clip(f"Crash: {head}" + (f' — "{comment}"' if comment else ""))
            else:
                title = _clip(f"Crash: {comment}" if comment else "Crash (uncategorized)")

        sym_bits = []
        if exc:
            sym_bits.append(exc)
        if sym.get("pattern_reason"):
            sym_bits.append(sym["pattern_reason"])
        if top:
            sym_bits.append(f"top frame: {top}")
        if comment:
            sym_bits.append(f'tester: "{comment}"')
        symptom = " · ".join(sym_bits) or "TestFlight crash"

        if top:
            note = "" if sym.get("symbolicated") else " (unsymbolicated — dSYM missing)"
            root_cause = f"{exc} at {top}{note}"
        elif sym.get("ok"):
            root_cause = " · ".join(x for x in (exc, sym.get("pattern_reason")) if x)
        else:
            root_cause = None

        attachments = [crash_name] if (crash_file and not dry) else []
        proof = [{"kind": "crashlog", "ref": f"/media?f={crash_name}", "at": _now()}] \
            if (crash_file and not dry) else []

        if not dry:
            ledger.add(
                title, app=slug, kind="bug", source="asc-crash",
                source_ref=ref, reporter=a.get("email") or "tester", status="new",
                classification="crash", severity=1, symptom=symptom,
                root_cause=root_cause, build=build,
                device=a.get("deviceModel"), os=a.get("osVersion"),
                reported=a.get("createdDate"),
                attachments=attachments or None, proof=proof or None,
            )
        existing.add(ref)
        filed.append((ref, title))
    return filed


def stamp_build_status(asc: ASC, slug: str, app: dict, ledger: Ledger,
                       dry: bool) -> str | None:
    """Best-effort: record the app's latest TestFlight build onto committed,
    still-open issues. We do NOT auto-close: commit->build mapping isn't reliable
    from ASC alone, so we annotate build_status and leave status changes to the
    human/board."""
    st = asc_build_status(asc, app["app_id"])
    if not st:
        return None
    latest = f"{st['version'] or '?'} ({st['build']}) — {st['processingState']}"
    if not dry:
        for bug in ledger.list(app=slug, open_only=True):
            if bug.commits and bug.status in ("approved", "awaiting-approval", "verifying"):
                ledger.update(bug.id, build_status=f"latest TestFlight build: {latest}")
    return latest


# --- orchestration -----------------------------------------------------------
def sync(only: str | None = None, dry: bool = False) -> dict:
    creds = load_credentials()
    asc = ASC(creds)
    registry = load_registry()
    apps = resolve_apps(asc, registry, only=only)
    if not apps:
        return {"error": "no iOS apps resolved from registry", "apps": {}}
    ledger = Ledger()
    existing = {b.source_ref for b in ledger.all() if b.source_ref}
    report = {"apps": {}}
    for slug, app in apps.items():
        shots = sync_screenshots(asc, slug, app, ledger, existing, dry)
        crashes = sync_crashes(asc, slug, app, ledger, existing, dry)
        latest = stamp_build_status(asc, slug, app, ledger, dry)
        report["apps"][slug] = {
            "app_id": app["app_id"], "name": app["name"],
            "screenshots_filed": shots, "crashes_filed": crashes,
            "latest_build": latest,
        }
    return report


def builds(only: str | None = None) -> dict:
    creds = load_credentials()
    asc = ASC(creds)
    apps = resolve_apps(asc, load_registry(), only=only)
    out = {}
    for slug, app in apps.items():
        out[slug] = {"name": app["name"], "app_id": app["app_id"],
                     "status": asc_build_status(asc, app["app_id"])}
    return out


# --- self-copy to AOS-X (retry on EPERM; AOS-X is flaky) ----------------------
def copy_to_aosx() -> str:
    src = str(APP_DIR / "ascbuild.py")
    for attempt in range(3):
        try:
            os.makedirs(os.path.dirname(AOSX_COPY), exist_ok=True)
            shutil.copy2(src, AOSX_COPY)
            return f"copied -> {AOSX_COPY}"
        except PermissionError:
            time.sleep(1.5)
        except FileNotFoundError:
            return f"skip: AOS-X path unavailable ({AOSX_COPY})"
        except Exception as e:
            return f"copy failed: {e}"
    return f"copy failed after retries (EPERM): {AOSX_COPY}"


# --- CLI ---------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(prog="ascbuild")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="pull TestFlight feedback + crashes into the ledger")
    s.add_argument("--app", help="restrict to one registry slug")
    s.add_argument("--dry-run", action="store_true")
    b = sub.add_parser("builds", help="show latest build per app")
    b.add_argument("--app")
    sub.add_parser("copy", help="copy this module to the AOS-X engine dir")
    a = ap.parse_args()

    try:
        if a.cmd == "sync":
            rep = sync(only=a.app, dry=a.dry_run)
            if rep.get("error"):
                print("⚠", rep["error"])
                sys.exit(1)
            tag = "would file" if a.dry_run else "filed"
            total = 0
            for slug, r in rep["apps"].items():
                print(f"\n{slug} · {r['name']}  [{r['app_id']}]")
                print(f"  latest TestFlight build: {r['latest_build']}")
                for ref, title in r["screenshots_filed"]:
                    print(f"  + [ux]    {title}  ({tag})")
                for ref, title in r["crashes_filed"]:
                    print(f"  + [crash] {title}  ({tag})")
                n = len(r["screenshots_filed"]) + len(r["crashes_filed"])
                total += n
                if n == 0:
                    print("  (nothing new)")
            print(f"\n{total} item(s) {tag}.")
        elif a.cmd == "builds":
            for slug, r in builds(only=a.app).items():
                st = r["status"]
                if st:
                    print(f"{slug:16} {r['name']:32} v{st['version']} "
                          f"build {st['build']}  {st['processingState']}"
                          f"  ({st['uploadedDate']})")
                else:
                    print(f"{slug:16} {r['name']:32} (no builds)")
        elif a.cmd == "copy":
            print(copy_to_aosx())
    except AuthError as e:
        print("⚠ ASC auth unavailable —", e)
        print("  Provide: ASC_ISSUER_ID + ASC_KEY_ID (keychain via agent-secret) "
              "and AuthKey_<KEYID>.p8 in ~/.appstoreconnect/private_keys/")
        sys.exit(2)


if __name__ == "__main__":
    main()
