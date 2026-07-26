"""
Migration 094: Correct TRANSCRIBER_PORT in the deployed transcriber LaunchAgent.

The transcriber's port was declared in five places that disagreed with its
manifest. `core/services/transcriber/service.yaml` says :7602; the plist
template, main.py's env-var default, the bridge's voice client, and the shared
transcriber client all said :7601 — which is **whatsmeow's** port.

Consequences on an affected machine:

  * The transcriber and the WhatsApp Go bridge raced for the same socket.
    Whichever launchd started first won; the other failed to bind.
  * The reconcile check health-probes the manifest port (:7602), found nothing,
    and kickstarted the transcriber on every pass — forever.
  * bridge/voice_transcriber.py POSTed voice audio at :7601, got a non-answer,
    and silently fell through to its per-request mlx-whisper fallback — loading
    a second copy of Whisper per voice note while the preloaded ~2.3GB model
    sat idle. Voice notes "worked", slowly, for the wrong reason.

The source-side fix is in the same commit. Migration 012 auto-globs
*.plist.template and installs them, so FRESH installs pick up the corrected
template there. This migration exists for EXISTING machines already past 012,
whose deployed plist still carries the wrong port.

Surgical by design: it rewrites only the TRANSCRIBER_PORT value, not the whole
plist, so local customization (PATH, log paths) survives.

Operator opt-out is respected. A disabled service is a deliberate choice, not
drift — if the operator has `launchctl disable`d the transcriber, this migration
corrects the plist on disk and leaves the job stopped. It must never be the
thing that silently switches a service back on.

Idempotent: check() compares the deployed plist's port against the manifest, so
a corrected (or absent, or disabled) plist re-runs as a no-op.
"""

DESCRIPTION = "Correct TRANSCRIBER_PORT in deployed transcriber LaunchAgent (:7601 → manifest)"

import os
import re
import subprocess
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"

PLIST_NAME = "com.aos.transcriber"
PLIST_PATH = HOME / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"
MANIFEST_PATH = AOS_ROOT / "core" / "services" / "transcriber" / "service.yaml"

# <key>TRANSCRIBER_PORT</key> <string>NNNN</string> — capture the value only.
_PORT_ENTRY = re.compile(
    r"(<key>TRANSCRIBER_PORT</key>\s*<string>)(\d+)(</string>)"
)


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _manifest_port() -> int | None:
    """The transcriber's declared port, read from its own manifest.

    Parsed with a regex rather than yaml so the migration carries no import
    dependency — migrations run before service venvs are guaranteed rebuilt.
    """
    if not MANIFEST_PATH.exists():
        return None
    m = re.search(r"^port:\s*(\d+)", MANIFEST_PATH.read_text(), re.MULTILINE)
    return int(m.group(1)) if m else None


def _deployed_port() -> int | None:
    """The TRANSCRIBER_PORT currently baked into the deployed plist."""
    if not PLIST_PATH.exists():
        return None
    m = _PORT_ENTRY.search(PLIST_PATH.read_text())
    return int(m.group(2)) if m else None


def _is_disabled() -> bool:
    """True when the operator has launchctl-disabled the transcriber.

    `launchctl print-disabled gui/<uid>` prints one line per override; the
    value renders as `disabled` on some macOS versions and `true` on others,
    so match either rather than the exact spelling.
    """
    try:
        result = _run(["launchctl", "print-disabled", f"gui/{os.getuid()}"], timeout=5)
    except subprocess.TimeoutExpired:
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        if f'"{PLIST_NAME}"' in line:
            return "disabled" in line.lower() or "true" in line.lower()
    return False


def check() -> bool:
    """Applied when the deployed plist's port matches the manifest.

    No plist deployed and no manifest are both "nothing to repair" — migration
    012 installs from the corrected template on fresh installs, and the
    reconcile check deploys it if it goes missing later.
    """
    target = _manifest_port()
    if target is None:
        return True

    deployed = _deployed_port()
    if deployed is None:
        return True

    return deployed == target


def up() -> bool:
    target = _manifest_port()
    if target is None:
        print(f"  ⚠ Transcriber manifest not found at {MANIFEST_PATH} — nothing to repair")
        return True

    deployed = _deployed_port()
    if deployed is None:
        print("  ✓ No deployed transcriber plist with a TRANSCRIBER_PORT entry — nothing to repair")
        return True

    if deployed == target:
        print(f"  ✓ Deployed plist already on :{target}")
        return True

    text = PLIST_PATH.read_text()
    patched = _PORT_ENTRY.sub(rf"\g<1>{target}\g<3>", text, count=1)
    if patched == text:
        print(f"  ⚠ Could not rewrite TRANSCRIBER_PORT in {PLIST_PATH}")
        return False

    PLIST_PATH.write_text(patched)
    print(f"  ✓ Corrected TRANSCRIBER_PORT {deployed} → {target} in deployed plist")

    # An operator-disabled service stays off. The plist is corrected on disk so
    # it is right whenever they choose to re-enable it.
    if _is_disabled():
        print("  ✓ Transcriber is disabled by the operator — plist corrected, service left stopped")
        return True

    service = f"gui/{os.getuid()}/{PLIST_NAME}"

    # Restart so the running instance binds the corrected port. `kickstart -k`
    # can block past a short timeout while the old instance drains — that is
    # launchctl taking its time, not a failure (migrations 054/056). The plist
    # is already correct on disk either way, and KeepAlive retries.
    try:
        result = _run(["launchctl", "kickstart", "-k", service], timeout=30)
        if result.returncode == 0:
            print(f"  ✓ Transcriber restarted on :{target}")
        else:
            print(f"  ⚠ kickstart returned {result.returncode} — KeepAlive will retry")
    except subprocess.TimeoutExpired:
        print("  ⚠ kickstart timed out (old instance draining) — KeepAlive will retry")

    return True
