"""Human copy for everything the machine sends the operator (aos#170, aos#235).

Reconcile checks speak in slugs, paths, and CI jargon — good for logs, wrong
for a phone. This module is the single place that translates a check finding
into the plain-English one-liner the operator actually reads on Telegram.

Since aos#235 it is the single place for the *other* machine-written senders
too. The 2026-09-13 bridge review graded the eleven live outbound generators
and found four that never came through here: STEER job reports (raw `job_id`,
raw `stderr[:200]`, raw internal "latest update" strings in `<code>`),
tool-status pings (an arbitrary, unscrubbed description), every non-reconcile
`aos-notify` caller (one emoji prefix and nothing else), and `channel-update`.
`humanize_job_report`, `humanize_tool_status` and `humanize_notice` close those
gaps; `core/engine/notify/router.py` calls the last one on the way out, so a
sender cannot forget.

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
    "bridge_poll_liveness": "the Telegram bridge",
    "deployment_health": "the last update",
    "cmux_socket_control": "your terminal",
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
    "cmux_socket_control": {
        "notify": ("🖥️ I can't open your terminal for you right now, so starting a "
                   "session will drop you in the window you're already in. "
                   "Logged for a look."),
        "fixed": "🖥️ Fixed your terminal — sessions will open in their own window again.",
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


# --------------------------------------------------------------------------
# The other senders (aos#235). Same contract as humanize_finding: the raw
# string goes to the log, a human line goes to the phone.
# --------------------------------------------------------------------------

MAX_ITEMS = 4  # MESSAGE_STYLE.md: four items max, a phone screen fills up fast.

_TB_START_RE = re.compile(r"^\s*Traceback \(most recent call last\)")
_TB_FRAME_RE = re.compile(r'^\s*(?:File [\"~/\'].*|at \S+.*|\.\.\. \d+ more)\s*$')
_TB_EXC_RE = re.compile(r"^\s*[A-Za-z_][\w.]*(?:Error|Exception|Warning)\b.*$")
_LINE_WS_RE = re.compile(r"[ \t]{2,}")


def strip_traceback(text: str | None) -> str:
    """Drop stack-trace machinery, keep any human sentence around it.

    A traceback is the single most common way a raw internal string reaches the
    operator's phone, and it is never the thing they need to read — the log has
    it in full.
    """
    if not text:
        return ""
    kept: list[str] = []
    in_tb = False
    for line in text.splitlines():
        if _TB_START_RE.match(line):
            in_tb = True
            continue
        if in_tb:
            if not line.strip():
                in_tb = False
                continue
            if line.startswith((" ", "\t")) or _TB_EXC_RE.match(line):
                continue
            in_tb = False
        if _TB_FRAME_RE.match(line) or _TB_EXC_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def scrub_lines(text: str | None, keep_html: bool = True) -> str:
    """`strip_jargon`, but per line, so a multi-line message keeps its shape.

    `strip_jargon` collapses all whitespace — right for a one-line finding,
    wrong for a digest, which would arrive as one long paragraph. HTML is kept
    by default: Telegram senders use `<b>` deliberately.
    """
    if not text:
        return ""
    out_lines = []
    for line in strip_traceback(text).splitlines():
        if not line.strip():
            out_lines.append("")
            continue
        if keep_html:
            # Mask tags before scrubbing: `</b>` contains `/b`, which the path
            # pattern would eat, leaving `<>` behind in the operator's message.
            tags: list[str] = []

            def _mask(m, _tags=tags):
                _tags.append(m.group(0))
                return f"\x00{len(_tags) - 1}\x00"

            cleaned = _HTML_RE.sub(_mask, line)
        else:
            tags = []
            cleaned = _HTML_RE.sub("", line)
        cleaned = _PATH_RE.sub("", cleaned)
        cleaned = _FILE_RE.sub("", cleaned)
        cleaned = _VERSION_RE.sub("", cleaned)
        cleaned = _MIGRATION_RE.sub("", cleaned)
        cleaned = _TASKID_RE.sub("", cleaned)
        cleaned = _COMMIT_RE.sub("", cleaned)
        cleaned = _SLUG_RE.sub(lambda m: m.group(0).replace("_", " "), cleaned)
        cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
        cleaned = _LINE_WS_RE.sub(" ", cleaned)
        for i, tag in enumerate(tags):
            cleaned = cleaned.replace(f"\x00{i}\x00", tag)
        out_lines.append(cleaned.rstrip())
    # Collapse the runs of blank lines that removals can leave behind, but keep
    # single blank lines: they are the paragraph breaks the reader scans by.
    result: list[str] = []
    for line in out_lines:
        if not line and result and not result[-1]:
            continue
        result.append(line)
    return "\n".join(result).strip()


def cap_items(text: str | None, max_items: int = MAX_ITEMS) -> str:
    """Keep at most `max_items` lines, then say how many were left out.

    The count is the useful part of a long list; the list itself belongs in the
    log. Blank lines do not count as items.
    """
    if not text:
        return ""
    lines = text.splitlines()
    items = [ln for ln in lines if ln.strip()]
    if len(items) <= max_items:
        return text
    kept: list[str] = []
    seen = 0
    for line in lines:
        if line.strip():
            if seen == max_items:
                break
            seen += 1
        kept.append(line)
    left = len(items) - max_items
    kept.append(f"…and {left} more. Details are in the log.")
    return "\n".join(kept).strip()


# ── STEER job reports ──────────────────────────────────────────────────────

_JOB_COPY = {
    "dispatch_failed": "😕 Couldn't start that job — I'll try again in a moment.",
    "started": "🔄 On it.",
    "working": "🔄 Still working on it.",
    "failed": "😕 That job didn't finish. Details are in the log.",
    "timeout": ("⏰ That job is taking much longer than it should, so I've stopped "
                "waiting on it. Details are in the log."),
    "error": "😕 Something went wrong starting that job. Details are in the log.",
}


def humanize_job_report(stage: str, detail: str | None = None) -> str:
    """Copy for a dispatched job, by stage.

    `detail` is accepted and deliberately *not* interpolated for any failure
    stage — stderr, job ids and internal step strings are what the log is for.
    The one stage that uses it is "done", where the summary is the whole point;
    it is scrubbed and capped like anything else.
    """
    if stage == "done":
        summary = cap_items(scrub_lines(detail, keep_html=False))
        return f"✅ Done.\n\n{summary}" if summary else "✅ Done."
    return _JOB_COPY.get(stage, _JOB_COPY["working"])


# ── Tool-status pings ──────────────────────────────────────────────────────

_TOOL_STATUS_LIMIT = 120


def humanize_tool_status(description: str | None) -> str:
    """One short line for the "what am I doing right now" ping.

    The description arrives from the streaming renderer as an arbitrary string
    — a tool name, a file path, sometimes an error. It is shown to the operator
    mid-conversation, so it gets scrubbed like any other outbound copy, and
    always says something rather than rendering an empty `<i></i>`.
    """
    cleaned = strip_jargon(strip_traceback(description or ""))
    if not cleaned:
        return "Working on it…"
    if len(cleaned) > _TOOL_STATUS_LIMIT:
        cleaned = cleaned[: _TOOL_STATUS_LIMIT - 1].rstrip() + "…"
    return cleaned


# ── Generic notices (aos-notify, crons, bus consumers) ─────────────────────

_NOTICE_FALLBACK = "Something needs a look on my end. Details are in the log."


def humanize_notice(text: str | None, kind: str = "info") -> str:
    """Last stop for any sender that writes its own copy.

    Scrubs tracebacks, paths, filenames and version/commit/task refs from every
    notice. Item-capping applies to alerts only: an alert that is a long list is
    a raw dump, while a digest is a list on purpose, and truncating the weekly
    summary to four lines would be a regression dressed as a style fix.

    Idempotent by construction — copy that is already clean passes through
    unchanged, so reconcile humanizing before it sends costs nothing here.
    """
    if not text:
        return ""
    out = scrub_lines(text, keep_html=True)
    if kind == "alert":
        out = cap_items(out)
    return out or _NOTICE_FALLBACK
