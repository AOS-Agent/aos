"""Notify — deliver council synthesis to the operator's Telegram.

Reads the synthesis memo from the vault, extracts the verdict + a key piece of
dissent, and sends to the operator's Telegram chat via the Bot API. Uses the
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets in the operator's Keychain.

The Telegram message format:
    🗳️ Council: <topic>

    <verdict paragraph>

    🔻 Dissent: <one-line>
    📄 Full memo: <vault path>
    Reply: @council <message> to push back

Operator can reply with `@council <anything>` to interject into the council.
The bridge listens for that pattern and routes via `council say`.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


def _get_secret(name: str) -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", name, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    # Fall back to agent-secret CLI
    try:
        out = subprocess.run(
            [str(Path.home() / "aos/core/bin/cli/agent-secret"), "get", name],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _extract_section(memo: str, header: str) -> str:
    """Pull text under a '## <header>' until the next '##' or end."""
    pattern = rf"##\s+{re.escape(header)}\s*\n+(.+?)(?=\n##\s|\Z)"
    m = re.search(pattern, memo, re.DOTALL)
    return m.group(1).strip() if m else ""


def send_to_telegram(memo_path: str, topic: str, council_id: str) -> dict:
    """Send the council synthesis to operator's Telegram."""
    token = _get_secret("TELEGRAM_BOT_TOKEN")
    chat_id = _get_secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return {"ok": False, "reason": "missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"}

    memo = Path(memo_path).read_text()
    # Strip frontmatter
    if memo.startswith("---"):
        end = memo.find("\n---", 3)
        if end > 0:
            memo = memo[end + 4:].lstrip()

    verdict = _extract_section(memo, "Verdict")
    dissent = _extract_section(memo, "Dissent and open questions")
    locks = _extract_section(memo, "What to lock in before action")

    # Compose message (HTML-safe; Telegram supports basic HTML)
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts = [f"🗳️ <b>Council: {esc(topic)}</b>", "", esc(verdict)]
    if dissent:
        # Take just the first 2-3 lines of dissent
        dissent_lines = [l.strip() for l in dissent.splitlines() if l.strip()]
        if dissent_lines:
            parts += ["", "🔻 <b>Dissent</b>", esc("\n".join(dissent_lines[:3]))]
    if locks:
        lock_lines = [l.strip() for l in locks.splitlines() if l.strip()]
        if lock_lines:
            parts += ["", "🔒 <b>Lock in</b>", esc("\n".join(lock_lines[:3]))]
    parts += ["", f"📄 <code>{esc(memo_path)}</code>",
             f"<i>Reply</i> <code>@council &lt;message&gt;</code> <i>to push back. ID:</i> <code>{esc(council_id)}</code>"]

    body = "\n".join(parts)
    # Telegram message size limit is 4096
    if len(body) > 4000:
        body = body[:3990] + "…"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": body,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            result = json.loads(resp.read())
            return {"ok": result.get("ok", False), "result": result}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
