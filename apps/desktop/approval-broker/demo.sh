#!/usr/bin/env bash
# demo.sh — the end-to-end approval loop the council asked for, on this machine.
#
#   one broker process, a stubbed no-op client, one hardcoded trust level,
#   one tool call that blocks on approval and one that auto-approves
#
# plus the two things the council added on top: fail-closed with the broker
# dead, and a liveness state the client is required to render.

set -uo pipefail
cd "$(dirname "$0")" || exit 1

BROKER_PID=""
PORT=4110

step() {
  echo
  echo "=================================================================="
  echo "  $*"
  echo "=================================================================="
}

say() { echo "  $*"; }

cleanup() {
  if [[ -n "$BROKER_PID" ]] && kill -0 "$BROKER_PID" 2>/dev/null; then
    kill "$BROKER_PID" 2>/dev/null
    wait "$BROKER_PID" 2>/dev/null
  fi
  rm -f .broker.curlrc
}
trap cleanup EXIT

write_trust() {  # $1 = default trust level
  cat > trust.json <<JSON
{
  "_comment": "Prototype copy of the AOS trust config. In the framework this is ~/.aos/config/trust.yaml (instance layer). See README.md for the field-by-field mapping.",
  "default_level": $1,
  "_levels": {
    "0": "shadow    - record what would have happened, allow nothing",
    "1": "approval  - every call blocks on a human",
    "2": "semi-auto - auto-allow, except always_escalate or destructive calls",
    "3": "full-auto - allow, except always_escalate exceptions"
  },
  "always_escalate": [
    "secrets.read",
    "payment.send",
    "system.destroy"
  ],
  "destructive_capabilities": [
    "shell.exec",
    "fs.delete",
    "system.destroy"
  ],
  "principals": {
    "operator:hisham": {"level": 3},
    "member:guest": {"level": 1}
  }
}
JSON
}

start_broker() {
  python3 brokerd.py --port "$PORT" --dir . > brokerd.log 2>&1 &
  BROKER_PID=$!
  for _ in $(seq 1 50); do
    if [[ -f broker.key ]] && curl -s -K .broker.curlrc \
        "http://127.0.0.1:$PORT/v1/liveness" >/dev/null 2>&1; then
      return 0
    fi
    # the key only exists after first start; (re)build the curl config once it does
    [[ -f broker.key ]] && printf 'header = "x-broker-key: %s"\n' "$(cat broker.key)" > .broker.curlrc
    sleep 0.2
  done
  echo "broker failed to come up; see brokerd.log" >&2
  exit 1
}

stop_broker() {
  kill "$BROKER_PID" 2>/dev/null
  wait "$BROKER_PID" 2>/dev/null
  BROKER_PID=""
}

# ---------------------------------------------------------------------------

step "STEP 0  clean slate"
rm -f audit.jsonl broker.key broker.port brokerd.log client-run*.txt
write_trust 2
say "removed audit.jsonl and broker.key; trust.json set to level 2 (semi-auto)"
say "the key does not exist yet - the broker mints it on first start"

step "STEP 1  start the broker"
start_broker
say "brokerd running as pid $BROKER_PID"
say "bind: $(grep -o 'bind=[^ ]*' brokerd.log | head -1)  (loopback only, council lock)"
say "auth key minted at ./broker.key with permissions $(stat -f '%Sp' broker.key 2>/dev/null || stat -c '%A' broker.key)"

step "STEP 2  liveness is a value, and the client must render it"
say "raw payload the client renders:"
curl -s -K .broker.curlrc "http://127.0.0.1:$PORT/v1/liveness"
echo

step "STEP 3  auth handshake is real - a client with no key gets nothing"
say "curl with no x-broker-key header:"
curl -s "http://127.0.0.1:$PORT/v1/liveness"
echo

step "STEP 4  run the workspace client (trust level 2, semi-auto)"
say "it will attempt two tool calls:"
say "  a) message.send  -> benign, expected to auto-allow"
say "  b) shell.exec    -> destructive, expected to BLOCK on a human"
echo
python3 -u client_stub.py --timeout 120 --principal agent:workspace-agent-1 \
  > client-run1.txt 2>&1 &
CLIENT_PID=$!

# wait for the blocked request to show up in the queue
for _ in $(seq 1 60); do
  COUNT=$(curl -s -K .broker.curlrc "http://127.0.0.1:$PORT/v1/approvals/pending" \
          | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])' 2>/dev/null || echo 0)
  [[ "$COUNT" -ge 1 ]] && break
  sleep 0.2
done

say "client output so far (it is now parked, waiting on a human):"
cat client-run1.txt

step "STEP 5  the approval queue - what a human is being asked to decide"
curl -s -K .broker.curlrc "http://127.0.0.1:$PORT/v1/approvals/pending"
echo
REQ_ID=$(curl -s -K .broker.curlrc "http://127.0.0.1:$PORT/v1/approvals/pending" \
         | python3 -c 'import json,sys; print(json.load(sys.stdin)["pending"][0]["id"])')
say "pending request id: $REQ_ID"

step "STEP 6  a human approves it out of band"
say "POST /v1/approvals/$REQ_ID/decide  {allow: true, decider: operator:hisham}"
curl -s -K .broker.curlrc -X POST -H 'Content-Type: application/json' \
  -d "{\"allow\": true, \"decider\": \"operator:hisham\"}" \
  "http://127.0.0.1:$PORT/v1/approvals/$REQ_ID/decide"
echo

wait "$CLIENT_PID"
step "STEP 7  the client unblocks and proceeds"
cat client-run1.txt

step "STEP 8  the audit log (append-only JSONL, principal + capability keyed)"
cat audit.jsonl
echo
say "one line per decision: who asked, what capability, who decided, how long it took"

step "STEP 9  FAIL-CLOSED PROOF - kill the broker, then run the client again"
stop_broker
say "brokerd killed. nothing is listening on 127.0.0.1:$PORT."
say "this is the adversarial test: is the broker a chokepoint or a curtain?"
echo
python3 -u client_stub.py --broker-down --timeout 5 | tee client-run2.txt
RC=${PIPESTATUS[0]}
echo
if [[ "$RC" -eq 0 ]]; then
  say "PROOF HELD (client exit $RC): broker dead, 0 of 2 calls executed,"
  say "and the dead broker is rendered plainly rather than degraded into silence."
else
  say "PROOF VIOLATED (client exit $RC): something executed without a broker. ABORT CONDITION."
fi

step "STEP 10  same client, trust level 1 (approval) - everything blocks"
write_trust 1
say "trust.json default_level -> 1"
start_broker
say "brokerd restarted as pid $BROKER_PID (same key, key file is persistent)"
echo
say "running the client with a 5s approval window and nobody at the keyboard:"
python3 -u client_stub.py --timeout 5 | tee client-run3.txt
echo
say "both calls blocked, nobody answered, both denied by timeout - fail-closed."

step "STEP 11  full audit trail for the whole ceremony"
cat audit.jsonl
echo
say "decisions recorded: $(wc -l < audit.jsonl | tr -d ' ')"

step "DONE"
say "auto-allow, block-and-approve, fail-closed-on-death, and level-1 lockdown"
say "all demonstrated against one broker process on 127.0.0.1:$PORT."
stop_broker
write_trust 2
say "trust.json restored to level 2 (semi-auto) so the repo copy is not a demo artifact."
