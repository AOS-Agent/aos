"""Slack reauth support for converse (PLAN.md §3).

Generalizes the slack-lite prototype's `decrypt_cookie.py` (Chrome Safe
Storage -> fresh `xoxd` session cookie) into a reusable flow: extract, verify
against the live Slack session, and — only on success — persist to Keychain.

This module provides the LOGIC behind `converse reauth slack`; wiring it up
as an actual CLI subcommand is core/bin/cli/converse (T3, a later wave, per
PLAN.md §9's build breakdown: "`converse reauth slack` support... generalize
the Chrome-cookie decryptor" is T2a's job, the CLI itself is T3's). Always
operator-invoked — never called automatically by the supervisor or by
poll()/send() on an auth failure.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..channels.slack_session import SlackSessionClient, secret_get, secret_set

log = logging.getLogger(__name__)

CHROME_COOKIES_DB = (
    Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Cookies"
)


def decrypt_xoxd_from_chrome(cookie_host_like: str = "%slack.com") -> str:
    """Extract a fresh xoxd session cookie from Chrome's Safe
    Storage-encrypted cookie jar (same recipe as slack-lite's
    decrypt_cookie.py). Requires Chrome to be logged into the workspace and
    Full Disk Access on this process."""
    if not CHROME_COOKIES_DB.exists():
        raise FileNotFoundError(f"Chrome cookies db not found: {CHROME_COOKIES_DB}")

    tmp_path = tempfile.mktemp(suffix=".sqlite")
    shutil.copy2(CHROME_COOKIES_DB, tmp_path)
    try:
        con = sqlite3.connect(tmp_path)
        con.text_factory = bytes
        row = con.execute(
            "SELECT encrypted_value FROM cookies WHERE host_key LIKE ? AND name='d' LIMIT 1",
            (cookie_host_like,),
        ).fetchone()
        con.close()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not row:
        raise RuntimeError(
            f"no Slack 'd' cookie found in Chrome for host LIKE {cookie_host_like!r} — "
            "log into Slack in Chrome first"
        )
    encrypted_value = row[0]

    passphrase = subprocess.check_output(
        ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage", "-a", "Chrome"]
    ).strip()
    key = PBKDF2HMAC(
        algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1003,
        backend=default_backend(),
    ).derive(passphrase)

    iv = b" " * 16
    ciphertext = encrypted_value[3:]  # strip the 'v10'/'v11' version prefix
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend()).decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    plaintext = plaintext[: -plaintext[-1]]  # strip PKCS7 padding

    # Newer Chrome versions prepend a 32-byte header before the cookie value.
    for candidate in (plaintext, plaintext[32:]):
        try:
            s = candidate.decode()
        except UnicodeDecodeError:
            continue
        if s.startswith("xoxd-"):
            return s

    raise RuntimeError("decrypted cookie did not look like an xoxd- token")


def reauth_slack(workspace_url: str, *, persist: bool = True) -> dict:
    """Full reauth flow: decrypt a fresh xoxd from Chrome, verify it against
    the live Slack session (auth.test), and — only on success — write it
    back to Keychain (SLACK_XOXD) via agent-secret, clearing the way for the
    supervisor to resume any sessions paused with paused_reason='reauth'
    (the resume itself is the supervisor's job, T3 — this function only
    proves the new token works and persists it).

    Returns {"ok": True, "user":, "team":, "user_id":} on success, or
    {"ok": False, "error": "..."} — never raises; every failure mode
    (missing Chrome cookie, Keychain read failure, invalid_auth again) is a
    clear message in "error", since this is the CLI operator's feedback
    loop, not another automated caller's.
    """
    try:
        xoxd = decrypt_xoxd_from_chrome()
    except Exception as e:
        return {"ok": False, "error": f"cookie extraction failed: {e}"}

    try:
        xoxc = secret_get("SLACK_XOXC")  # xoxc is stable across reauth; only the xoxd session cookie rotates
    except Exception as e:
        return {"ok": False, "error": f"could not read SLACK_XOXC: {e}"}

    try:
        client = SlackSessionClient(workspace_url, xoxc=xoxc, xoxd=xoxd)
        result = client.auth_test()
    except Exception as e:
        return {"ok": False, "error": f"auth.test call failed: {e}"}

    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "auth_test failed"), "raw": result}

    if persist:
        try:
            secret_set("SLACK_XOXD", xoxd)
        except Exception as e:
            return {"ok": False, "error": f"verified but could not persist to Keychain: {e}"}

    return {
        "ok": True,
        "user": result.get("user"),
        "team": result.get("team"),
        "user_id": result.get("user_id"),
    }
