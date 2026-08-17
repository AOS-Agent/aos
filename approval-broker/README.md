# broker-local — the AOS approval broker (prototype)

The smallest end-to-end loop the council asked for before any fork UI or
rebrand work: **one broker process, a stubbed no-op client, one hardcoded
trust level, one tool call that blocks on approval and one that auto-approves**
— plus the two properties the council added on top of that loop: it fails
closed, and a dead broker is a *rendered value*, not a silence.

Python 3 stdlib only. No dependencies, no install step. This directory becomes
an AOS framework service (`core/services/broker/`) once the shape is settled.

```
brokerd.py       the daemon — 127.0.0.1:4110, owns every trust decision
client_stub.py   the stubbed workspace client — no-op tool calls, renders liveness
trust.json       trust config (prototype copy of ~/.aos/config/trust.yaml)
demo.sh          the full ceremony, end to end
demo-output.txt  real captured output of ./demo.sh on this machine
test_broker.py   33 stdlib unittest cases
audit.jsonl      append-only decision log (created at first decision)
broker.key       auth key, minted 0600 at first start (created, never committed)
```

## Run it

```bash
./demo.sh                 # the whole ceremony, ~25s
python3 test_broker.py    # 33 tests, ~18s (several exercise real timeouts)
```

## The model

Every tool call an agent wants to make is an **approval request** carrying a
**principal** (`operator` / `member` / `agent` + id) and a **capability**
(`message.send`, `shell.exec`, …). The broker resolves a trust level for that
principal and answers `allow`, `deny`, or `pending`.

| Level | Name | Semantics |
|---|---|---|
| 0 | shadow | Record what would have happened. Allow nothing. |
| 1 | approval | Every call blocks on a human. |
| 2 | semi-auto | Auto-allow, except `always_escalate` capabilities and destructive calls. |
| 3 | full-auto | Allow, except `always_escalate` exceptions. |

Destructiveness is decided server-side (`destructive_capabilities`). A client
may *volunteer* that a call is destructive and that escalates it; a client
claiming a listed capability is harmless changes nothing.

### API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/approvals` | Ask. `{principal, capability, tool, summary, args_digest, destructive?}` → `200 allow/deny` or `202 pending`. Missing principal = `400`. |
| `GET` | `/v1/approvals/pending` | The human's queue. |
| `POST` | `/v1/approvals/{id}/decide` | `{allow: bool, decider}` resolves a pending request. Second decision = `409`. |
| `GET` | `/v1/approvals/{id}/wait?timeout=290` | Caller long-polls here. **Timeout = deny.** |
| `GET` | `/v1/liveness` | Self-describing state the client is contractually required to render. |

All routes require the `x-broker-key` header. The key is minted into
`./broker.key` (0600) on first start — the named local contract, not a shared
secret with upstream.

### Audit

One JSONL line per decision, appended and fsynced:

```json
{"ts":"2026-08-17T20:24:06.922Z","id":"330c776faa51","principal":"agent:workspace-agent-1",
 "capability":"shell.exec","tool":"workspace.terminal.run","decision":"allow",
 "decider":"operator:hisham","reason":"human_decision","trust_level":2,"latency_ms":246}
```

`decider` is always attributable: `broker:policy`, `broker:timeout`, or a named
human.

## Council locks — where each one lands

The "What to lock in before action" section of `council-verdict.md`, bullet by
bullet.

**1. Broker owns trust, and fails closed.** Implemented, in four places, because
one is not enough: an unreachable broker makes the client refuse before it asks
(`client_stub.py` liveness precheck) and refuse mid-flight if the broker dies
during a long poll; an unanswered approval times out into `deny` server-side
(`_wait`, reason `wait_timeout_fail_closed`); an unknown trust level denies
(`evaluate`); and an unparseable or missing `trust.json` collapses to level 0
rather than to "allow" (`Broker.trust`). Tests: `test_wait_timeout_is_a_denial`,
`test_unknown_level_fails_closed`, `test_broken_trust_file_fails_closed`,
`test_client_refuses_everything_when_broker_is_down`.

**2. Liveness is a rendered value, not an absence.** `GET /v1/liveness` returns
`schema`, `state`, `render.{label,detail,tone,must_render}` and a
`render_contract` string stating that absence *is* the DOWN state. The client
fetches and prints it before every run, and when the broker is unreachable it
**synthesises** a DOWN liveness object locally (`client_stub.down_state`) so
there is always a concrete value to render — "BROKER DOWN — nothing executes" —
instead of a missing field that defaults to normal. Step 9 of the demo is the
dreamer lens's texture test: with the broker killed, the workspace does not look
normal.

