# Networking Conventions

**The rule: every AOS service binds `127.0.0.1`. No exceptions without a written one.**

Remote access is a separate, deliberate layer — never a side effect of how a
service happens to bind.

## Why this is a hard rule

AOS services have no authentication of their own. They assume the only thing
that can reach them is the machine they run on. Qareen is the clearest case: a
single port fronts `people.db` (1,148 contacts), `comms.db` (~248,000 WhatsApp /
iMessage / email messages), every task and project, and the vault API. There is
no login, no token, and it speaks plain HTTP.

For a while it bound `0.0.0.0`. On a home network that means every phone,
laptop, TV, and IoT device on the WiFi could read the operator's entire
communication history by opening a URL. That was found by measuring — a `curl`
from another machine on the LAN returned HTTP 200 with the full dashboard — not
by reading the code.

A wildcard bind is therefore not a performance or convenience decision. It is a
decision to publish whatever that service can read.

## Binding a new service

```python
uvicorn.run(app, host=os.environ.get("AOS_MYSVC_HOST", "127.0.0.1"), port=PORT)
```

Two properties matter:

1. **The default is loopback.** A fresh install with no configuration must be
   closed. Never rely on the operator to lock it down afterwards.
2. **The override is explicit.** An env var lets someone who genuinely wants a
   wider bind ask for it in a way that is visible and greppable.

If the service is a LaunchAgent, the plist is what actually decides. A CLI
argument beats the code default, so both have to be right:

```xml
<string>--host</string>
<string>127.0.0.1</string>
...
<key>EnvironmentVariables</key>
<dict>
    <key>AOS_MYSVC_HOST</key>
    <string>127.0.0.1</string>
</dict>
```

Fixing only the module default while the plist still passes `--host 0.0.0.0`
changes nothing. That is exactly how the Qareen bug survived a code review.

### Third-party services need their own homework

Do not assume an env var named `*_HOST` controls binding. n8n is the trap: its
plist set `N8N_HOST=127.0.0.1` and still listened on every interface, because
`N8N_HOST` only supplies the public hostname used to build editor and webhook
URLs. The bind address is `N8N_LISTEN_ADDRESS`, which defaults to `::`.

When wrapping an external tool, find the bind setting in *its* documentation or
source and verify with `lsof` that the process actually landed on loopback.

## Remote access

Three sanctioned front doors, all of which connect to `127.0.0.1`:

| Path | Reach | Notes |
|---|---|---|
| `tailscale serve` | Tailnet only | Default choice. `tailscale serve --bg --https=<port> 127.0.0.1:<port>` |
| Cloudflare Tunnel | Public hostname behind Cloudflare Access | Operator opt-in only, via the remote-access wizard. Email OTP + allow-list. |
| SSH port-forward | Ad hoc | Fine for debugging. |

**Never `tailscale funnel`.** Funnel publishes to the open internet. Putting an
unauthenticated dashboard behind Funnel is strictly worse than the LAN exposure
it would replace.

Prefer a dedicated serve port over a path prefix under `:443`. Single-page apps
request assets from absolute paths and break when mounted under a subpath.

## Declaring a deliberate exception

If a service really must accept non-local connections, say so in the instance
config rather than leaving a bare `0.0.0.0` for a future reader to interpret:

```yaml
# ~/.aos/config/network.yaml
allow_wildcard_bind:
  - label: com.aos.listen
    reason: Voice endpoint reached from the operator's iPhone over Tailscale
```

The reason is the point. A wildcard bind with a rationale is an engineering
decision; one without is a leak waiting to be found.

## Enforcement

`NetworkBindingCheck` (`core/infra/reconcile/checks/network_binding.py`) runs on
every reconcile cycle and reports:

- **live** wildcard listeners held by an AOS-managed launchd job or any of its
  children (uvicorn's reload worker holds the socket, so the job PID alone is
  not enough), and
- **declared** `0.0.0.0` in a deployed AOS plist — a service that is stopped now
  but will expose itself on next start.

What counts as AOS-managed is discovered from the filesystem: a `com.aos.*`
label, or any plist string pointing into the AOS tree. The operator's own
services are deliberately out of scope — flagging their intentionally-public
caddy sites would train everyone to ignore the check.

It is **report-only**. Rebinding means restarting a service, and doing that
unattended can sever remote access the operator depends on. Migration
`095_loopback_bind_hardening.py` is the supervised pattern to copy: provision
and *verify* the replacement path first, flip the bind second, and if continuity
cannot be established, say so loudly instead of silently taking access away.

To audit by hand:

```bash
lsof -nP -iTCP -sTCP:LISTEN | grep -v '127.0.0.1\|\[::1\]'
```
