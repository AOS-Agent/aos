"""
Migration 095: bind Qareen and n8n to loopback, preserving remote access.

WHAT WAS WRONG
Qareen defaulted to ``0.0.0.0`` and the shipped LaunchAgent template passed
``--host 0.0.0.0`` outright, so port 4096 served the entire dashboard —
people.db (1,148 contacts), comms.db (~248,000 WhatsApp/iMessage/email
messages), every task and project, and the vault API — unauthenticated, over
plain HTTP, to anything on the local network. Verified empirically before this
migration was written: ``curl http://<lan-ip>:4096/`` from another machine on
the house WiFi returned HTTP 200. n8n was the same on 5678, with workflow
credentials behind it. Both violate the standing rule in CLAUDE.md: "Network:
localhost only. Tailscale for remote access."

n8n deserves a note, because the plist *looked* correct. It already set
``N8N_HOST=127.0.0.1``, but that variable only supplies the public hostname n8n
uses to build editor and webhook URLs — it does not bind. The bind address is
``N8N_LISTEN_ADDRESS``, which defaults to ``'::'`` (all interfaces). Confirmed
against the installed n8n's own compiled config, not assumed.

WHY THIS MIGRATION EXISTS AT ALL
The framework fix is two template edits. But an install that reaches Qareen
across the LAN or over Tailscale *today* would lose that access the moment the
bind flips, and per the atomic migration rule the commit that breaks an
instance assumption ships the bridge. So this migration:

  1. Ensures a ``tailscale serve`` route to 127.0.0.1:4096 exists and VERIFIES
     it responds before touching the bind. Funnel (public internet) is never
     enabled — that would trade this bug for a worse one.

     NOTE 2026-07-26 — a retracted claim, kept because the reasoning matters.
     This docstring briefly asserted that serve was NOT tailnet-only here, based
     on a probe that appeared to come from outside and reached the route. The
     assertion was wrong: the probe resolved the .ts.net name to the node's own
     100.x tailnet address, so it was never external. A test that cannot fail is
     not evidence, and this one could not fail.

     What is actually true: AllowFunnel was empty, Funnel is permitted only on a
     short list of ports (Self.Capabilities in `tailscale status --json`), and
     serve was behaving exactly as documented.

     The durable lesson is about verification, not Tailscale. Do not try to prove
     a route is private by requesting it from a tailnet-joined machine — read the
     config (AllowFunnel, and the permitted-ports capability) instead. Prefer a
     serve port OUTSIDE the funnel-capable list, so the service cannot be
     published even by accident; this instance uses one for :4096, which has no
     authentication of its own.
  2. Only then rewrites the deployed plists and restarts the services.
  3. If it cannot establish that route, it does NOT flip silently. It flips and
     tells the operator loudly — printed output plus a Telegram message — with
     the exact command to restore reach. Losing remote access without being
     told is worse than the exposure; losing it *with* clear instructions is
     recoverable, and leaving 248,000 messages open to the LAN is not an
     acceptable resting state.

The Cloudflare tunnel path (aos.<domain> behind Cloudflare Access) connects to
``localhost:4096`` and is unaffected by a loopback bind.

Idempotent: plist rewrites are deterministic, ``tailscale serve`` is
declarative, launchctl lifecycle tolerates re-runs.

Kickstart safety: ``launchctl kickstart -k`` can block past a short subprocess
timeout while the old instance drains before the new one binds. TimeoutExpired
is caught and the real success criterion is a health poll on the port —
mirroring migrations 054/056/071/080.
"""

DESCRIPTION = "Bind Qareen (4096/4097) and n8n (5678) to loopback; preserve tailnet access"

import os
import plistlib
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"
LAUNCH_AGENTS = HOME / "Library" / "LaunchAgents"

QAREEN_LABEL = "com.aos.qareen"
QAREEN_PLIST = LAUNCH_AGENTS / f"{QAREEN_LABEL}.plist"
QAREEN_PORT = 4096

QAREEN_DEV_LABEL = "com.agent.qareen-dev"
QAREEN_DEV_PLIST = LAUNCH_AGENTS / f"{QAREEN_DEV_LABEL}.plist"
QAREEN_DEV_PORT = 4097

