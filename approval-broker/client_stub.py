#!/usr/bin/env python3
"""client_stub.py — the stubbed no-op workspace client.

Stands in for the forked Buzz workspace app. It does no real work: every
"tool call" is a no-op print. What it does faithfully is the *shape* of the
loop the council asked for:

  1. fetch broker liveness and RENDER it (never treat absence as normal)
  2. ask the broker for permission, keyed by principal + capability
  3. block on a long poll while a human decides
  4. refuse to execute anything the broker did not explicitly allow

Fail-closed is proven, not asserted: with --broker-down the client exits
non-zero if it ever reaches the execute path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BAR = "=" * 66


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


class BrokerDown(Exception):
    """Any failure to get a clear answer out of the broker."""


class BrokerClient:
    def __init__(self, base, key, timeout=10):
        self.base = base.rstrip("/")
        self.key = key
        self.timeout = timeout

    def _call(self, method, path, body=None, timeout=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("x-broker-key", self.key or "")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode())
            except Exception:
                return exc.code, {"error": "http_%s" % exc.code}
        except Exception as exc:  # connection refused, timeout, DNS, TLS, ...
            raise BrokerDown(str(exc)) from exc

    def liveness(self):
        status, body = self._call("GET", "/v1/liveness")
        if status != 200:
            raise BrokerDown("liveness returned %s: %s" % (status, body))
        return body

    def ask(self, principal, capability, tool, summary, args_digest, destructive):
        return self._call("POST", "/v1/approvals", {
            "principal": principal,
            "capability": capability,
            "tool": tool,
            "summary": summary,
            "args_digest": args_digest,
            "destructive": destructive,
        })

    def wait(self, rid, timeout):
        return self._call("GET", "/v1/approvals/%s/wait?timeout=%d" % (rid, timeout),
                          timeout=timeout + 5)


# --------------------------------------------------------------------------
# rendering — liveness is a value that flows, not an absence
# --------------------------------------------------------------------------


def down_state(detail):
    """The DOWN liveness value. Synthesised locally so the client always has
    something concrete to render instead of silently defaulting to 'fine'."""
    return {
        "schema": "aos.broker.liveness/v1",
        "alive": False,
        "state": "DOWN",
        "pending": None,
        "trust_level": None,
        "fail_closed": True,
        "render": {
            "must_render": True,
            "label": "BROKER DOWN - nothing executes",
            "detail": detail,
            "tone": "blocking",
        },
    }


def render_liveness(live):
    r = live.get("render", {})
    print(BAR)
    print("  BROKER STATE : %s" % live.get("state", "UNKNOWN"))
    print("  %s" % r.get("label", "(no label)"))
    print("  %s" % r.get("detail", ""))
    if live.get("alive"):
        print("  uptime %ss | pending %s | trust level %s (%s) | fail-closed %s" % (
            live.get("uptime_s"), live.get("pending"),
            live.get("trust_level"), live.get("trust_level_name"),
            live.get("fail_closed")))
    print(BAR)


# --------------------------------------------------------------------------
# the two tool calls
# --------------------------------------------------------------------------

CALLS = [
    {
        "capability": "message.send",
        "tool": "workspace.chat.post",
        "summary": "Post 'standup notes are up' to #general",
        "args_digest": "sha256:2f1c9a...b40e",
        "destructive": False,
    },
    {
        "capability": "shell.exec",
        "tool": "workspace.terminal.run",
        "summary": "Run: rm -rf ~/project/scratch/build",
        "args_digest": "sha256:9ba77e...01cc",
        "destructive": True,
    },
]


def attempt(client, principal, call, wait_timeout, ledger):
    cap = call["capability"]
    print("\n-- tool call: %s (capability %s)%s" % (
        call["tool"], cap, "  [declared destructive]" if call["destructive"] else ""))
    print("   summary: %s" % call["summary"])

    try:
        status, body = client.ask(
            principal, cap, call["tool"], call["summary"],
            call["args_digest"], call["destructive"])
    except BrokerDown as exc:
        render_liveness(down_state("lost the broker while asking: %s" % exc))
        print("   REFUSED  reason=broker_unreachable (fail-closed)")
        ledger.append((cap, "refused", "broker_unreachable"))
        return "deny"

    if status not in (200, 202):
        print("   REFUSED  broker rejected the request: %s %s" % (status, body))
        ledger.append((cap, "refused", "broker_rejected"))
        return "deny"

    state = body.get("state")
    if state == "pending":
        print("   BLOCKED  awaiting human approval - request id %s (reason: %s)"
              % (body["id"], body.get("reason")))
        print("            approve with: POST /v1/approvals/%s/decide" % body["id"])
        try:
            _, body = client.wait(body["id"], wait_timeout)
        except BrokerDown as exc:
            render_liveness(down_state("broker vanished mid-approval: %s" % exc))
            print("   REFUSED  reason=broker_unreachable_during_wait (fail-closed)")
            ledger.append((cap, "refused", "broker_unreachable_during_wait"))
            return "deny"
        state = body.get("state")

    if state == "allow":
        print("   ALLOWED  by %s (%s)" % (body.get("decider"), body.get("reason")))
        print("   EXECUTED (no-op stub): %s" % call["tool"])
        ledger.append((cap, "executed", body.get("reason")))
        return "allow"

    print("   DENIED   by %s (%s)" % (body.get("decider"), body.get("reason")))
    print("   NOT EXECUTED")
    ledger.append((cap, "refused", body.get("reason")))
    return "deny"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def read_key(state_dir):
    path = os.path.join(state_dir, "broker.key")
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def read_port(state_dir, fallback=4110):
    path = os.path.join(state_dir, "broker.port")
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return fallback


def main(argv=None):
    ap = argparse.ArgumentParser(description="stubbed no-op workspace client")
    ap.add_argument("--dir", default=HERE)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--timeout", type=int, default=290,
                    help="long-poll seconds before a pending request fails closed")
    ap.add_argument("--principal", default="agent:workspace-agent-1",
                    help="kind:id, e.g. operator:hisham / member:guest / agent:foo")
    ap.add_argument("--broker-down", action="store_true",
                    help="proof mode: assert nothing executes while the broker is dead")
    ap.add_argument("--no-key", action="store_true",
                    help="proof mode: present no auth key (should be refused)")
    args = ap.parse_args(argv)

    kind, _, pid = args.principal.partition(":")
    principal = {"kind": kind, "id": pid}

    port = args.port or read_port(args.dir)
    key = None if args.no_key else read_key(args.dir)
    client = BrokerClient("http://127.0.0.1:%d" % port, key)

    print("\nWORKSPACE CLIENT (stub) - principal %s:%s" % (kind, pid))
    print("asking broker at http://127.0.0.1:%d  (contract aos.broker.local/v1)" % port)

    # Step 1: liveness is fetched and rendered BEFORE anything is attempted.
    try:
        live = client.liveness()
    except BrokerDown as exc:
        live = down_state("cannot reach the broker on 127.0.0.1:%d - %s" % (port, exc))

    render_liveness(live)

    ledger = []
    if not live.get("alive"):
        print("\nThe broker is not answering. This client has no local fallback,")
        print("no cached grant, and no bypass. Both tool calls are refused now.\n")
        for call in CALLS:
            print("   REFUSED  %s (%s) - broker down, nothing executes"
                  % (call["tool"], call["capability"]))
            ledger.append((call["capability"], "refused", "broker_down_precheck"))
    else:
        for call in CALLS:
            attempt(client, principal, call, args.timeout, ledger)

    executed = [c for c, outcome, _ in ledger if outcome == "executed"]
    print("\nEXECUTION LEDGER")
    for cap, outcome, reason in ledger:
        print("   %-14s %-9s %s" % (cap, outcome, reason))
    print("   executed: %d of %d" % (len(executed), len(CALLS)))

    if args.broker_down:
        if executed:
            print("\nFAIL-CLOSED PROOF: VIOLATED - %s ran with no broker" % executed)
            return 1
        print("\nFAIL-CLOSED PROOF: HELD - broker down, 0 of %d calls executed"
              % len(CALLS))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
