---
title: "aos-connect-broker — threat model"
date: 2026-08-17
type: security-note
status: draft-for-operator-review
---

# aos-connect-broker — threat model

The broker exists to move one secret off every user's machine and into one
place we control. That trade is worth stating precisely, including what it
costs.

## What the broker holds

One Composio project API key (`ak_…`), as a Cloudflare Worker secret. That key
is the only credential in the system with real blast radius: it can read, mint,
and delete connected accounts for **every** user of the project.

## What user machines hold

- An invite token (a bearer credential for the broker, nothing else).
- A machine id (a random UUID, no hardware linkage).
- Optionally, the user's *own* Composio key, on the BYO lane — which the broker
  never sees and which bypasses the broker entirely.

**Provider tokens — the Google refresh token, the Slack bot token, the GitHub
OAuth grant — never touch a user machine at all.** Composio's hosted auth page
completes the OAuth flow and stores the grant in the Composio project. The app
receives a redirect URL on the way in and a boolean on the way out. This is the
central property: a stolen laptop yields no provider access, because there is
no provider credential on the laptop to steal.

## If the worker is compromised

Compromise here means an attacker reads `COMPOSIO_API_KEY` — via a Cloudflare
account takeover, a malicious deploy, or a bug that echoes the secret.

They get: full control of the Composio project. Every user's connected accounts
— read, use, and delete. Through those accounts, whatever scope each user
granted: their Gmail, their Slack, their Drive. This is the worst case in the
system and it is a genuine centralisation cost of the design. The
counter-argument is not that it cannot happen, but that one hardened surface
under our control is defensible in a way that N laptops are not.

They do **not** get: anything on the user's Mac. No Keychain contents, no files,
no shell. The broker is a strictly outbound proxy with no channel back into the
app beyond the JSON responses the app expects.

Mitigations in place, and the ones that are not:

- The key is a Worker secret, never in `[vars]`, never in the repository, never
  in an error message. Composio auth failures are reported as *"rejected this
  server's credentials"* without echoing anything.
- `composio()` refuses any URL not on `backend.composio.dev`, so a routing bug
  cannot send the key to an attacker-chosen host.
- Not in place: key rotation on a schedule, and alerting on anomalous Composio
  usage. Both belong on the pre-launch list. Rotation is
  `wrangler secret put` plus a redeploy, with no client-side change.

## What a malicious client can do

Assume an attacker holds a valid invite token and can send arbitrary requests.

**Can:**

- Create Composio sessions and connect *their own* provider accounts. They are
  consuming our Composio quota, which is the real cost of a leaked token.
- Enumerate the public toolkit catalog. Not sensitive; it is Composio's
  published directory.
- Mint sign-in links for any toolkit, and disconnect the connections belonging
  to the machine ids they present.
- Pick arbitrary machine ids, and so occupy an unbounded number of derived user
  identities under one token. This is the abuse ceiling worth watching: the
  60/minute throttle slows it but does not bound the total.

**Cannot:**

- Read or infer the project key. It never appears in any response.
- Reach any Composio endpoint the worker does not name. Routing is exact-match
  with no catch-all; there is no path-forwarding proxy to abuse.
- Touch another user's connections without knowing that user's exact invite
  token *and* machine id — the derived user id is a one-way hash of both, and
  the worker accepts no user id from the client.
- Enumerate valid invite tokens beyond brute force. Tokens are 24 random bytes;
  the throttle applies per token, so a scan across many tokens is bounded by
  Cloudflare's own limits rather than ours. Worth revisiting if it ever becomes
  a real signal in the logs.
- Push a payload into the app. Every response field is constructed by the
  worker or copied from a whitelist, so a compromised Composio account cannot
  inject arbitrary JSON into the client.

## Why the user id is deterministic but opaque

`user_id = "aos_" + sha256(invite_token + ":" + machine_id)[:32]`

**Deterministic** so the worker holds no mapping table. There is no database
row linking a person to their connections, so there is no database row to leak,
subpoena, or corrupt. An app reinstall that preserves `~/.aos/.machine-id`
recovers the user's connections with no server-side recovery mechanism — and
therefore no recovery mechanism to attack.

**Opaque** so the value carries no email address, hostname, serial number, or
account name. What appears in the Composio dashboard is `aos_9f3c…` and nothing
more. We cannot tell who a given user is from the Composio side, which is the
intended level of knowledge: we run the plumbing, we do not hold the roster.

The hash includes the invite token, not just the machine id, so a machine id
guessed or reused elsewhere does not collide into someone's identity — you need
both halves, and one of them is a secret.

The costs of this choice, stated plainly: we cannot attribute Composio spend to
a particular invite, and a user who loses `~/.aos/.machine-id` loses access to
their existing connections with no way for us to restore them. They must
reconnect their services. Both follow directly from holding no mapping.

## Invite revocation semantics

Setting `status` to anything other than `"active"` blocks all new broker calls
for that token within about 60 seconds (KV propagation).

What revocation does **not** do: it does not revoke provider connections
already established. Those persist in the Composio project under the derived
user id, and the grants remain live at Google, Slack, and so on. A revoked user
loses the ability to *manage* their connections through us while those
connections keep working for anything that still holds the session.

If the intent is to fully cut someone off, the order is: delete their connected
accounts first (via the app's disconnect, or by `user_id` in the Composio
dashboard), then revoke the invite. Revoking first strands the accounts, since
the user can no longer reach the endpoint that would remove them.

This is a sharp edge and should be a documented step in whatever process issues
invites, not something rediscovered during an incident.

## Residual risks worth a decision

1. **Single key, all users.** Accepted deliberately; see above. Revisit if the
   user count reaches a scale where per-tenant Composio projects become viable.
2. **CORS is `*`.** The broker authenticates by header, not cookie, so a
   browser origin cannot borrow a user's credentials by making requests on
   their behalf. It is permissive to keep the browser demo path (`localhost:1420`)
   working. Tighten to an explicit allowlist if the app ever ships a web
   surface with real sessions.
3. **The invite token is a plain bearer credential.** It authenticates the
   install, not the human. Anyone who copies it can consume our Composio quota
   under new machine ids. Keep it in the Keychain, not in a config file — see
   `client-changes.md`.
4. **No audit trail per user.** Deliberate, and it means an abuse investigation
   has less to work with than usual. The `label` on each invite is the only
   handle you have; write useful ones.
