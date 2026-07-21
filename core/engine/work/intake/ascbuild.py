#!/usr/bin/env python3
"""App Store Connect intake → ``work.db`` bug tasks.

Transplanted wholesale from islah (the strongest keeper organ — a real, fiddly,
working ASC integration: ES256 JWT auth, ``xcsym`` symbolication, dedup). The
only structural change is the SINK: TestFlight beta feedback (tester screenshots
+ comments) and beta crash submissions now become ``pipeline='bug'`` tasks on the
work board (via ``bug_tasks.file_bug``) instead of ``bugs.yaml`` appends.

Config-driven on entry (discharges islah's aos#164 hardcode debt):
  * app registry  → ``apps_registry`` (config/apps.yaml + the instance override),
    NOT a hardcoded ``APPS`` map or an AOS-X yaml path.
  * ASC credentials → Keychain via ``agent-secret`` (ASC_ISSUER_ID / ASC_KEY_ID)
    + the ``AuthKey_<KEYID>.p8`` private key. No creds in the framework tree.

Idempotent: every submission carries a stable ``source_ref``
(``testflight:<id>`` / ``asc-crash:<id>``); ``file_bug`` skips anything already
filed, so this is safe to poll hourly. Runs from the framework via the
``ascbuild-sync`` cron.

CLI:
  python3 -m core.engine.work.intake.ascbuild sync            # file into work.db
  python3 -m core.engine.work.intake.ascbuild sync --dry-run  # show, file nothing
  python3 -m core.engine.work.intake.ascbuild sync --app <id> # scope to one app
  python3 -m core.engine.work.intake.ascbuild builds          # latest build per app
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

try:
    import jwt  # PyJWT
    import requests
except ImportError as e:  # pragma: no cover - hard deps, defensive only
    sys.stderr.write(f"missing dependency: {e} (need PyJWT + requests + cryptography)\n")
    raise

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "core" / "engine" / "work")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.engine.work.apps_registry import AppEntry, load_apps  # noqa: E402
from core.engine.work.intake.bug_tasks import already_filed, file_bug  # noqa: E402

# --- constants ---------------------------------------------------------------
ASC_BASE = "https://api.appstoreconnect.apple.com"
# Where downloaded screenshots / crash logs land (instance data, not committed).
MEDIA_DIR = Path(os.path.expanduser("~/.aos/data/bug-intake/media"))
# xcsym ships with the Axiom plugin; overridable for portability.
XCSYM = os.environ.get("AOS_XCSYM") or os.path.expanduser(
    "~/.claude/plugins/cache/axiom-marketplace/axiom/3.4.0/bin/xcsym"
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _agent_secret_bin() -> str | None:
    found = shutil.which("agent-secret")
    if found:
        return found
    for c in ("~/aos/core/bin/cli/agent-secret", "~/aos/core/bin/agent-secret"):
        p = os.path.expanduser(c)
        if os.path.isfile(p):
            return p
    return None


def _secret(key: str) -> str:
    binp = _agent_secret_bin()
    if not binp:
        return ""
    try:
        r = subprocess.run([binp, "get", key], capture_output=True, text=True, timeout=15)
        return r.stdout.strip()
    except Exception:
        return ""


# --- auth --------------------------------------------------------------------
class AuthError(RuntimeError):
    pass


def _find_p8(key_id: str) -> str | None:
    names = [f"AuthKey_{key_id}.p8", f"{key_id}.p8"] if key_id else []
    dirs = ["~/.appstoreconnect/private_keys", "~/.private_keys",
            "~/.aos/config/keys", "~/.aos"]
    for d in dirs:
        dp = Path(os.path.expanduser(d))
        for n in names:
            p = dp / n
            if p.is_file():
                return str(p)
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


# --- app registry (config-driven) --------------------------------------------
def _norm(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum())


def resolve_apps(asc: ASC, apps: dict[str, AppEntry] | None = None,
                 only: str | None = None) -> dict:
    """Map registry app id -> {app_id, name, bundle_id, entry}. iOS apps only.

    Resolution order per app: explicit ``asc_app_id`` → ``bundle_id`` →
    fuzzy display-name match. ``platform='web'`` apps are skipped (no TestFlight).
    """
    apps = load_apps() if apps is None else apps
    asc_apps = asc.paged("/v1/apps", max_items=200, **{"fields[apps]": "name,bundleId"})
    by_bundle = {a["attributes"].get("bundleId"): a for a in asc_apps}
    resolved: dict[str, dict] = {}
    for app_id, entry in apps.items():
        if only and app_id != only:
            continue
        if entry.platform == "web":
            continue
        match = None
        if entry.asc_app_id:
            match = next((a for a in asc_apps if a["id"] == str(entry.asc_app_id)), None)
        if not match and entry.bundle_id:
            match = by_bundle.get(entry.bundle_id)
        if not match:
            disp = _norm(entry.name or app_id)
            for a in asc_apps:
                nm = _norm(a["attributes"].get("name"))
                if disp and (disp in nm or nm in disp):
                    match = a
                    break
        if match:
            resolved[app_id] = {
                "app_id": match["id"],
                "name": match["attributes"].get("name"),
                "bundle_id": match["attributes"].get("bundleId"),
                "entry": entry,
            }
    return resolved


# --- build status ------------------------------------------------------------
def asc_build_status(asc: ASC, app_id: str) -> dict | None:
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
    for f in frames:
        if f.get("image") == app_name and f.get("file") \
                and "compiler-generated" not in f.get("file", ""):
            return f
    for f in frames:
        if f.get("image") == app_name and f.get("symbol"):
            return f
    return None


def symbolicate(crash_path: Path) -> dict:
    """Run xcsym; return a compact symbolication summary."""
    if not os.path.isfile(XCSYM):
        return {"ok": False, "error": f"xcsym not found at {XCSYM}"}
    try:
        r = subprocess.run([XCSYM, "crash", "-format", "summary", str(crash_path)],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return {"ok": False, "error": f"xcsym failed: {e}"}
    # xcsym uses non-zero exit codes for symbolication completeness, NOT hard
    # failure — parse stdout regardless; only unparseable output is an error.
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
    if v and "(" in v and ")" in v:
        return v[v.rfind("(") + 1:v.rfind(")")].strip()
    return None


# --- intake ------------------------------------------------------------------
def _clip(s: str, n: int = 80) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _resolve_screenshot_urls(attrs: dict) -> list[tuple[str, str]]:
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


def sync_screenshots(asc: ASC, app_id: str, app: dict, engine, dry: bool) -> list:
    """File TestFlight screenshot feedback as bug tasks (classification=ux)."""
    filed = []
    subs = asc.paged(f"/v1/apps/{app['app_id']}/betaFeedbackScreenshotSubmissions",
                     max_items=200)
    for sub in subs:
        sid = sub["id"]
        ref = f"testflight:{sid}"
        if already_filed(engine, ref):
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
            name = f"{app_id}-tf-{sid}-{i}{ext}"
            dest = MEDIA_DIR / name
            if not dry and asc.download(url, dest):
                attachments.append(name)
                proof.append({"kind": "proof", "body": f"Screenshot: {name}",
                              "data": {"kind": "screenshot", "ref": name},
                              "ts": a.get("createdDate"), "marker": f"shot:{i}"})
        if not dry:
            fields = {"app": app_id, "classification": "ux", "severity": 3,
                      "symptom": comment or "(no comment)", "reporter": reporter,
                      "device": a.get("deviceModel"), "os": a.get("osVersion")}
            beats = [{"kind": "comment",
                      "body": _clip(comment) if comment else "Screenshot feedback (no comment)",
                      "data": {"comment": comment, "device": a.get("deviceModel"),
                               "os": a.get("osVersion"), "attachments": attachments},
                      "ts": a.get("createdDate"), "actor": "ascbuild",
                      "marker": "feedback"}] + proof
            file_bug(engine, title=title, app=app_id, source="testflight",
                     source_ref=ref, fields=fields, stage="new",
                     reported_ts=a.get("createdDate"), activities=beats,
                     created_body=f"TestFlight feedback: {title}",
                     created_data={"reporter": reporter}, actor="ascbuild")
        filed.append((ref, title))
    return filed


def sync_crashes(asc: ASC, app_id: str, app: dict, engine, dry: bool) -> list:
    """File TestFlight beta crashes (symbolicated) as bug tasks (severity=1)."""
    filed = []
    RECENT_WINDOW = 0  # current build only — older builds are presumed fixed
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
        if already_filed(engine, ref):
            continue
        a = sub.get("attributes", {})
        comment = (a.get("comment") or "").strip()

        sym, crash_file = {}, None
        try:
            cl = asc.get(f"/v1/betaFeedbackCrashSubmissions/{sid}/crashLog")
            log_text = ((cl.get("data") or {}).get("attributes") or {}).get("logText")
        except Exception:
            log_text = None
        crash_name = f"{app_id}-crash-{sid}.crash"
        if log_text:
            crash_file = MEDIA_DIR / crash_name
            if not dry:
                crash_file.parent.mkdir(parents=True, exist_ok=True)
                crash_file.write_text(log_text)
                sym = symbolicate(crash_file)
            else:
                tmp = MEDIA_DIR.parent / f".dry_{sid}.crash"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(log_text)
                sym = symbolicate(tmp)
                tmp.unlink(missing_ok=True)

        exc = sym.get("exception") or "crash"
        pattern = sym.get("pattern") or ""
        top = sym.get("top_frame")
        build = _build_from_version(sym.get("app_version"))
        try:
            _cur = int(str(build)) if build else None
        except (ValueError, TypeError):
            _cur = None
        if latest_build is not None and (_cur is None or _cur < latest_build - RECENT_WINDOW):
            continue

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

        if not dry:
            fields = {"app": app_id, "classification": "crash", "severity": 1,
                      "symptom": symptom, "root_cause": root_cause, "build": build,
                      "reporter": a.get("email") or "tester",
                      "device": a.get("deviceModel"), "os": a.get("osVersion")}
            beats = [{"kind": "comment", "body": _clip(symptom, 120),
                      "data": {"exception": exc, "pattern": pattern,
                               "top_frame": top, "comment": comment,
                               "crashlog": crash_name if crash_file else None,
                               "symbolicated": sym.get("symbolicated")},
                      "ts": a.get("createdDate"), "actor": "ascbuild",
                      "marker": "crash"}]
            if crash_file:
                beats.append({"kind": "proof", "body": f"Crash log: {crash_name}",
                              "data": {"kind": "crashlog", "ref": crash_name},
                              "ts": a.get("createdDate"), "actor": "ascbuild",
                              "marker": "crashlog"})
            file_bug(engine, title=title, app=app_id, source="asc-crash",
                     source_ref=ref, fields=fields, stage="new",
                     reported_ts=a.get("createdDate"), activities=beats,
                     created_body=f"Beta crash: {title}",
                     created_data={"reporter": a.get("email") or "tester"},
                     actor="ascbuild")
        filed.append((ref, title))
    return filed


# --- orchestration -----------------------------------------------------------
def _bind_engine(engine):
    if engine is not None:
        return engine
    os.environ.setdefault(
        "AOS_WORK_DB", str(Path.home() / ".aos" / "data" / "work.db"))
    import backend as engine  # the work backend, on sys.path
    return engine


def sync(only: str | None = None, dry: bool = False, engine=None) -> dict:
    creds = load_credentials()
    asc = ASC(creds)
    apps = resolve_apps(asc, only=only)
    if not apps:
        return {"error": "no iOS apps resolved from registry", "apps": {}}
    engine = _bind_engine(engine)
    report = {"apps": {}}
    for app_id, app in apps.items():
        shots = sync_screenshots(asc, app_id, app, engine, dry)
        crashes = sync_crashes(asc, app_id, app, engine, dry)
        latest = asc_build_status(asc, app["app_id"])
        report["apps"][app_id] = {
            "app_id": app["app_id"], "name": app["name"],
            "screenshots_filed": shots, "crashes_filed": crashes,
            "latest_build": latest,
        }
    return report


def builds(only: str | None = None) -> dict:
    creds = load_credentials()
    asc = ASC(creds)
    apps = resolve_apps(asc, only=only)
    out = {}
    for app_id, app in apps.items():
        out[app_id] = {"name": app["name"], "app_id": app["app_id"],
                       "status": asc_build_status(asc, app["app_id"])}
    return out


# --- CLI ---------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(prog="ascbuild")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="pull TestFlight feedback + crashes into work.db")
    s.add_argument("--app", help="restrict to one registry app id")
    s.add_argument("--dry-run", action="store_true")
    b = sub.add_parser("builds", help="show latest build per app")
    b.add_argument("--app")
    a = ap.parse_args()

    try:
        if a.cmd == "sync":
            rep = sync(only=a.app, dry=a.dry_run)
            if rep.get("error"):
                print("⚠", rep["error"])
                sys.exit(1)
            tag = "would file" if a.dry_run else "filed"
            total = 0
            for app_id, r in rep["apps"].items():
                lb = r["latest_build"]
                lb_str = f"{lb.get('version')} ({lb.get('build')}) — {lb.get('processingState')}" \
                    if lb else "(no builds)"
                print(f"\n{app_id} · {r['name']}  [{r['app_id']}]")
                print(f"  latest TestFlight build: {lb_str}")
                for _ref, title in r["screenshots_filed"]:
                    print(f"  + [ux]    {title}  ({tag})")
                for _ref, title in r["crashes_filed"]:
                    print(f"  + [crash] {title}  ({tag})")
                n = len(r["screenshots_filed"]) + len(r["crashes_filed"])
                total += n
                if n == 0:
                    print("  (nothing new)")
            print(f"\n{total} item(s) {tag}.")
        elif a.cmd == "builds":
            for app_id, r in builds(only=a.app).items():
                st = r["status"]
                if st:
                    print(f"{app_id:16} {r['name']:32} v{st['version']} "
                          f"build {st['build']}  {st['processingState']}")
                else:
                    print(f"{app_id:16} {r['name']:32} (no builds)")
    except AuthError as e:
        print("⚠ ASC auth unavailable —", e)
        print("  Provide: ASC_ISSUER_ID + ASC_KEY_ID (keychain via agent-secret) "
              "and AuthKey_<KEYID>.p8 in ~/.appstoreconnect/private_keys/")
        sys.exit(2)


if __name__ == "__main__":
    main()