**3. Approvals key to principal plus capability.** Schema-level, as required: no
principal means `400 principal_required`, before any policy runs. Trust levels
resolve per principal (`principals: {"member:guest": {"level": 1}}`) over a
default, so an invited guest does not inherit the operator's reach — the
multi-tenant hole the skeptic lens raised. Every audit line is keyed
`kind:id`. Test: `test_decision_is_keyed_to_the_principal_not_just_capability`
sends the *same* capability as three principals and gets three different
answers.

**4. Loopback in the first fork commit.** `BIND_HOST = "127.0.0.1"`, not
configurable by flag; there is no `--host`. Asserted by
`test_bind_is_loopback_only`. The compose-bind half of this lock lives in the
fork's repo (E1/E3 territory), not here.

**5. Rebase gate as a standing test.** *Partly N/A at prototype stage.*
`test_broker.py` is the AOS-side half — it proves the broker's own properties
hold. The other half must live in the fork's CI, asserting against each pinned
upstream that (a) `BUZZ_ACP_PERMISSION_MODE` is not `bypass`, and (b) no code
path reaches a tool executor without a broker round-trip. That assertion cannot
be written until the fork exists and is pinned, so it is deliberately not
faked here.

**6. Broker built once, on the AOS side.** This is a standalone process in an
AOS-owned directory with zero upstream imports. Nothing in it is a patch, and
the client talks to it over HTTP on loopback with a key handshake — the "named
local contract" (`aos.broker.local/v1`).

**7. The end-to-end loop precedes all fork UI and rebrand work.** This directory
is that loop, and `demo-output.txt` is it running on the Mini. No fork UI code
was written to get here.

**8. Two abort conditions, both live.** Condition (2) — "with the broker killed,
the workspace still looks normal" — is now an executable check, not a judgement
call: `client_stub.py --broker-down` exits non-zero if anything executes, and
the rendered DOWN banner is what the operator sees. Condition (1) — "the broker
cannot be made mandatory without patching monorepo internals" — **cannot be
evaluated here.** It is a fact about the fork's permission plumbing, and it is
the one thing this prototype does *not* prove: the stub client asks the broker
because it was written to. Proving the real client *cannot* skip it is the
buzz-acp integration's job (see below), and until that lands, the broker is
demonstrated, not mandatory.

**9. Bundle-ID convergence stays a separate decision.** N/A — no code here
touches identifiers, signing, or install paths. Nothing in this prototype
creates pressure to converge.

## Mapping to the AOS instance layer

The prototype reads `./trust.json`. The framework service will read
`~/.aos/config/trust.yaml`, same shape:

| trust.json | trust.yaml | Notes |
|---|---|---|
| `default_level` | `broker.default_level` | 0–3. Fresh install defaults to 1, not 2. |
| `always_escalate` | `broker.always_escalate` | Capabilities that block at every level ≥ 1. |
| `destructive_capabilities` | `broker.destructive` | Server-authoritative destructive list. |
| `principals` | `broker.principals` | `kind:id` → `{level}`. Operator entry written at onboarding. |
| `./broker.key` | `~/.aos/run/broker.key` | 0600. Not Keychain: it is a file-scoped local capability, minted per machine and readable by the client process, not an account secret. |
| `./audit.jsonl` | `~/.aos/data/broker/audit.jsonl` | Append-only; rotation TBD. |

Per the component-lifecycle rule: **framework** ships `brokerd.py` and the
LaunchAgent; **instance** owns `trust.yaml`, `broker.key`, and `audit.jsonl`;
**runtime** with no `trust.yaml` present writes the level-1 default and starts —
missing config must be a locked door, never a skipped one.

## How this wires into buzz-acp later (reference only — not built here)

Not implemented in this prototype, stated so the seam is not re-derived:

1. The fork runs with `BUZZ_ACP_PERMISSION_MODE` **off** — no bypass, no
   default-allow. That setting is the fork's half of lock 5.
2. ACP's `session/request_permission` is forwarded to `POST /v1/approvals` with
   the workspace member mapped to a principal (`member:<id>`), the requested
   tool mapped to a capability, and the tool arguments hashed into
   `args_digest`. The ACP call blocks on `/wait` and returns the broker's
   answer verbatim.
3. The workspace chrome renders `/v1/liveness` continuously, not per call, so a
   broker that dies between calls is visible before the next one is attempted.
4. Any ACP permission path that can answer without a broker round-trip is a
   bug at the level of abort condition (1), and the rebase gate asserts against
   exactly that.

The 400–600 line / one week estimate from the builder lens went uncorroborated
in the council. For reference: this prototype is ~1470 lines total — 538 for
the daemon, 275 for the client, 470 of tests, 188 of demo. That is the easy
half; the ACP forwarding and the proof that the real client cannot skip the
broker are not included in it.