N8N_LABEL = "com.aos.n8n"
N8N_PLIST = LAUNCH_AGENTS / f"{N8N_LABEL}.plist"
N8N_PORT = 5678
N8N_LISTEN_KEY = "N8N_LISTEN_ADDRESS"

HOST_ENV_KEY = "AOS_QAREEN_HOST"
LOOPBACK = "127.0.0.1"

# Tailnet port that fronts Qareen after the flip. Serve on a dedicated port
# rather than a path prefix under :443 — the dashboard is an SPA with absolute
# asset paths, which a path-mounted proxy breaks.
SERVE_PORT = 8443

TAILSCALE_CANDIDATES = (
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
)


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _port_open(port: int, host: str = LOOPBACK, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _tailscale_bin() -> str | None:
    from shutil import which
    for candidate in TAILSCALE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return which("tailscale")


def _notify(text: str) -> None:
    """Best-effort Telegram ping. Never fails the migration."""
    try:
        sys.path.insert(0, str(AOS_ROOT / "core"))
        from lib.notify import send_telegram
        send_telegram(text)
    except Exception as e:  # noqa: BLE001
        print(f"  (could not send Telegram notice: {e})")


# ---------------------------------------------------------------------------
# plist rewriting
# ---------------------------------------------------------------------------
def _rebind_qareen_plist() -> bool:
    """Rewrite the deployed Qareen plist: --host arg AND AOS_QAREEN_HOST env.

    Both matter. The CLI arg wins over the env var, so changing only the
    module default (or only the env) leaves the service exposed — which is
    exactly why the original one-line-looking bug was not one line.
    """
    if not QAREEN_PLIST.exists():
        print(f"  ⚠ {QAREEN_PLIST.name} not deployed — skipping")
        return True
    with open(QAREEN_PLIST, "rb") as fh:
        data = plistlib.load(fh)

    args = data.get("ProgramArguments", [])
    if "--host" in args:
        args[args.index("--host") + 1] = LOOPBACK
    else:
        print("  ⚠ no --host arg in Qareen plist; relying on env var")
    data.setdefault("EnvironmentVariables", {})[HOST_ENV_KEY] = LOOPBACK

    with open(QAREEN_PLIST, "wb") as fh:
        plistlib.dump(data, fh)
    print(f"  ✓ {QAREEN_PLIST.name}: --host + {HOST_ENV_KEY} → {LOOPBACK}")
    return True


def _rebind_qareen_dev_plist() -> bool:
    """The dev backend on 4097 embeds host='0.0.0.0' inside a python -c string."""
    if not QAREEN_DEV_PLIST.exists():
        return True
    with open(QAREEN_DEV_PLIST, "rb") as fh:
        data = plistlib.load(fh)
    args = data.get("ProgramArguments", [])
    changed = False
    for i, arg in enumerate(args):
        if isinstance(arg, str) and "host='0.0.0.0'" in arg:
            args[i] = arg.replace("host='0.0.0.0'", f"host='{LOOPBACK}'")
            changed = True
    if not changed:
        return True
    with open(QAREEN_DEV_PLIST, "wb") as fh:
        plistlib.dump(data, fh)
    print(f"  ✓ {QAREEN_DEV_PLIST.name}: uvicorn host → {LOOPBACK}")
    return True


def _rebind_n8n_plist() -> bool:
    """Add N8N_LISTEN_ADDRESS. N8N_HOST does not bind; this does."""
    if not N8N_PLIST.exists():
        return True
    with open(N8N_PLIST, "rb") as fh:
        data = plistlib.load(fh)
    env = data.setdefault("EnvironmentVariables", {})
    if env.get(N8N_LISTEN_KEY) == LOOPBACK:
        return True
    env[N8N_LISTEN_KEY] = LOOPBACK
    with open(N8N_PLIST, "wb") as fh:
        plistlib.dump(data, fh)
    print(f"  ✓ {N8N_PLIST.name}: {N8N_LISTEN_KEY} → {LOOPBACK}")
    return True


def _restart(label: str, plist: Path, port: int, wait: int = 60) -> bool:
    """bootout → bootstrap → kickstart, then poll the port on loopback."""
    if not plist.exists():
        return True
    uid = os.getuid()
    domain = f"gui/{uid}"
    service = f"{domain}/{label}"

    _run(["launchctl", "bootout", service])
    time.sleep(1)
    result = _run(["launchctl", "bootstrap", domain, str(plist)])
    if result.returncode != 0:
        print(f"  ⚠ bootstrap {label} returned {result.returncode}: {result.stderr.strip()}")
    try:
        _run(["launchctl", "kickstart", "-k", service])
    except subprocess.TimeoutExpired:
        print(f"  ⚠ kickstart {label} timed out (old instance draining) — polling port")

    for i in range(wait // 2):
        time.sleep(2)
        if _port_open(port):
            print(f"  ✓ {label} listening on {LOOPBACK}:{port} after {(i + 1) * 2}s")
            return True
    print(f"  ✗ {label} not listening on {LOOPBACK}:{port} after {wait}s")
    print(f"    Check ~/.aos/logs/{label.split('.')[-1]}.err.log")
    return False


# ---------------------------------------------------------------------------
# plist backup / restore — the way back if a service will not come up
# ---------------------------------------------------------------------------
#
# This migration rewrites LaunchAgent plists and restarts services on machines
# nobody is watching. Without a copy of the original, a service that fails to
# restart leaves the operator with a rewritten config, a dead dashboard, and
# nothing to roll back to. Take the copy first; put it back on failure.

BACKUP_SUFFIX = ".pre-095.bak"


def _backup_plist(plist: Path) -> Path | None:
    """Copy a plist aside before rewriting. Returns the backup path, or None."""
    if not plist.exists():
        return None
    backup = plist.with_suffix(plist.suffix + BACKUP_SUFFIX)
    try:
        shutil.copy2(plist, backup)
        return backup
    except OSError as exc:
        print(f"  ⚠ could not back up {plist.name}: {exc}")
        return None


def _restore_plist(plist: Path, backup: Path | None, label: str, port: int) -> bool:
    """Put the original back and restart. Returns True if the service recovered."""
    if backup is None or not backup.exists():
        print(f"  ✗ no backup of {plist.name} to restore from")
        return False
    try:
        shutil.copy2(backup, plist)
    except OSError as exc:
        print(f"  ✗ could not restore {plist.name}: {exc}")
        return False
    print(f"  ↩ restored {plist.name} from backup — retrying restart")
    return _restart(label, plist, port, wait=60)


# ---------------------------------------------------------------------------
# tailscale serve — the continuity bridge, established BEFORE the flip
# ---------------------------------------------------------------------------
# Opt-in only. See _ensure_serve.
SERVE_OPT_IN_ENV = "AOS_MIGRATION_MAY_CREATE_TAILSCALE_SERVE"


def _ensure_serve() -> tuple[bool, str]:
    """Ensure + verify a tailnet route to Qareen. Returns (ok, description).

    DISABLED BY DEFAULT as of 2026-07-26 — but NOT for the reason first recorded
    here. That earlier note claimed serve was secretly public, based on a probe
    that turned out to resolve the .ts.net name to the node's own 100.x tailnet
    address. It was never external, so it could not have failed, and the
    conclusion drawn from it was wrong. Serve was behaving as documented.

    The remaining reason to stay opt-in is smaller but real: this creates network
    reachability on someone else's machine, unattended, during a migration whose
    actual job is to REMOVE reachability. The operator should choose their remote
    path deliberately — especially since Qareen has no authentication of its own,
    so whatever fronts it is the entire security boundary.

    Note also that a tailnet-joined host cannot verify privacy by fetching the
    route. Read the config instead: AllowFunnel in `tailscale serve status
    --json`, and the funnel-capable port list in `tailscale status --json` ->
    Self.Capabilities. A port outside that list cannot be published at all, which
    makes it the safer choice for an unauthenticated service.

    Set AOS_MIGRATION_MAY_CREATE_TAILSCALE_SERVE=1 to opt in.
    """
    if os.environ.get(SERVE_OPT_IN_ENV) != "1":
        return False, (
            "not attempted — this migration does not create network routes on "
            "your behalf; choose your remote path deliberately. Set "
            f"{SERVE_OPT_IN_ENV}=1 to opt in."
        )

    ts = _tailscale_bin()
    if not ts:
        return False, "Tailscale not installed"

    status = _run([ts, "status"])
    if status.returncode != 0:
        return False, f"tailscale status failed: {status.stderr.strip() or 'not running'}"

    existing = _run([ts, "serve", "status"])
    already = f"{LOOPBACK}:{QAREEN_PORT}" in (existing.stdout or "")
    if not already:
        # `tailscale serve` is declarative; re-running is safe.
        add = _run(
            [ts, "serve", "--bg", f"--https={SERVE_PORT}", f"{LOOPBACK}:{QAREEN_PORT}"],
            timeout=30,
        )
        if add.returncode != 0:
            return False, f"tailscale serve failed: {add.stderr.strip()}"

    verify = _run([ts, "serve", "status"])
    if f"{LOOPBACK}:{QAREEN_PORT}" not in (verify.stdout or ""):
        return False, "serve route not present after configuration"

    # Confirm the proxy target actually answers. Qareen is still wildcard-bound
    # at this point, so loopback necessarily works — this catches a dead service
    # before we take away the LAN fallback.
    if not _port_open(QAREEN_PORT):
        return False, f"nothing listening on {LOOPBACK}:{QAREEN_PORT} to serve"

    host = ""
    for line in (verify.stdout or "").splitlines():
        if line.startswith("https://") and f":{SERVE_PORT}" in line:
            host = line.split()[0]
            break
    return True, host or f"https://<this-machine>:{SERVE_PORT}"


# ---------------------------------------------------------------------------
# migration contract
# ---------------------------------------------------------------------------
def check() -> bool:
    """Applied when every deployed service binds loopback."""
    if QAREEN_PLIST.exists():
        with open(QAREEN_PLIST, "rb") as fh:
            data = plistlib.load(fh)
        args = data.get("ProgramArguments", [])
        if "--host" in args and args[args.index("--host") + 1] != LOOPBACK:
            return False
        if data.get("EnvironmentVariables", {}).get(HOST_ENV_KEY) != LOOPBACK:
            return False

    if QAREEN_DEV_PLIST.exists():
        with open(QAREEN_DEV_PLIST, "rb") as fh:
            data = plistlib.load(fh)
        if any(
            isinstance(a, str) and "host='0.0.0.0'" in a
            for a in data.get("ProgramArguments", [])
        ):
            return False

    if N8N_PLIST.exists():
        with open(N8N_PLIST, "rb") as fh:
            data = plistlib.load(fh)
        if data.get("EnvironmentVariables", {}).get(N8N_LISTEN_KEY) != LOOPBACK:
            return False

    return True


def up() -> bool:
    print("  Hardening network binds to loopback (Qareen 4096/4097, n8n 5678)")

    # 1. Continuity FIRST. Never flip a bind before the replacement path works.
    serve_ok, serve_info = _ensure_serve()
    if serve_ok:
        print(f"  ✓ Tailnet route verified: {serve_info} → {LOOPBACK}:{QAREEN_PORT}")
    else:
        print(f"  ⚠ Could not establish a tailnet route: {serve_info}")

    # 2. Rewrite the deployed plists — taking a copy of each one FIRST, so a
    #    service that will not come back can be put back the way it was. This
    #    runs unattended on machines nobody is watching; "rewritten config,
    #    dead service, no way back" is not an acceptable outcome to hand
    #    someone in exchange for closing an exposure.
    qareen_backup = _backup_plist(QAREEN_PLIST)
    dev_backup = _backup_plist(QAREEN_DEV_PLIST)
    n8n_backup = _backup_plist(N8N_PLIST)

    _rebind_qareen_plist()
    _rebind_qareen_dev_plist()
    _rebind_n8n_plist()

    # 3. Restart. Qareen is the one that matters; a dev-backend or n8n hiccup
    #    must not fail the whole batch (KeepAlive owns their liveness).
    ok = _restart(QAREEN_LABEL, QAREEN_PLIST, QAREEN_PORT)
    if not _restart(QAREEN_DEV_LABEL, QAREEN_DEV_PLIST, QAREEN_DEV_PORT, wait=30):
        print("  ⚠ dev backend (4097) did not come back — non-fatal")
        _restore_plist(QAREEN_DEV_PLIST, dev_backup, QAREEN_DEV_LABEL, QAREEN_DEV_PORT)
    if not _restart(N8N_LABEL, N8N_PLIST, N8N_PORT, wait=90):
        print("  ⚠ n8n (5678) did not come back — non-fatal, check its log")
        _restore_plist(N8N_PLIST, n8n_backup, N8N_LABEL, N8N_PORT)

    if not ok:
        # Qareen is the operator's dashboard. Leaving it down to close an
        # exposure is the wrong trade: roll back, tell them plainly, and let
        # them retry deliberately rather than discover a dead service later.
        print("  ✗ Qareen failed to restart on loopback — rolling back")
        recovered = _restore_plist(QAREEN_PLIST, qareen_backup,
                                   QAREEN_LABEL, QAREEN_PORT)
        if recovered:
            print("  ↩ Qareen is back on its previous binding. NOTE: that "
                  "binding is the exposed one — rerun this migration or set "
                  f"--host {LOOPBACK} by hand once the restart problem is fixed.")
            _notify(
                "⚠️ I tried to close a security hole on your machine and the "
                "dashboard would not restart, so I put it back exactly as it "
                "was. Nothing is broken — but the hole is still open. Tell me "
                "and I will sort it out properly."
            )
        else:
            print(f"  ✗✗ ROLLBACK ALSO FAILED. Original plist saved at "
                  f"{QAREEN_PLIST}{BACKUP_SUFFIX} — restore it by hand.")
            _notify(
                "🚨 Your dashboard did not restart and I could not put the old "
                f"settings back automatically. A copy is saved next to the "
                f"original with the suffix {BACKUP_SUFFIX}. This one needs you."
            )
        return False

    # 4. Tell the operator, loudly, if their remote path is not guaranteed.
    if not serve_ok:
        banner = (
            "\n"
            "  ================================================================\n"
            "  ⚠  REMOTE ACCESS MAY HAVE CHANGED\n"
            "  ================================================================\n"
            "  Qareen (4096) and n8n (5678) now listen on 127.0.0.1 only. They\n"
            "  were reachable from any device on your local network, with no\n"
            "  password, serving your contacts and message history.\n"
            "\n"
            f"  A Tailscale route could NOT be set up here: {serve_info}\n"
            "\n"
            "  If you used the dashboard from another machine, prefer an\n"
            "  AUTHENTICATED path (e.g. a Cloudflare Access hostname fronting\n"
            f"  {LOOPBACK}:{QAREEN_PORT}). Qareen has no login of its own, so\n"
            "  whatever fronts it IS the security boundary.\n"
            "\n"
            "  A Tailscale route is NOT recommended blind:\n"
            f"    tailscale serve --bg --https={SERVE_PORT} {LOOPBACK}:{QAREEN_PORT}\n"
            "  Qareen has no login of its own, so whatever fronts it IS the\n"
            "  security boundary. Do not confirm a route is private by fetching\n"
            "  it from a machine on your tailnet — that reaches it either way.\n"
            "  Read the config: AllowFunnel must be empty in `tailscale serve\n"
            "  status --json`, and `tailscale status --json` lists the only ports\n"
            "  Funnel is permitted on. A port outside that list cannot be made\n"
            "  public even by mistake, so prefer one.\n"
            "\n"
            "  Do NOT set AOS_QAREEN_HOST back to 0.0.0.0 unless you accept that\n"
            "  every device on the local network can read your contacts and\n"
            f"  message history without a password ({QAREEN_PLIST}).\n"
            "  ================================================================\n"
        )
        print(banner)
        _notify(
            "🔒 I closed a security hole on the Mac Mini.\n\n"
            "Your dashboard and n8n were open to every device on your home WiFi "
            "with no password. Anyone on that network could read your contacts "
            "and messages. They now only accept connections from the Mini itself.\n\n"
            "One catch: I did not set up a remote route automatically. Doing that "
            "the usual way once made the dashboard public by mistake, so I would "
            "rather you and I choose the path together. If you used the dashboard "
            "from your laptop or phone, tell me and I will sort it out. 👍"
        )
    else:
        print(f"  ✓ Remote access preserved via {serve_info}")
        _notify(
            "🔒 Security fix applied on the Mac Mini.\n\n"
            "Your dashboard and n8n were reachable from every device on your home "
            "WiFi with no password — contacts, messages, tasks, all of it. That is "
            "now closed. They only accept local connections.\n\n"
            f"You can still reach the dashboard from your other devices here:\n{serve_info}\n\n"
            "Nothing else changes. ✅"
        )

    return True


if __name__ == "__main__":
    if check():
        print("Migration 095 already applied")
    else:
        print("Done" if up() else "Failed")
