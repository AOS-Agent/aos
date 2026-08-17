---
title: "aos-connect-broker — operations runbook"
date: 2026-08-17
type: runbook
status: draft-for-operator-review
---

# aos-connect-broker — operations

Deploys to the operator's personal Cloudflare account. Nothing here has been
run; every command is written to be executed in order.

## 0. Prerequisites

```bash
npm i -g wrangler          # v4.x
wrangler --version
wrangler login             # opens a browser; picks the account
wrangler whoami            # confirm the right account before anything else
```

Wrangler v3 used colons in these subcommands (`kv:namespace create`). Everything
below uses the v4 spellings.

## 1. Create the KV namespaces

```bash
cd /path/to/broker
wrangler kv namespace create INVITES
wrangler kv namespace create SESSIONS
wrangler kv namespace create MACHINES
wrangler kv namespace create INVITES  --preview
wrangler kv namespace create SESSIONS --preview
wrangler kv namespace create MACHINES --preview
```

Each command prints an id. Paste the six ids into the `[[kv_namespaces]]`
blocks in `wrangler.toml`, replacing the `REPLACE_WITH_…` placeholders.

`MACHINES` holds activation records — which handle belongs to which machine.
Unlike `SESSIONS` it is not a cache, so never bulk-delete it to "clear state".

The API token doing all this needs, at minimum, **Account → Workers Scripts →
Edit** and **Account → Workers KV Storage → Edit**. A DNS-only zone token gets
`Authentication error [code: 10000]` on every command in this section.

## 2. Put the Composio key in as a secret

```bash
wrangler secret put COMPOSIO_API_KEY
# paste the ak_… project key at the prompt, then Enter
```

Type it at the prompt — do not pass it on the command line, where it lands in
shell history and in `ps`. Verify it registered without printing it:

```bash
wrangler secret list        # shows the name only
```

If the key ever needs rotating, `wrangler secret put COMPOSIO_API_KEY` again
with the new value and redeploy; the old value is replaced atomically.

The admin credential goes in the same way. It is what stands between the
internet and the ability to mint invites, so it is generated once and kept in
the Keychain rather than anywhere on disk:

```bash
agent-secret get AOS_BROKER_ADMIN_TOKEN | wrangler secret put ADMIN_TOKEN
```

Until `ADMIN_TOKEN` is set, `/v1/admin/*` answers 503 to everyone — it fails
closed, never open.

## 3. Deploy

Run the test suite first — it needs no network, no wrangler, and no
credentials, and it covers auth, session reuse, path whitelisting, and key
leakage:

```bash
node test-worker.mjs      # expect "32 passed, 0 failed"
```

Then:

```bash
wrangler deploy
```

The worker answers at `https://aos-connect-broker.<subdomain>.workers.dev`.
Smoke test, in order:

```bash
BROKER=https://aos-connect-broker.<subdomain>.workers.dev

curl -s $BROKER/health
# {"ok":true,"service":"aos-connect-broker"}

curl -s $BROKER/v1/session -X POST
# {"error":"This app is not set up to use the connection service yet."}   ← 401, correct
```

`/health` answering while `/v1/session` refuses is the shape you want: the
worker is up and auth is on.

## 4. Mint and seed an invite token

Normally use the CLI, which mints through the worker and never puts the admin
credential on a command line:

```bash
./invite mint "hisham macbook"      # prints the token once
./invite check aos_inv_…            # Active / Revoked / Unknown
```

Set `AOS_BROKER_URL` if the worker is not at the default hostname.

The rest of this section is the manual path, for when the worker is down or the
admin token is unavailable.

Generate a token that is unguessable and URL-safe:

```bash
TOKEN="itk_$(openssl rand -hex 24)"
echo "$TOKEN"    # record it once — the KV value does not store it recoverably
```

Seed it:

```bash
wrangler kv key put --binding=INVITES --remote "$TOKEN" \
  '{"status":"active","label":"hisham macbook 2026-08-17"}'
```

`--remote` targets the deployed namespace rather than the local dev simulation.
The `label` is for your own audit trail — who or which machine this went to, and
when. It is never returned to the client.

List what is out there:

```bash
wrangler kv key list --binding=INVITES --remote
wrangler kv key get  --binding=INVITES --remote "$TOKEN"
```

End-to-end check with a real token:

```bash
curl -s $BROKER/v1/session -X POST \
  -H "x-invite-token: $TOKEN" \
  -H "x-machine-id: test-machine-0001"
# {"session_id":"…"}
```

Run it twice — the second call must return the **same** session id. If it does
not, the SESSIONS binding is wrong or the write failed.

## 5. Revoke an invite

```bash
./invite revoke aos_inv_…
```

That flips the record to `revoked` and keeps the label. Revoking now stops
updates as well as connections: `/v1/updater/latest.json` answers 403, so a
revoked machine stays on the version it has and never sees another release.

