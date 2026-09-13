"""generate-connect-script's embedded SSH-block writer (aos#2334).

The generated `connect-to-aos.sh` used to see ANY existing `Host aos` block
— marked or not — and leave it alone ("SSH host 'aos' already configured —
leaving it alone."), so a changed Tailscale IP (new machine, new network)
never landed on a re-run, and the script still reported success. Distinct
from #50 (stale IP captured at *generation* time on this machine); this is
the *write path on the receiving laptop* refusing to update an IP it
already correctly resolved.

Fixed with an idempotent, marker-delimited managed block: a re-run replaces
the block between `# >>> aos-connect: managed 'aos' host ... >>>` /
`# <<< ... <<<` comments (adding the markers on a first run, or when
upgrading from an older unmarked block), and never touches anything else in
`~/.ssh/config`.

This test does not run the whole generated script (which installs
Tailscale, opens the Tailscale app, and probes a live SSH connection) — it
extracts just the "2. SSH shortcut" section verbatim from the source file
between its banner comments and executes that section directly under bash,
with HOME/AOS_IP/AOS_USER controlled. `say()` is stubbed since it's defined
elsewhere in the real script.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "core" / "bin" / "setup" / "generate-connect-script"

BEGIN_MARKER = "# >>> aos-connect: managed 'aos' host — do not edit between these lines >>>"
END_MARKER = "# <<< aos-connect: managed 'aos' host <<<"


def _extract_ssh_shortcut_section() -> str:
    text = SCRIPT.read_text()
    start = text.index("# ── 2. SSH shortcut")
    end = text.index("# ── 3. Desktop shortcuts")
    section = text[start:end]
    assert "_write_aos_ssh_block" in section, "extraction boundary drifted from the source"
    return section


def _run(config_text: str | None, aos_ip: str, aos_user: str, tmp_path) -> str:
    """Run the real SSH-shortcut section against a sandboxed $HOME/.ssh/config
    and return the resulting file's contents."""
    home = tmp_path / "home"
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    config_path = ssh_dir / "config"
    if config_text is not None:
        config_path.write_text(config_text)

    section = _extract_ssh_shortcut_section()
    script = f"""
set -euo pipefail
say() {{ :; }}
{section}
"""
    env = {
        "HOME": str(home),
        "AOS_IP": aos_ip,
        "AOS_USER": aos_user,
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    return config_path.read_text()


def _expected_block(ip: str, user: str) -> str:
    return (
        f"{BEGIN_MARKER}\n"
        f"Host aos\n"
        f"    HostName {ip}\n"
        f"    User {user}\n"
        f"    ServerAliveInterval 60\n"
        f"{END_MARKER}"
    )


# ── 1. Fresh file (no config at all yet) ─────────────────────────────────

def test_fresh_file_gets_marked_block(tmp_path):
    out = _run(None, "203.0.113.10", "opuser", tmp_path)
    assert _expected_block("203.0.113.10", "opuser") in out


def test_fresh_file_is_idempotent_on_second_run(tmp_path):
    home = tmp_path / "home"
    first = _run(None, "203.0.113.10", "opuser", tmp_path)
    (home / ".ssh" / "config").write_text(first)
    second = _run(first, "203.0.113.10", "opuser", tmp_path)
    assert second == first


# ── 2. Existing UNMARKED block (older connect-to-aos.sh) ─────────────────

def test_existing_unmarked_block_is_replaced_with_managed_one(tmp_path):
    existing = (
        "# some prior comment\n"
        "\n"
        "# Added by AOS connect script\n"
        "Host aos\n"
        "    HostName 203.0.113.99\n"
        "    User olduser\n"
        "    ServerAliveInterval 60\n"
    )
    out = _run(existing, "203.0.113.10", "opuser", tmp_path)
    assert _expected_block("203.0.113.10", "opuser") in out
    assert "203.0.113.99" not in out
    assert "olduser" not in out
    # The unmanaged comment right before the old block is unrelated content
    # and must survive.
    assert "# some prior comment" in out


# ── 3. Existing MARKED block with a changed IP ───────────────────────────

def test_existing_marked_block_updates_changed_ip(tmp_path):
    existing = "\n" + _expected_block("203.0.113.99", "olduser") + "\n"
    out = _run(existing, "203.0.113.10", "opuser", tmp_path)
    assert _expected_block("203.0.113.10", "opuser") in out
    assert "203.0.113.99" not in out
    assert "olduser" not in out
    # Exactly one managed block — no duplicate markers left behind.
    assert out.count(BEGIN_MARKER) == 1
    assert out.count(END_MARKER) == 1


# ── 4. Unrelated hosts untouched ──────────────────────────────────────────

def test_unrelated_hosts_are_byte_for_byte_untouched(tmp_path):
    existing = (
        "Host github.com\n"
        "    User git\n"
        "    IdentityFile ~/.ssh/id_ed25519_github\n"
        "\n"
        "Host laptop\n"
        "    HostName 10.0.2.20\n"
        "    User alice\n"
        "\n"
        + _expected_block("203.0.113.99", "olduser") + "\n"
        "\n"
        "Host desktop\n"
        "    HostName 10.0.2.30\n"
        "    User bob\n"
    )
    out = _run(existing, "203.0.113.10", "opuser", tmp_path)

    assert (
        "Host github.com\n"
        "    User git\n"
        "    IdentityFile ~/.ssh/id_ed25519_github\n"
    ) in out
    assert (
        "Host laptop\n"
        "    HostName 10.0.2.20\n"
        "    User alice\n"
    ) in out
    assert (
        "Host desktop\n"
        "    HostName 10.0.2.30\n"
        "    User bob\n"
    ) in out
    assert _expected_block("203.0.113.10", "opuser") in out
    assert "203.0.113.99" not in out


def test_unrelated_hosts_untouched_with_legacy_unmarked_block(tmp_path):
    existing = (
        "Host github.com\n"
        "    User git\n"
        "\n"
        "# Added by AOS connect script\n"
        "Host aos\n"
        "    HostName 203.0.113.99\n"
        "    User olduser\n"
        "    ServerAliveInterval 60\n"
        "\n"
        "Host laptop\n"
        "    HostName 10.0.2.20\n"
        "    User alice\n"
    )
    out = _run(existing, "203.0.113.10", "opuser", tmp_path)

    assert "Host github.com\n    User git\n" in out
    assert "Host laptop\n    HostName 10.0.2.20\n    User alice\n" in out
    assert _expected_block("203.0.113.10", "opuser") in out
    assert "203.0.113.99" not in out
    assert "olduser" not in out


def test_config_file_permissions_remain_owner_only(tmp_path):
    import stat

    out_path_home = tmp_path / "home"
    _run(None, "203.0.113.10", "opuser", tmp_path)
    mode = stat.S_IMODE((out_path_home / ".ssh" / "config").stat().st_mode)
    assert mode == 0o600
