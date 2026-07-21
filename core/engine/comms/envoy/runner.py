"""Envoy runner — one poll cycle over all active conversations.

Called by the com.aos.envoy LaunchAgent (or `envoy run-once`). For each
active conversation: detect new inbound in comms.db, run a headless Claude
turn, act on the structured decision. Exits instantly when idle.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
COMMS_DB = HOME / ".aos" / "data" / "comms.db"
LOG_DIR = HOME / ".aos" / "logs" / "envoy"
AGENT_SECRET = str(HOME / "aos" / "core" / "bin" / "cli" / "agent-secret")
CLAUDE_BIN = (shutil.which("claude")
              or next((p for p in ("/opt/homebrew/bin/claude",
                                   str(HOME / ".claude" / "local" / "claude"),
                                   "/usr/local/bin/claude")
                       if Path(p).exists()), "claude"))
TURN_TIMEOUT = 180

log = logging.getLogger("envoy")


def _ensure_path():
    for p in (HOME / "aos", HOME / "project" / "aos"):
        if (p / "core" / "engine" / "comms" / "envoy").is_dir():
            sys.path.insert(0, str(p))
            return


_ensure_path()
from core.engine.comms.envoy import prompts, store  # noqa: E402

# ── Side effects ─────────────────────────────────────────────────────


def send_imessage(recipient: str, text: str) -> bool:
    """Same AppleScript path as the comms iMessage adapter."""
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
    safe_rcpt = recipient.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
        tell application "Messages"
            set targetService to 1st account whose service type = iMessage
            set targetBuddy to participant "{safe_rcpt}" of targetService
            send "{safe_text}" to targetBuddy
        end tell
    '''
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        log.error("imessage send failed: %s", r.stderr.strip()[:200])
    return r.returncode == 0


def _secret(name: str) -> str:
    try:
        return subprocess.run([AGENT_SECRET, "get", name], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return ""


def telegram(text: str) -> bool:
    token, chat = _secret("TELEGRAM_BOT_TOKEN"), _secret("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    r = subprocess.run(
        ["curl", "-s", "-m", "15", "-X", "POST",
         f"https://api.telegram.org/bot{token}/sendMessage",
         "-d", f"chat_id={chat}", "--data-urlencode", f"text={text}"],
        capture_output=True, text=True)
    return '"ok":true' in r.stdout


def operator_name() -> str:
    try:
        import yaml
        cfg = yaml.safe_load((HOME / ".aos" / "config" / "operator.yaml").read_text())
        return cfg.get("name", "the operator").split()[0]
    except Exception:
        return "the operator"


# ── Inbound detection ────────────────────────────────────────────────


def new_inbound(contact: str, since_ts: str) -> list[tuple[str, str]]:
    """New inbound (timestamp, content) from contact since since_ts."""
    digits = "".join(ch for ch in contact if ch.isdigit())
    needle = f"%{digits[-10:]}%" if len(digits) >= 10 else f"%{contact}%"
    conn = sqlite3.connect(str(COMMS_DB))
    try:
        rows = conn.execute(
            "SELECT timestamp, content FROM messages "
            "WHERE channel IN ('imessage','sms','rcs') AND direction='inbound' "
            "AND sender_id LIKE ? AND timestamp > ? ORDER BY timestamp",
            (needle, since_ts)).fetchall()
    finally:
        conn.close()
    return [(ts, c) for ts, c in rows if c and c.strip()]


# ── Turn execution ───────────────────────────────────────────────────


def run_turn(conv: store.Conversation, kickoff: bool) -> dict | None:
    prompt = prompts.build_turn_prompt(
        conv.mission, conv.transcript(), operator_name(), kickoff)
    try:
        proc = subprocess.run(
            [CLAUDE_BIN, "--print", "--model", "sonnet",
             "--dangerously-skip-permissions", "--allowedTools", ""],
            input=prompt, capture_output=True, text=True,
            timeout=TURN_TIMEOUT, env=dict(os.environ))
    except subprocess.TimeoutExpired:
        log.error("claude turn timeout (%s)", conv.id)
        return None
    if proc.returncode != 0:
        log.error("claude rc=%d (%s): %s", proc.returncode, conv.id,
                  proc.stderr.strip()[:200])
        return None
    action = prompts.parse_action(proc.stdout)
    if action is None:
        log.error("unparseable turn output (%s): %s", conv.id, proc.stdout[:200])
    return action


def process(conv: store.Conversation) -> None:
    st = conv.state
    m = conv.mission
    phase = st["phase"]

    # Expiry / cap guards
    if conv.is_expired():
        conv.log_message("system", "expired")
        st["phase"] = "expired"
        conv.save_state(st)
        telegram(f"⏳ Envoy [{conv.id}]: conversation with {m['name']} expired "
                 f"({m.get('expires_days', 5)}d) without completing. "
                 f"Mission: {m['mission'][:120]}")
        return

    kickoff = phase == "kickoff"
    if not kickoff:
        msgs = new_inbound(m["contact"], st["last_seen_ts"])
        if not msgs:
            return  # nothing new; stay idle
        for ts, content in msgs:
            conv.log_message("contact", content)
            st["last_seen_ts"] = ts
        if phase == "escalated":
            # Paused for the operator — record inbound but don't auto-reply.
            conv.save_state(st)
            telegram(f"💬 Envoy [{conv.id}] (escalated/paused): new message from "
                     f"{m['name']}: {msgs[-1][1][:200]}")
            return

    if st["sent_count"] >= m.get("max_messages", 12):
        st["phase"] = "capped"
        conv.save_state(st)
        telegram(f"🛑 Envoy [{conv.id}]: hit max_messages "
                 f"({m.get('max_messages', 12)}) with {m['name']} — pausing. "
                 "Review the transcript and extend or stop.")
        return

    action = run_turn(conv, kickoff)
    if action is None:
        st["errors"] = st.get("errors", 0) + 1
        conv.save_state(st)
        if st["errors"] == 3:
            telegram(f"⚠️ Envoy [{conv.id}]: 3 consecutive turn failures — "
                     "check ~/.aos/logs/envoy/runner.log")
        return

    st["errors"] = 0
    st["turns"] = st.get("turns", 0) + 1
    msg = (action.get("message") or "").strip()
    act = action["action"]
    dry = m.get("dry_run", False)

    if msg and act in ("reply", "complete", "escalate"):
        if dry:
            conv.log_message("agent", msg, {"dry_run": True, "action": act})
        elif send_imessage(m["contact"], msg):
            conv.log_message("agent", msg, {"action": act})
            st["sent_count"] += 1
        else:
            st["errors"] = 1
            conv.save_state(st)
            return

    if act == "complete":
        st["phase"] = "complete"
        telegram(f"✅ Envoy [{conv.id}]: mission with {m['name']} complete. "
                 f"{action.get('summary', '')}")
    elif act == "escalate":
        st["phase"] = "escalated"
        telegram(f"🙋 Envoy [{conv.id}]: escalating conversation with {m['name']} — "
                 f"{action.get('summary') or action.get('reason', '')} "
                 f"Transcript: ~/.aos/work/envoy/{conv.id}/transcript.jsonl")
    elif kickoff:
        st["phase"] = "active"

    conv.save_state(st)


def cycle() -> int:
    convs = store.active_conversations()
    for conv in convs:
        try:
            process(conv)
        except Exception:
            log.exception("process failed for %s", conv.id)
    return len(convs)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_DIR / "runner.log"), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")
    n = cycle()
    log.info("cycle done (%d active)", n)


if __name__ == "__main__":
    main()
