"""Human copy for reconcile alerts (aos#170 — Telegram message quality).

Reconcile checks speak in slugs, paths, and CI jargon — good for logs, wrong
for a phone. This module is the single place that translates a check finding
into the plain-English one-liner the operator actually reads on Telegram.

The rest of the runner keeps its raw `CheckResult.message`/`detail` (those go
to the JSONL log and `aos reconcile` terminal output untouched). Only the
Telegram path routes through here.

Anatomy of an alert (see core/services/bridge/MESSAGE_STYLE.md → "System
alerts"): one emoji + one line of what happened in human terms + one line of
what I did or what you should do + an optional "details in the log" tail.
Counts are fine; slugs, paths, and jargon are not.

Adding a check? Add a template below. If you forget, the fallback still strips
paths/slugs/version-and-migration refs so a raw message never lands verbatim.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Jargon stripping — the safety net for any finding without a template.
# --------------------------------------------------------------------------

# Order matters: paths and known refs go before the generic slug pass, so we
# don't half-spacify a filename first.
_PATH_RE = re.compile(r"~?/[\w./\-]+")
_FILE_RE = re.compile(r"\b[\w.\-]+\.(?:py|yaml|yml|json|toml|log|md|plist|sql|sh|cfg|ini)\b")
_VERSION_RE = re.compile(r"\bv\d+\.\d+\.\d+\S*")
_MIGRATION_RE = re.compile(r"\b[Mm]igration\s+\d+\b")
_COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_TASKID_RE = re.compile(r"\b[a-z][a-z0-9]*#\d+(?:\.\d+)?\b")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_HTML_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_SLUG_RE = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b")
_WS_RE = re.compile(r"[ \t]{2,}")


def strip_jargon(text: str) -> str:
    """Best-effort scrub of the machine-speak a raw finding can carry.

    Removes file paths, filenames, version/commit/migration/task refs, bracketed
    codes and HTML tags, and turns snake_case slugs into spaced words. It will
    never be as good as a hand-written template — it exists so an untemplated
    finding still reads like English instead of a stack trace.
    """
    if not text:
        return ""
    out = _HTML_RE.sub("", text)
    out = _PATH_RE.sub("", out)
    out = _FILE_RE.sub("", out)
    out = _VERSION_RE.sub("", out)
    out = _MIGRATION_RE.sub("", out)
    out = _TASKID_RE.sub("", out)
    out = _COMMIT_RE.sub("", out)
    out = _BRACKET_RE.sub("", out)
    out = _SLUG_RE.sub(lambda m: m.group(0).replace("_", " "), out)
    # Tidy the debris left behind by removals.
    out = out.replace("()", "").replace("( )", "")
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = _WS_RE.sub(" ", out)
    out = re.sub(r"\s+", " ", out)
    return out.strip(" -—:;,").strip()


# --------------------------------------------------------------------------
# Small extractors used by the dynamic templates.
# --------------------------------------------------------------------------

def _first_int(text: str) -> int | None:
    m = re.search(r"\d[\d,]*", text or "")
    return int(m.group(0).replace(",", "")) if m else None


def _size(text: str) -> str | None:
    """Pull a human size like '1.2GB' or '340 KB' out of a message."""
    m = re.search(r"[\d.]+\s?[KMGT]?B", text or "")
    return m.group(0).strip() if m else None


def _plural(n: int | None, singular: str, plural: str | None = None) -> str:
    plural = plural or (singular + "s")
    return singular if n == 1 else plural


def _friendly_host(host: str) -> str:
    return (host or "this Mac").replace(".local", "")


# --------------------------------------------------------------------------
# Friendly short labels — for the "cleared" line and the last-resort subject.
# --------------------------------------------------------------------------

LABELS: dict[str, str] = {
    "volume_access": "the external drive",
    "dead_code": "leftover scripts",
    "disk_smart_health": "disk health",
    "storage_layout": "storage layout",
    "instance_hygiene": "leftover files",
    "vault_contract": "vault notes",
    "google_workspace": "Google Workspace",
    "service_loaded": "a background service",
    "transcriber_service": "the transcriber",
    "n8n_service": "the automation service",
    "bridge_poll_liveness": "the Telegram bridge",
    "deployment_health": "the last update",
    "dev_backend_plist": "the Qareen dev service",
    "dev_browser": "the automation browser",
    "launchagent_python_paths": "background services",
    "settings_config": "Claude Code settings",
    "bridge_topics_config": "Telegram topics",
    "runtime_protection": "the live system",
    "disk_smart": "disk health",
}


def _label(name: str) -> str:
    return LABELS.get(name) or strip_jargon(name) or "a self-check"


# --------------------------------------------------------------------------
# Per-check templates. A value is either a plain string or a callable that
# receives (message, detail) and returns the human line(s). Keyed by check
# name, then by a status bucket: "notify" (needs you / couldn't auto-fix),
# "fixed" (I sorted it), "error" (the check itself crashed).
# --------------------------------------------------------------------------

def _dead_code(message: str, detail: str | None) -> str:
    n = _first_int(detail or "") or _first_int(message or "")
    if n:
        return (f"🧹 Found {n} old {_plural(n, 'script')} nobody uses anymore. "
                "Nothing urgent — I'll list them for cleanup whenever you're ready.")
    return ("🧹 Spotted some leftover code that's no longer used. "
            "Nothing urgent — I'll list it for cleanup whenever you're ready.")


def _storage_layout(message: str, detail: str | None) -> str:
    n = _first_int(message or "")
    size = _size(message or "")
    where = f" ({size})" if size else ""
    subj = f"{n} {_plural(n, 'folder')}" if n else "Some folders"
    return (f"📦 {subj} that belong on the external drive are on the internal disk"
            f"{where}. I'll move them on the next cleanup pass.")


def _instance_hygiene(message: str, detail: str | None) -> str:
    size = _size(message or "")
    tail = f" ({size})" if size else ""
    return (f"🧹 Found some leftover files from old runs{tail}. "
            "Harmless — I'll clear them out whenever you're ready.")


def _deployment_health_notify(message: str, detail: str | None) -> str:
    n = _first_int(message or "")
    subj = f"{n} {_plural(n, 'part')}" if n else "A few parts"
    return (f"🚧 {subj} of the last update didn't finish installing. "
            "I've logged exactly what's missing.")


def _deployment_health_fixed(message: str, detail: str | None) -> str:
    n = _first_int(message or "")
    subj = f"{n} leftover {_plural(n, 'piece')}" if n else "some leftover pieces"
    return f"✅ Finished installing {subj} from the last update."


def _google_workspace(message: str, detail: str | None) -> str:
    m = (message or "").lower()
    if "not installed" in m:
        why = "the Google command-line tool isn't installed yet"
    elif "keychain" in m or "secret" in m:
        why = "some Google credentials are missing"
    elif "credential" in m or "no google" in m:
        why = "it's not signed in yet"
    else:
        why = "it isn't fully set up"
    return (f"🔌 Google Workspace isn't ready — {why}. "
            "Gmail and Calendar features stay off until it's connected.")


def _service_loaded(message: str, detail: str | None) -> str:
    """service_loaded packs several outcomes into one ';'-joined message.
    Pull out the service names (before the '(reason)') and pick the tone."""
    msg = message or ""

    def _names(after: str) -> str:
        # e.g. "restarted transcriber (not loaded), n8n (health)"
        seg = ""
        for part in msg.split(";"):
            if after in part:
                seg = part.split(after, 1)[1]
                break
        names = re.findall(r"([a-zA-Z][\w-]*)\s*\(", seg)
        names = [n.replace("_", " ") for n in names]
        return ", ".join(names)

    if "FAILED to reload" in msg:
        who = _names("FAILED to reload:") or "a background service"
        return (f"🔧 Tried to restart {who} but it didn't come back up. "
                "Worth a look when you're at the Mac — the log has the detail.")
    if "RETIRED but still loaded" in msg:
        who = _names("RETIRED but still loaded") or "a background service"
        return (f"🧹 {who} is retired but still running. "
                "You can unload it whenever convenient — no rush.")
    if msg.startswith("restarted") or "restarted " in msg:
        who = _names("restarted") or "a background service"
        return f"🔧 Restarted {who} — back up and healthy."
    # All skipped for cooldown, or nothing actionable.
    return ("🔧 A background service needed a nudge but is still settling. "
            "I'll recheck it shortly.")


def _transcriber_notify(message: str, detail: str | None) -> str:
    return ("🔧 The transcriber (voice-note text) needs attention and I couldn't "
            "bring it back automatically. I've logged the details.")


def _n8n_notify(message: str, detail: str | None) -> str:
    return ("🔧 The automation service (n8n) needs attention and I couldn't bring "
            "it back automatically. I've logged the details.")


TEMPLATES: dict[str, dict[str, object]] = {
    "volume_access": {
        "notify": (
            "⚠️ I can't reliably read the external drive right now. This usually "
            "means a Mac permission got reset after an app update. Two-minute fix: "
            "System Settings → Privacy & Security → Files and Folders → allow your "
            "terminal, then relaunch it. Until then I'll treat anything from the "
            "vault or projects as unreliable, not empty."
        ),
    },
    "dead_code": {"notify": _dead_code},
    "storage_layout": {"notify": _storage_layout},
    "instance_hygiene": {"notify": _instance_hygiene},
    "vault_contract": {
        "notify": ("📝 A batch of vault notes are missing their frontmatter. "
                   "Not urgent — worth tidying so search stays sharp."),
        "error": ("📝 I couldn't finish checking the vault notes. "
                  "Nothing broke, but it's worth a glance — details in the log."),
    },
    "disk_smart_health": {
        "notify": ("🚨 One of the drives is reporting hardware health warnings. "
                   "Worth backing up soon and keeping an eye on it — details in the log."),
    },
    "google_workspace": {"notify": _google_workspace},
    "deployment_health": {
        "notify": _deployment_health_notify,
        "fixed": _deployment_health_fixed,
    },
    "dev_backend_plist": {
        "notify": ("🔧 The Qareen dev service isn't loaded, so the dashboard and dev "
                   "backend may be offline. Logged for a look."),
    },
    "dev_browser": {
        "notify": ("🌐 The automation browser isn't set up correctly, so some web "
                   "tasks may not run. Details in the log."),
    },
    "launchagent_python_paths": {
        "notify": ("🔧 A few background services point at a Python that moved and I "
                   "couldn't fix it automatically. They may not start until it's sorted."),
        "fixed": "🔧 Pointed a few background services back at the right Python.",
    },
    "settings_config": {
        "notify": ("⚙️ My Claude Code settings drifted from the expected setup and I "
                   "couldn't fix it automatically. Logged for a look."),
        "fixed": "⚙️ Tidied up a couple of Claude Code settings that had drifted.",
    },
    "bridge_topics_config": {
        "notify": ("💬 Telegram topics aren't configured yet. Set up the Telegram "
                   "group first and I'll route messages into the right threads."),
        "error": ("💬 I couldn't set up the Telegram topics config. "
                  "Details in the log."),
    },
    "bridge_poll_liveness": {
        "notify": ("📡 The Telegram bridge stopped checking for new messages and I "
                   "couldn't restart it automatically. Worth a look when you're at the Mac."),
        "fixed": ("📡 The Telegram bridge had stalled — I restarted it and messages "
                  "are flowing again."),
    },
    "runtime_protection": {
        "fixed": ("🛡️ The live system had been edited directly — I reset it back to "
                  "the shipped version. (Changes belong in the dev workspace.)"),
    },
    "service_loaded": {"fixed": _service_loaded, "notify": _service_loaded},
    "transcriber_service": {
        "notify": _transcriber_notify,
        "fixed": "🔧 The transcriber was down — I redeployed and restarted it. Healthy again.",
    },
    "n8n_service": {
        "notify": _n8n_notify,
        "fixed": "🔧 The automation service (n8n) was down — I restarted it. Healthy again.",
    },
    "initiative_directories": {
        "error": ("📁 I couldn't create some initiative folders in the vault. "
                  "Usually means the drive wasn't mounted — details in the log."),
    },
}


# --------------------------------------------------------------------------
# Public API.
# --------------------------------------------------------------------------

def humanize_finding(name: str, status: str, message: str,
                     detail: str | None = None) -> str:
    """Translate one reconcile finding into a human, emoji-led Telegram line.

    `status` is the CheckResult.status value ("notify", "fixed", "error", ...).
    Falls back to a jargon-stripped rendering when no template matches, so a
    raw message never reaches the operator verbatim.
    """
    bucket = "fixed" if status == "fixed" else ("error" if status == "error" else "notify")
    tmpl = TEMPLATES.get(name, {}).get(bucket)
    if tmpl is None and bucket == "error":
        # A crashed check — generic but honest, never the traceback.
        return (f"❗ One of my self-checks ({_label(name)}) hit a snag and couldn't "
                "finish. Nothing broke, but it's worth a glance — details in the log.")
    if callable(tmpl):
        return tmpl(message, detail).strip()
    if isinstance(tmpl, str):
        return tmpl
    # No template: scrub the raw message so it at least reads like English.
    cleaned = strip_jargon(message) or _label(name)
    emoji = "🔧" if bucket == "fixed" else "⚠️"
    return f"{emoji} {cleaned[0].upper()}{cleaned[1:]}." if cleaned else f"{emoji} {_label(name)} needs a look."


def cleared_line(names: list[str]) -> str | None:
    """A warm 'back to normal' line for findings that resolved since last run."""
    if not names:
        return None
    labels = [_label(n) for n in names]
    if len(labels) == 1:
        return f"✅ Good news — {labels[0]} is back to normal."
    joined = ", ".join(labels[:-1]) + f" and {labels[-1]}"
    return f"✅ Good news — {joined} are back to normal."


def render_report(findings: list[tuple[str, str, str, str | None]],
                  cleared: list[str], host: str) -> str | None:
    """Assemble the full Telegram alert from findings and cleared names.

    `findings`: list of (name, status, message, detail).
    Returns None when there's nothing worth sending.
    """
    blocks = [humanize_finding(n, s, m, d) for (n, s, m, d) in findings]
    cl = cleared_line(cleared)

    parts: list[str] = []
    if blocks:
        if len(blocks) > 1:
            parts.append(f"🛠️ A few housekeeping notes from {_friendly_host(host)}:")
        parts.extend(blocks)
    if cl:
        parts.append(cl)

    return "\n\n".join(parts) if parts else None
