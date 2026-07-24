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

import re
from pathlib import Path




def _extract_section(memo: str, header: str) -> str:
    """Pull text under a '## <header>' until the next '##' or end."""
    pattern = rf"##\s+{re.escape(header)}\s*\n+(.+?)(?=\n##\s|\Z)"
    m = re.search(pattern, memo, re.DOTALL)
    return m.group(1).strip() if m else ""


def send_to_telegram(memo_path: str, topic: str, council_id: str) -> dict:
    """Send the council synthesis to operator's Telegram (knowledge topic)."""
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

    # Topic-routed delivery: knowledge topic, falls back General -> DM.
    import sys
    sys.path.insert(0, str(Path.home() / "aos" / "core" / "engine"))
    try:
        from notify.router import send_notification
        result = send_notification(body, topic="knowledge",
                                   parse_mode="HTML", no_preview=True)
        return {"ok": result["delivered"], "result": result}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
