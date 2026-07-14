"""macOS notifications via terminal-notifier (with osascript fallback).

Used for:
- "Sentinel will send to X in 30s" countdown card
- "Sentinel sent to X" post-send confirmation
- "Sentinel needs direction on X" pending-queue escalation
- "Sentinel failed on X" error alerts
"""

from __future__ import annotations

import shutil
import subprocess


def _have_terminal_notifier() -> bool:
    return shutil.which("terminal-notifier") is not None


def notify(title: str, message: str, subtitle: str = "",
           sound: str = "Glass", group: str = "sentinel",
           url: str = "") -> bool:
    """Fire a macOS notification. Returns True if delivered."""
    if _have_terminal_notifier():
        cmd = [
            "terminal-notifier",
            "-title", title,
            "-message", message,
            "-group", group,
            "-sound", sound,
        ]
        if subtitle:
            cmd += ["-subtitle", subtitle]
        if url:
            cmd += ["-open", url]
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
            return True
        except Exception:
            pass

    # Fallback: osascript
    script = f'''
        display notification "{message.replace('"', '\\"')}" \\
            with title "{title.replace('"', '\\"')}" \\
            subtitle "{subtitle.replace('"', '\\"')}" \\
            sound name "{sound}"
    '''
    try:
        subprocess.run(["osascript", "-e", script],
                        capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def notify_pending(contact: str, task: str, reasons: list[str]) -> bool:
    return notify(
        title="Sentinel needs direction",
        message=f"{contact}: {task[:80]}",
        subtitle="Run: aos sentinel pending",
    )


def notify_send_imminent(contact: str, seconds: int, trigger_id: str) -> bool:
    return notify(
        title=f"Sentinel sending in {seconds}s",
        message=f"To: {contact}",
        subtitle="Run: aos sentinel cancel " + trigger_id,
    )


def notify_sent(contact: str, task: str) -> bool:
    return notify(
        title="Sentinel sent",
        message=f"To: {contact}",
        subtitle=task[:80],
    )


def notify_failed(contact: str, error: str) -> bool:
    return notify(
        title="Sentinel failed",
        message=f"{contact}: {error[:80]}",
        sound="Basso",
    )


def notify_test(message: str) -> bool:
    """For wiring verification."""
    return notify(
        title="Sentinel",
        message=message,
        subtitle="Wiring test",
    )
