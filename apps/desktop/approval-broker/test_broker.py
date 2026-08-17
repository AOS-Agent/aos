#!/usr/bin/env python3
"""Tests for the AOS approval broker. Stdlib unittest, no deps.

    python3 test_broker.py

Each test gets its own broker on an ephemeral loopback port with its own
temp state directory, so nothing here touches the demo's audit log.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import brokerd

AGENT = {"kind": "agent", "id": "workspace-agent-1"}


def request(port, method, path, body=None, key=None, timeout=15):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if key is not None:
        req.add_header("x-broker-key", key)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


class BrokerCase(unittest.TestCase):
    default_level = 2

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="brokertest-")
        self.write_trust(self.default_level)
        self.httpd, self.broker = brokerd.make_server(self.dir, 0)
        self.port = self.httpd.server_address[1]
        self.key = self.broker.key
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.dir, ignore_errors=True)

    # helpers ----------------------------------------------------------

    def write_trust(self, level, always_escalate=None, destructive=None,
                    principals=None):
        cfg = {
            "default_level": level,
            "always_escalate": always_escalate if always_escalate is not None
            else ["secrets.read"],
            "destructive_capabilities": destructive if destructive is not None
            else ["shell.exec"],
            "principals": principals or {},
        }
        with open(os.path.join(self.dir, "trust.json"), "w") as fh:
            json.dump(cfg, fh)

    def ask(self, capability="message.send", tool="workspace.chat.post",
            principal=AGENT, destructive=None, key="__default__"):
        body = {
            "principal": principal,
            "capability": capability,
            "tool": tool,
            "summary": "test call",
            "args_digest": "sha256:test",
        }
        if destructive is not None:
            body["destructive"] = destructive
        if principal is None:
            body.pop("principal")
        return request(self.port, "POST", "/v1/approvals", body,
                       key=self.key if key == "__default__" else key)

    def audit_lines(self):
        path = os.path.join(self.dir, "audit.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]


# ----------------------------------------------------------------------
# auth handshake
# ----------------------------------------------------------------------


class TestAuth(BrokerCase):
    def test_key_file_is_minted_0600(self):
        path = os.path.join(self.dir, "broker.key")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertGreaterEqual(len(self.key), 32)

    def test_rejected_without_header(self):
        status, body = request(self.port, "GET", "/v1/liveness")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")

    def test_rejected_with_wrong_key(self):
        status, _ = request(self.port, "GET", "/v1/liveness", key="nope")
        self.assertEqual(status, 401)

    def test_approvals_also_key_gated(self):
        status, _ = self.ask(key=None)
        self.assertEqual(status, 401)
        self.assertEqual(self.audit_lines(), [])


# ----------------------------------------------------------------------
# principal is mandatory (council lock: principal + capability keyed)
# ----------------------------------------------------------------------


class TestPrincipal(BrokerCase):
    def test_missing_principal_rejected(self):
        status, body = self.ask(principal=None)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "principal_required")

    def test_bad_kind_rejected(self):
        status, body = self.ask(principal={"kind": "robot", "id": "x"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "principal_required")

    def test_empty_id_rejected(self):
        status, _ = self.ask(principal={"kind": "agent", "id": ""})
        self.assertEqual(status, 400)

    def test_capability_and_tool_required(self):
        status, body = self.ask(capability="")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "capability_required")
        status, body = self.ask(tool="")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "tool_required")

    def test_decision_is_keyed_to_the_principal_not_just_capability(self):
        # Same capability, two principals, two different answers.
        self.write_trust(2, principals={"member:guest": {"level": 1},
                                        "operator:hisham": {"level": 3}})
        _, agent = self.ask(principal=AGENT)                       # level 2
        _, guest = self.ask(principal={"kind": "member", "id": "guest"})
        _, op = self.ask(principal={"kind": "operator", "id": "hisham"},
                         capability="shell.exec")
        self.assertEqual(agent["state"], "allow")
        self.assertEqual(guest["state"], "pending")
        self.assertEqual(op["state"], "allow")      # full-auto ignores destructive
        self.assertEqual(agent["principal_key"], "agent:workspace-agent-1")


# ----------------------------------------------------------------------
# trust level semantics
# ----------------------------------------------------------------------


class TestLevels(BrokerCase):
    def test_level0_shadow_denies_with_record(self):
        self.write_trust(0)
        status, body = self.ask()
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "deny")
        self.assertEqual(body["reason"], "shadow_mode_deny_with_record")
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["decision"], "deny")

    def test_level1_blocks_everything(self):
        self.write_trust(1)
        status, body = self.ask()
        self.assertEqual(status, 202)
        self.assertEqual(body["state"], "pending")
        self.assertEqual(body["reason"], "approval_required")
        # nothing is audited until the decision actually lands
        self.assertEqual(self.audit_lines(), [])

    def test_level2_auto_allows_benign(self):
        self.write_trust(2)
        _, body = self.ask(capability="message.send")
        self.assertEqual(body["state"], "allow")
        self.assertEqual(body["reason"], "semi_auto_allow")

    def test_level2_blocks_destructive(self):
        self.write_trust(2)
        _, body = self.ask(capability="shell.exec", tool="workspace.terminal.run")
        self.assertEqual(body["state"], "pending")
        self.assertEqual(body["reason"], "destructive_flag")
        self.assertTrue(body["destructive"])

    def test_level2_always_escalate_overrides_auto_allow(self):
        self.write_trust(2, always_escalate=["message.send"])
        _, body = self.ask(capability="message.send")
        self.assertEqual(body["state"], "pending")
        self.assertEqual(body["reason"], "capability_always_escalate")

    def test_level3_allows_but_still_escalates_exceptions(self):
        self.write_trust(3, always_escalate=["secrets.read"])
        _, ok = self.ask(capability="message.send")
        _, esc = self.ask(capability="secrets.read", tool="workspace.keychain.get")
        self.assertEqual(ok["state"], "allow")
        self.assertEqual(ok["reason"], "full_auto_allow")
        self.assertEqual(esc["state"], "pending")
        self.assertEqual(esc["reason"], "capability_always_escalate")

    def test_unknown_level_fails_closed(self):
        self.write_trust(9)
        _, body = self.ask()
        self.assertEqual(body["state"], "deny")
        self.assertEqual(body["reason"], "unknown_trust_level_fail_closed")

    def test_broken_trust_file_fails_closed(self):
        with open(os.path.join(self.dir, "trust.json"), "w") as fh:
            fh.write("{ this is not json")
        _, body = self.ask()
        self.assertEqual(body["state"], "deny")
        self.assertEqual(body["trust_level"], 0)

    def test_client_hint_can_escalate_but_never_de_escalate(self):
        self.write_trust(2, destructive=["shell.exec"])
        # server list is authoritative: client says "not destructive", still blocks
        _, lying = self.ask(capability="shell.exec", destructive=False)
        self.assertEqual(lying["state"], "pending")
        # client may volunteer that an unlisted capability is destructive
        _, honest = self.ask(capability="file.write", destructive=True)
        self.assertEqual(honest["state"], "pending")
        self.assertEqual(honest["reason"], "destructive_flag")

    def test_evaluate_matrix(self):
        cases = [
            (0, "message.send", False, "deny"),
            (0, "shell.exec", True, "deny"),
            (1, "message.send", False, "pending"),
            (1, "shell.exec", True, "pending"),
            (2, "message.send", False, "allow"),
            (2, "shell.exec", True, "pending"),
            (2, "secrets.read", False, "pending"),
            (3, "message.send", False, "allow"),
            (3, "shell.exec", True, "allow"),
            (3, "secrets.read", False, "pending"),
        ]
        for level, cap, destructive, expected in cases:
            state, _ = brokerd.evaluate(level, cap, destructive, ["secrets.read"])
            self.assertEqual(state, expected,
                             "level %s / %s / destructive=%s" % (level, cap, destructive))


# ----------------------------------------------------------------------
# decide + wait flow
# ----------------------------------------------------------------------


class TestDecideFlow(BrokerCase):
    def test_pending_list_then_approve_releases_waiter(self):
        self.write_trust(1)
        _, req = self.ask(capability="message.send")
        rid = req["id"]

        status, pending = request(self.port, "GET", "/v1/approvals/pending",
                                  key=self.key)
        self.assertEqual(status, 200)
        self.assertEqual(pending["count"], 1)
        self.assertEqual(pending["pending"][0]["id"], rid)
        self.assertEqual(pending["pending"][0]["principal"]["id"],
                         "workspace-agent-1")

        result = {}

        def waiter():
            _, result["body"] = request(
                self.port, "GET", "/v1/approvals/%s/wait?timeout=20" % rid,
                key=self.key, timeout=30)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.3)
        status, decided = request(self.port, "POST",
                                  "/v1/approvals/%s/decide" % rid,
                                  {"allow": True, "decider": "operator:hisham"},
                                  key=self.key)
        self.assertEqual(status, 200)
        self.assertEqual(decided["state"], "allow")
        t.join(timeout=25)
        self.assertFalse(t.is_alive(), "waiter never woke up")
        self.assertEqual(result["body"]["state"], "allow")
        self.assertEqual(result["body"]["decider"], "operator:hisham")

    def test_deny_decision_propagates(self):
        self.write_trust(1)
        _, req = self.ask()
        _, decided = request(self.port, "POST",
                             "/v1/approvals/%s/decide" % req["id"],
                             {"allow": False, "decider": "operator:hisham"},
                             key=self.key)
        self.assertEqual(decided["state"], "deny")
        _, waited = request(self.port, "GET",
                            "/v1/approvals/%s/wait?timeout=1" % req["id"],
                            key=self.key)
        self.assertEqual(waited["state"], "deny")

    def test_wait_timeout_is_a_denial(self):
        self.write_trust(1)
        _, req = self.ask()
        started = time.time()
        status, body = request(self.port, "GET",
                               "/v1/approvals/%s/wait?timeout=1" % req["id"],
                               key=self.key, timeout=15)
        elapsed = time.time() - started
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "deny")
        self.assertEqual(body["reason"], "wait_timeout_fail_closed")
        self.assertEqual(body["decider"], "broker:timeout")
        self.assertGreaterEqual(elapsed, 0.9)
        # and the denial is on the record
        self.assertEqual(self.audit_lines()[-1]["decision"], "deny")

    def test_timed_out_request_cannot_be_approved_afterwards(self):
        self.write_trust(1)
        _, req = self.ask()
        request(self.port, "GET", "/v1/approvals/%s/wait?timeout=1" % req["id"],
                key=self.key, timeout=15)
        status, body = request(self.port, "POST",
                               "/v1/approvals/%s/decide" % req["id"],
                               {"allow": True, "decider": "operator:hisham"},
                               key=self.key)
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "already_decided")

    def test_decide_requires_decider_and_allow(self):
        self.write_trust(1)
        _, req = self.ask()
        status, _ = request(self.port, "POST",
                            "/v1/approvals/%s/decide" % req["id"],
                            {"allow": True}, key=self.key)
        self.assertEqual(status, 400)
        status, _ = request(self.port, "POST",
                            "/v1/approvals/%s/decide" % req["id"],
                            {"decider": "operator:hisham"}, key=self.key)
        self.assertEqual(status, 400)

    def test_unknown_request_ids(self):
        status, _ = request(self.port, "GET", "/v1/approvals/deadbeef/wait?timeout=1",
                            key=self.key)
        self.assertEqual(status, 404)
        status, _ = request(self.port, "POST", "/v1/approvals/deadbeef/decide",
                            {"allow": True, "decider": "x"}, key=self.key)
        self.assertEqual(status, 404)


# ----------------------------------------------------------------------
# audit log
# ----------------------------------------------------------------------


class TestAudit(BrokerCase):
    REQUIRED = ("ts", "principal", "capability", "tool", "decision", "decider",
                "latency_ms")

    def test_auto_allow_writes_a_line_with_the_required_fields(self):
        self.write_trust(2)
        self.ask(capability="message.send")
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1)
        for field in self.REQUIRED:
            self.assertIn(field, lines[0])
        self.assertEqual(lines[0]["principal"], "agent:workspace-agent-1")
        self.assertEqual(lines[0]["capability"], "message.send")
        self.assertEqual(lines[0]["decision"], "allow")
        self.assertEqual(lines[0]["decider"], "broker:policy")

    def test_human_decision_records_decider_and_latency(self):
        self.write_trust(1)
        _, req = self.ask(capability="shell.exec", tool="workspace.terminal.run")
        time.sleep(0.15)
        request(self.port, "POST", "/v1/approvals/%s/decide" % req["id"],
                {"allow": True, "decider": "operator:hisham"}, key=self.key)
        line = self.audit_lines()[-1]
        self.assertEqual(line["decision"], "allow")
        self.assertEqual(line["decider"], "operator:hisham")
        self.assertGreaterEqual(line["latency_ms"], 100)
        self.assertEqual(line["capability"], "shell.exec")

    def test_log_is_append_only_across_requests(self):
        self.write_trust(2)
        for _ in range(3):
            self.ask()
        self.assertEqual(len(self.audit_lines()), 3)
        ids = {line["id"] for line in self.audit_lines()}
        self.assertEqual(len(ids), 3)


# ----------------------------------------------------------------------
# liveness
# ----------------------------------------------------------------------


class TestLiveness(BrokerCase):
    def test_shape_is_self_describing(self):
        status, body = request(self.port, "GET", "/v1/liveness", key=self.key)
        self.assertEqual(status, 200)
        for field in ("schema", "alive", "state", "uptime_s", "pending",
                      "trust_level", "trust_level_name", "fail_closed",
                      "render", "render_contract", "contract"):
            self.assertIn(field, body)
        self.assertTrue(body["alive"])
        self.assertEqual(body["state"], "UP")
        self.assertTrue(body["fail_closed"])
        self.assertTrue(body["render"]["must_render"])
        self.assertIn("BROKER UP", body["render"]["label"])
        self.assertIsInstance(body["uptime_s"], float)

    def test_pending_count_is_live(self):
        self.write_trust(1)
        self.ask()
        self.ask(capability="shell.exec")
        _, body = request(self.port, "GET", "/v1/liveness", key=self.key)
        self.assertEqual(body["pending"], 2)
        self.assertEqual(body["trust_level"], 1)
        self.assertEqual(body["trust_level_name"], "approval")

    def test_bind_is_loopback_only(self):
        self.assertEqual(brokerd.BIND_HOST, "127.0.0.1")
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")


# ----------------------------------------------------------------------
# the client stub's own fail-closed contract
# ----------------------------------------------------------------------


class TestClientFailClosed(unittest.TestCase):
    def test_client_refuses_everything_when_broker_is_down(self):
        import client_stub
        tmp = tempfile.mkdtemp(prefix="brokerclient-")
        try:
            with open(os.path.join(tmp, "broker.key"), "w") as fh:
                fh.write("irrelevant\n")
            # port 1 on loopback: nothing is listening there
            rc = client_stub.main(["--dir", tmp, "--port", "1", "--broker-down",
                                   "--timeout", "2"])
            self.assertEqual(rc, 0, "client must exit 0 only if nothing executed")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_down_state_is_a_rendered_value(self):
        import client_stub
        state = client_stub.down_state("connection refused")
        self.assertFalse(state["alive"])
        self.assertEqual(state["state"], "DOWN")
        self.assertTrue(state["render"]["must_render"])
        self.assertIn("nothing executes", state["render"]["label"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