The manual equivalent, if the worker is unreachable:

```bash
wrangler kv key put --binding=INVITES --remote "$TOKEN" \
  '{"status":"revoked","label":"hisham macbook — revoked 2026-09-01"}'
```

Takes effect within roughly 60 seconds worldwide (KV propagation). The client
gets *"This invite has been turned off. Ask for a new one."*

Revoking the invite stops **new** broker calls. It does **not** revoke the
provider connections that were already made — those live in the Composio
project under the derived `user_id`. To also cut those, find the user's
connected accounts in the Composio dashboard by `user_id` and delete them, or
have the app call `DELETE /v1/connection/{slug}` before you revoke. Sequence
matters: revoke last.

Hard-delete an invite only when you also want the label gone:

```bash
wrangler kv key delete --binding=INVITES --remote "$TOKEN"
```

## 6. Attach a custom domain

The zone must be in the same Cloudflare account as the worker.

1. Pick the hostname (e.g. `connect.<domain>`).
2. Uncomment the `custom_domain = true` route block in `wrangler.toml` and set
   the pattern.
3. `wrangler deploy` — Cloudflare creates the DNS record and issues the
   certificate. Allow a few minutes for the certificate.
4. Verify: `curl -s https://connect.<domain>/health`.
5. Set `workers_dev = false` in `wrangler.toml` and redeploy, so there is one
   public entry point instead of two.
6. Update `url:` in each client's `~/.aos/config/app-broker.yaml`.

Do not put the broker behind Cloudflare Access — the client is a desktop app
sending headers, not a browser that can complete an Access login.

## 7. Watching it

```bash
wrangler tail                          # live request log
wrangler tail --status error           # failures only
```

`[observability]` is enabled in `wrangler.toml`, so logs are also queryable in
the dashboard under Workers → aos-connect-broker → Logs.

What to look for:

- A spike of 401s on one token — someone is probing, or a client is misconfigured.
- 502s mentioning *"rejected this server's credentials"* — the Composio key is
  bad or expired. This is the alert that matters most; it takes every user down
  at once.
- 504s — Composio is slow. Nothing to do on our side.

The worker deliberately logs no invite tokens, no machine ids, and no derived
user ids. If you need to trace one user's traffic, add a temporary log of the
first 8 characters of the derived user id and remove it afterwards.

## Limits

### Cloudflare — fine at this scale, with one caveat

| Free tier | Ceiling | Our usage |
|---|---|---|
| Worker requests | 100,000 / day | A few dozen per app session |
| Worker CPU | 10 ms / request | Well under — the worker is I/O bound, not compute |
| KV reads | 100,000 / day | ~2 per request |
| KV writes | **1,000 / day** | **1 per request on the KV rate-limit fallback** |
| KV storage | 1 GB | Kilobytes |

**The caveat is the KV write limit.** With the KV rate-limit fallback, every
authenticated request costs one write, so the free tier caps out around 1,000
broker requests per day across all users — fine for dogfood, not for launch.
Two ways out, and you should take the first:

1. Keep the `[[unsafe.bindings]]` rate-limit binding in `wrangler.toml`. The
   worker prefers it and then spends KV writes only on session creation
   (once per machine) and the daily TTL refresh — a handful per day total.
2. Move to Workers Paid ($5/month), which raises KV writes to 1 million/month.

Worth doing regardless before opening invites beyond a handful.

### Composio — verify before launch, not before dogfood

Unverified numbers; check them against the current plan page and the project's
usage dashboard, because this is where the real cost sits:

- **Tool-router sessions per project.** One session per user per machine. If
  sessions are capped or metered, that cap *is* our user cap. Ask support for
  the number in writing before public launch.
- **Connected accounts per project.** Same reasoning — every user connecting
  Gmail plus Slack is two accounts.
- **Request volume / rate limits per project key.** All our users share one key,
  so their limits are pooled. Composio 429s reach the app as *"the connection
  service is rate limiting us"*, which the user cannot fix.
- **Whether hosted-auth (the `link` flow) is metered separately** from tool
  calls.

Also unresolved: our Composio project holds provider OAuth grants on behalf of
every user. Confirm their data-processing terms permit that, and that deleting
a connected account with `revoke_on_delete=true` actually revokes at the
provider rather than only forgetting the token locally.

## Known limitations

- **Duplicate sessions.** Two simultaneous first-time requests from the same
  machine can each create a Composio session, because KV offers no atomic
  check-and-set. Harmless but wasteful. If session count ever becomes the
  metered resource, move the session cache to a Durable Object keyed by
  `user_id`, which makes it exactly-once.
- **Revocation lag.** KV propagation means a revoked invite can keep working for
  up to a minute.
- **No per-user usage accounting.** The worker cannot tell you which invite is
  responsible for Composio spend, because it deliberately stores no mapping.
  Adding one is possible but reintroduces the linkage the design removes — an
  explicit trade to make consciously.
