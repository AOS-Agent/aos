#!/usr/bin/env python3
"""brokerd — the AOS approval broker (prototype).

A loopback-only HTTP daemon that owns trust decisions for tool calls made by
agents inside a forked workspace client. The client never decides; it asks.

Contract name: aos.broker.local/v1
Bind:          127.0.0.1 only (never 0.0.0.0 — council lock)
Auth:          x-broker-key header, key minted at first start into ./broker.key
Fail-closed:   unreachable broker, expired wait, or unknown trust level = DENY

Stdlib only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CONTRACT = "aos.broker.local/v1"
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 4110
MAX_WAIT = 290  # seconds; caller-supplied timeouts are clamped to this

TRUST_LEVEL_NAMES = {
    0: "shadow",
    1: "approval",
    2: "semi-auto",
    3: "full-auto",
}

DEFAULT_TRUST = {
    "_comment": (
        "Prototype copy of the trust config. In AOS this is ~/.aos/config/trust.yaml; "
        "see README.md for the field-by-field mapping."
    ),
    "default_level": 2,
    "always_escalate": ["secrets.read", "system.destroy", "payment.send"],
    "destructive_capabilities": ["shell.exec", "fs.delete", "system.destroy"],
    "principals": {
        "operator:hisham": {"level": 3},
        "member:guest": {"level": 1},
    },
}

VALID_PRINCIPAL_KINDS = ("operator", "member", "agent")


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


class Broker:
    """All mutable broker state. One instance per daemon."""

    def __init__(self, state_dir: str):
        self.dir = os.path.abspath(state_dir)
        os.makedirs(self.dir, exist_ok=True)
        self.trust_path = os.path.join(self.dir, "trust.json")
        self.audit_path = os.path.join(self.dir, "audit.jsonl")
        self.key_path = os.path.join(self.dir, "broker.key")

        self.started_at = time.time()
        self.requests = {}          # id -> record dict
        self.events = {}            # id -> threading.Event
        self.lock = threading.Lock()
        self.audit_lock = threading.Lock()

        self._trust = None

        self.key = self._load_or_mint_key()

    # -- key -----------------------------------------------------------

    def _load_or_mint_key(self) -> str:
        if os.path.exists(self.key_path):
            with open(self.key_path) as fh:
                key = fh.read().strip()
            if key:
                os.chmod(self.key_path, 0o600)
                return key
        key = secrets.token_hex(32)
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key + "\n")
        os.chmod(self.key_path, 0o600)
        return key

    def check_key(self, presented) -> bool:
        if not presented:
            return False
        return secrets.compare_digest(str(presented).strip(), self.key)

    # -- trust config --------------------------------------------------

    def trust(self) -> dict:
        """Read trust.json fresh on every call, so editing the file takes
        effect without a restart. A broken or missing file must not open the
        gates, so both failure paths land on level 0 (shadow = deny)."""
        if not os.path.exists(self.trust_path):
            with open(self.trust_path, "w") as fh:
                json.dump(DEFAULT_TRUST, fh, indent=2)
                fh.write("\n")
        try:
            with open(self.trust_path) as fh:
                self._trust = json.load(fh)
        except (OSError, ValueError):
            self._trust = {"default_level": 0, "always_escalate": [],
                           "destructive_capabilities": [], "principals": {},
                           "_load_error": True}
        return self._trust

    def level_for(self, principal: dict) -> int:
        cfg = self.trust()
        pid = "%s:%s" % (principal.get("kind"), principal.get("id"))
        entry = (cfg.get("principals") or {}).get(pid)
        if isinstance(entry, dict) and "level" in entry:
            return entry["level"]
        if isinstance(entry, int):
            return entry
        return cfg.get("default_level", 0)

    # -- audit ---------------------------------------------------------

    def audit(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True)
        with self.audit_lock:
            with open(self.audit_path, "a") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())

    # -- pending -------------------------------------------------------

    def pending_count(self) -> int:
        with self.lock:
            return sum(1 for r in self.requests.values() if r["state"] == "pending")


# --------------------------------------------------------------------------
# decision core
# --------------------------------------------------------------------------


def evaluate(level, capability, destructive, always_escalate):
    """Pure trust-level semantics. Returns (state, reason).

    state is one of: allow | deny | pending
    """
    if level not in TRUST_LEVEL_NAMES:
        return "deny", "unknown_trust_level_fail_closed"
    if level == 0:
        # shadow: record what would have happened, never let it happen
        return "deny", "shadow_mode_deny_with_record"
    if level == 1:
        return "pending", "approval_required"
    if level == 2:
        if capability in always_escalate:
            return "pending", "capability_always_escalate"
        if destructive:
            return "pending", "destructive_flag"
        return "allow", "semi_auto_allow"
    # level 3
    if capability in always_escalate:
        return "pending", "capability_always_escalate"
    return "allow", "full_auto_allow"


def validate_principal(principal):
    """Returns an error string, or None when the principal is usable."""
    if principal is None:
        return "principal is required on every approval request"
    if not isinstance(principal, dict):
        return "principal must be an object {kind, id}"
    kind = principal.get("kind")
    pid = principal.get("id")
    if kind not in VALID_PRINCIPAL_KINDS:
        return "principal.kind must be one of %s" % (list(VALID_PRINCIPAL_KINDS),)
    if not pid or not isinstance(pid, str):
        return "principal.id must be a non-empty string"
    return None


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "brokerd/0.1"
    protocol_version = "HTTP/1.1"

    # injected by make_server
    broker: Broker = None  # type: ignore[assignment]

    # -- plumbing ------------------------------------------------------

    def log_message(self, fmt, *args):  # quieter, and to stderr
        sys.stderr.write("[brokerd] %s\n" % (fmt % args))

    def _send(self, code, payload):
        body = json.dumps(payload, indent=2, sort_keys=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode())
        except ValueError:
            return None

    def _authed(self):
        if self.broker.check_key(self.headers.get("x-broker-key")):
            return True
        self._send(401, {
            "error": "unauthorized",
            "detail": "present the local contract key in the x-broker-key header",
            "contract": CONTRACT,
        })
        return False

    # -- routes --------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._authed():
            return
        if path == "/v1/liveness":
            return self._liveness()
        if path == "/v1/approvals/pending":
            return self._pending()
        if path.startswith("/v1/approvals/") and path.endswith("/wait"):
            rid = path[len("/v1/approvals/"):-len("/wait")]
            return self._wait(rid, parse_qs(parsed.query))
        if path.startswith("/v1/approvals/"):
            return self._show(path[len("/v1/approvals/"):])
        self._send(404, {"error": "no_such_route", "path": path})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._authed():
            return
        if path == "/v1/approvals":
            return self._create()
        if path.startswith("/v1/approvals/") and path.endswith("/decide"):
            return self._decide(path[len("/v1/approvals/"):-len("/decide")])
        self._send(404, {"error": "no_such_route", "path": path})

    # -- handlers ------------------------------------------------------

    def _liveness(self):
        b = self.broker
        cfg = b.trust()
        pending = b.pending_count()
        level = cfg.get("default_level", 0)
        self._send(200, {
            "schema": "aos.broker.liveness/v1",
            "contract": CONTRACT,
            "alive": True,
            "state": "UP",
            "uptime_s": round(time.time() - b.started_at, 3),
            "pending": pending,
            "trust_level": level,
            "trust_level_name": TRUST_LEVEL_NAMES.get(level, "unknown"),
            "fail_closed": True,
            "render": {
                "must_render": True,
                "label": "BROKER UP",
                "detail": "trust level %s (%s) - %d pending" % (
                    level, TRUST_LEVEL_NAMES.get(level, "unknown"), pending),
                "tone": "normal",
            },
            "render_contract": (
                "Clients MUST display render.label and render.detail before "
                "attempting any tool call. Absence of this payload is not a "
                "neutral default: it is the DOWN state and means deny."
            ),
        })

    def _pending(self):
        b = self.broker
        with b.lock:
            items = [self._public(r) for r in b.requests.values()
                     if r["state"] == "pending"]
        items.sort(key=lambda r: r["created_at"])
        self._send(200, {"pending": items, "count": len(items)})

    def _show(self, rid):
        with self.broker.lock:
            rec = self.broker.requests.get(rid)
        if not rec:
            return self._send(404, {"error": "no_such_request", "id": rid})
        self._send(200, self._public(rec))

    @staticmethod
    def _public(rec):
        out = {k: v for k, v in rec.items() if not k.startswith("_")}
        return out

    def _create(self):
        b = self.broker
        body = self._read_json()
        if body is None:
            return self._send(400, {"error": "bad_json"})

        principal = body.get("principal")
        err = validate_principal(principal)
        if err:
            return self._send(400, {"error": "principal_required", "detail": err})
        capability = body.get("capability")
        tool = body.get("tool")
        if not capability or not isinstance(capability, str):
            return self._send(400, {"error": "capability_required"})
        if not tool or not isinstance(tool, str):
            return self._send(400, {"error": "tool_required"})

        cfg = b.trust()
        always = list(cfg.get("always_escalate") or [])
        server_destructive = list(cfg.get("destructive_capabilities") or [])
        # The caller may declare a call destructive; the server list is
        # authoritative. A client hint can only escalate, never de-escalate.
        destructive = bool(body.get("destructive")) or capability in server_destructive

        level = b.level_for(principal)
        state, reason = evaluate(level, capability, destructive, always)

        now = time.time()
        rid = uuid.uuid4().hex[:12]
        rec = {
            "id": rid,
            "created_at": now,
            "principal": principal,
            "principal_key": "%s:%s" % (principal["kind"], principal["id"]),
            "capability": capability,
            "tool": tool,
            "summary": body.get("summary") or "",
            "args_digest": body.get("args_digest") or "-",
            "destructive": destructive,
            "trust_level": level,
            "trust_level_name": TRUST_LEVEL_NAMES.get(level, "unknown"),
            "state": state,
            "reason": reason,
            "decider": None,
            "decided_at": None,
        }
        with b.lock:
            b.requests[rid] = rec
            b.events[rid] = threading.Event()

        if state == "pending":
            rec["decider"] = "pending:human"
            self._send(202, dict(self._public(rec), **{
                "next": "GET /v1/approvals/%s/wait?timeout=290" % rid,
                "note": "blocked pending human decision; timeout denies",
            }))
            return

        rec["decider"] = "broker:policy"
        rec["decided_at"] = now
        b.events[rid].set()
        b.audit({
            "ts": iso(now),
            "id": rid,
            "principal": rec["principal_key"],
            "capability": capability,
            "tool": tool,
            "decision": state,
            "decider": "broker:policy",
            "reason": reason,
            "trust_level": level,
            "latency_ms": 0,
        })
        self._send(200, self._public(rec))

    def _decide(self, rid):
        b = self.broker
        body = self._read_json()
        if body is None:
            return self._send(400, {"error": "bad_json"})
        if "allow" not in body:
            return self._send(400, {"error": "allow_required",
                                    "detail": "body must contain {allow: bool, decider}"})
        decider = body.get("decider")
        if not decider:
            return self._send(400, {"error": "decider_required",
                                    "detail": "a human decision must name its decider"})

        with b.lock:
            rec = b.requests.get(rid)
            if not rec:
                return self._send(404, {"error": "no_such_request", "id": rid})
            if rec["state"] != "pending":
                return self._send(409, {"error": "already_decided",
                                        "state": rec["state"],
                                        "decider": rec["decider"]})
            now = time.time()
            rec["state"] = "allow" if body["allow"] else "deny"
            rec["reason"] = "human_decision"
            rec["decider"] = str(decider)
            rec["decided_at"] = now
            latency = int((now - rec["created_at"]) * 1000)
            b.events[rid].set()

        b.audit({
            "ts": iso(now),
            "id": rid,
            "principal": rec["principal_key"],
            "capability": rec["capability"],
            "tool": rec["tool"],
            "decision": rec["state"],
            "decider": rec["decider"],
            "reason": "human_decision",
            "trust_level": rec["trust_level"],
            "latency_ms": latency,
        })
        self._send(200, self._public(rec))

    def _wait(self, rid, query):
        b = self.broker
        try:
            timeout = float((query.get("timeout") or [MAX_WAIT])[0])
        except ValueError:
            timeout = MAX_WAIT
        timeout = max(0.0, min(timeout, MAX_WAIT))

        with b.lock:
            rec = b.requests.get(rid)
            ev = b.events.get(rid)
        if not rec:
            return self._send(404, {"error": "no_such_request", "id": rid})

        ev.wait(timeout)

        with b.lock:
            if rec["state"] == "pending":
                # Fail closed. Nobody answered in time, so the answer is no.
                now = time.time()
                rec["state"] = "deny"
                rec["reason"] = "wait_timeout_fail_closed"
                rec["decider"] = "broker:timeout"
                rec["decided_at"] = now
                latency = int((now - rec["created_at"]) * 1000)
                ev.set()
                timed_out = True
            else:
                timed_out = False
                latency = int(((rec["decided_at"] or time.time())
                               - rec["created_at"]) * 1000)

        if timed_out:
            b.audit({
                "ts": iso(time.time()),
                "id": rid,
                "principal": rec["principal_key"],
                "capability": rec["capability"],
                "tool": rec["tool"],
                "decision": "deny",
                "decider": "broker:timeout",
                "reason": "wait_timeout_fail_closed",
                "trust_level": rec["trust_level"],
                "latency_ms": latency,
            })

        self._send(200, dict(self._public(rec), latency_ms=latency))


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + \
        ".%03dZ" % int((ts % 1) * 1000)


# --------------------------------------------------------------------------
# server construction
# --------------------------------------------------------------------------


def make_server(state_dir, port=DEFAULT_PORT):
    broker = Broker(state_dir)
    broker.trust()  # materialise trust.json on first start

    handler = type("BoundHandler", (Handler,), {"broker": broker})
    httpd = ThreadingHTTPServer((BIND_HOST, port), handler)
    httpd.daemon_threads = True
    return httpd, broker


def main(argv=None):
    ap = argparse.ArgumentParser(description="AOS approval broker (prototype)")
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)),
                    help="state directory holding trust.json, audit.jsonl, broker.key")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)

    httpd, broker = make_server(args.dir, args.port)
    actual = httpd.server_address[1]
    with open(os.path.join(broker.dir, "broker.port"), "w") as fh:
        fh.write(str(actual) + "\n")

    print("BROKER_PORT=%d" % actual, flush=True)
    print("[brokerd] contract=%s bind=%s:%d dir=%s" %
          (CONTRACT, BIND_HOST, actual, broker.dir), flush=True)
    print("[brokerd] trust=%s key=%s (0600)" %
          (broker.trust_path, broker.key_path), flush=True)

    def stop(signum, frame):
        print("[brokerd] shutting down on signal %s" % signum, flush=True)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
