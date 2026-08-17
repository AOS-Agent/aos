---
title: "Broker mode — client (Rust) changes"
date: 2026-08-17
type: implementation-notes
status: draft-for-operator-review
related: [connector-system-v2, aos-app]
---

# Broker mode — client changes

Target: `src-tauri/src/lib.rs`, the `── Composio ──` block (currently lines
~526–784). Nothing in `src/App.tsx` has to change for the happy path: every
command keeps its exact return shape. The UI work is additive — a mode
indicator, the trust banner, and the "Use your own Composio project" toggle.

## The two lanes

| Lane | When it is active | Where the credential lives |
|---|---|---|
| **Personal (BYO)** | Keychain has a non-empty `COMPOSIO_API_KEY` | User's own Composio project |
| **Broker** | No personal key, and a broker config resolves | Our Composio project, key held by the Cloudflare Worker |

**Personal key wins.** The check is ordered, not merged: if `composio_key()`
returns `Some`, every command behaves exactly as it does today and the broker
is never contacted. This keeps self-hosters entirely off our infrastructure and
makes "paste your own key" a complete escape hatch.

If neither lane resolves, commands fail with the same class of message they do
now ("Connect a service account first…").

## New configuration

### `~/.aos/config/app-broker.yaml`

```yaml
# Connection service (broker). Non-secret coordinates.
url: https://connect.example.com
```

The invite token is a bearer credential, so per the machine rule (*secrets:
macOS Keychain only*) it belongs in the Keychain:

```bash
agent-secret set AOS_INVITE_TOKEN itk_…
```

Resolution order for the token: Keychain `AOS_INVITE_TOKEN` first, then an
`invite_token:` field in `app-broker.yaml` as a fallback for machines
provisioned by a file drop. **Flag for the operator:** the file fallback puts a
credential on disk in plaintext. Recommend shipping only the Keychain path and
having the installer write the Keychain entry; keep the file field only if
zero-touch provisioning needs it, and chmod the file 0600 if so.

### `~/.aos/.machine-id`

Opaque, stable, per-machine. Generated on first use, `0600`, never leaves the
machine except as an auth header. It is not a hardware identifier — a lost file
means a new identity and a fresh (empty) connection set, which is the correct
failure mode.

## New helpers

```rust
// ── Broker lane ─────────────────────────────────────────────────────
//
// When the user has no Composio project of their own, connection calls
// go to our Cloudflare Worker instead. The worker holds the project key
// and scopes every call to a user id derived from (invite token, machine
// id), so this machine can only ever see its own connections.

struct BrokerCfg {
    url: String,
    token: String,
    machine: String,
}

fn broker_config_path(home: &str) -> String {
    format!("{home}/.aos/config/app-broker.yaml")
}

/// Stable opaque id for this install. Created on first use.
fn machine_id(home: &str) -> Result<String, String> {
    let path = format!("{home}/.aos/.machine-id");
    if let Ok(existing) = std::fs::read_to_string(&path) {
        let trimmed = existing.trim().to_string();
        if !trimmed.is_empty() {
            return Ok(trimmed);
        }
    }
    let id = cmd_ok("uuidgen", &[])
        .map(|u| u.trim().to_lowercase())
        .ok_or("could not generate a machine id")?;
    std::fs::write(&path, format!("{id}\n")).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(id)
}

/// Broker coordinates, or None when this machine is not invited.
fn broker_base(home: &str) -> Option<BrokerCfg> {
    let text = std::fs::read_to_string(broker_config_path(home)).ok()?;
    let v: serde_yaml::Value = serde_yaml::from_str(&text).ok()?;
    let url = v.get("url")?.as_str()?.trim().trim_end_matches('/').to_string();
    if !url.starts_with("https://") {
        return None;
    }
    let token = agent_secret(home, &["get", "AOS_INVITE_TOKEN"])
        .ok()
        .filter(|t| !t.is_empty())
        .or_else(|| v.get("invite_token").and_then(|t| t.as_str()).map(|t| t.trim().to_string()))
        .filter(|t| !t.is_empty())?;
    Some(BrokerCfg { url, token, machine: machine_id(home).ok()? })
}

/// One request to the broker. Both headers go through curl's stdin config,
/// never argv — the invite token is a credential.
fn broker_call(
    cfg: &BrokerCfg,
    method: &str,
    path: &str,
    body: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let url = format!("{}{}", cfg.url, path);
    let mut cmd = Command::new("curl");
    cmd.args(["-s", "-m", "35", "-X", method, &url, "--config", "-"]);
    if let Some(b) = body {
        cmd.args(["-H", "content-type: application/json", "--data-binary"]);
        cmd.arg(b.to_string());
    }
    cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    {
        use std::io::Write as _;
        let mut stdin = child.stdin.take().ok_or("no stdin")?;
        stdin
            .write_all(
                format!(
                    "header = \"x-invite-token: {}\"\nheader = \"x-machine-id: {}\"\n",
                    cfg.token, cfg.machine
                )
                .as_bytes(),
            )
            .map_err(|e| e.to_string())?;
    }
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if text.is_empty() {
        return Err("No response from the connection service — check your internet connection.".into());
    }
    let v: serde_json::Value =
        serde_json::from_str(&text).map_err(|_| format!("Unexpected reply: {}", &text[..text.len().min(200)]))?;
    // The broker's only failure shape is {"error": "plain language"}; no
    // success response carries that key.
    if let Some(message) = v.get("error").and_then(|e| e.as_str()) {
        return Err(message.to_string());
    }
    Ok(v)
}

/// Which lane is live. Personal key always wins.
enum Lane {
    Personal(String),
    Broker(BrokerCfg),
}

fn composio_lane(home: &str) -> Option<Lane> {
    if let Some(key) = composio_key(home) {
        return Some(Lane::Personal(key));
    }
    broker_base(home).map(Lane::Broker)
}
```

## Endpoint mapping

Every broker call carries `x-invite-token` and `x-machine-id`. The `session_id`
the worker returns is informational for the client — it is never sent back; the
worker resolves the session from the derived user id on every call.

| Command | Personal lane (unchanged) | Broker lane |
|---|---|---|
| `composio_setup` | `POST v3.1/tool_router/session` with `x-api-key` | n/a — replaced by `composio_broker_setup` |
| *(new)* `composio_broker_setup` | — | `POST {broker}/v1/session` |
| `composio_link(slug)` | `POST v3.1/tool_router/session/{id}/link` | `POST {broker}/v1/link` body `{"toolkit": slug}` |
| `composio_status(slugs)` | `GET v3.1/tool_router/session/{id}/toolkits?limit=50&toolkits=…` | `GET {broker}/v1/status?toolkits=a,b` |
| `composio_toolkits()` | `GET v3/toolkits?limit=500&sort_by=usage` | `GET {broker}/v1/toolkits` |
| `composio_disconnect(slug)` | list toolkits → `DELETE v3.1/connected_accounts/{id}?revoke_on_delete=true` | `DELETE {broker}/v1/connection/{slug}` |
| *(none)* | — | `GET {broker}/health` — unauthenticated liveness, for diagnostics only |

Session find-or-create, the 404-recreate dance, and the connected-accounts
enrichment all move **into the worker** on the broker lane. The client does not
cache a session id and `app-composio.yaml` is not written in broker mode.

## Command-by-command changes

### `composio_link`

```rust
#[tauri::command]
fn composio_link(slug: String) -> Result<String, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let url = match composio_lane(&home).ok_or("Connect a service account first.")? {
        Lane::Personal(key) => {
            let session = composio_ensure_session(&home, &key)?;
            let v = curl_json(
                "POST",
                &format!("{COMPOSIO_API}/tool_router/session/{session}/link"),
                &key,
                Some(serde_json::json!({ "toolkit": slug })),
            )?;
            v["redirect_url"].as_str().map(str::to_string)
        }
        Lane::Broker(cfg) => {
            let v = broker_call(&cfg, "POST", "/v1/link", Some(serde_json::json!({ "toolkit": slug })))?;
            v["redirect_url"].as_str().map(str::to_string)
        }
    }
    .ok_or_else(|| format!("No sign-in link returned for {slug}."))?;
    let _ = Command::new("open").arg(&url).spawn();
    Ok(url)
}
```

### `composio_status`

The broker already returns the finished map, so the broker arm is a passthrough
— no client-side normalisation, and the same `{connected, pending}` keys the UI
reads today (plus a `status` string the personal lane does not currently emit;
harmless, and worth adopting on both lanes later so the detail page can say
*why* something is pending).

```rust
Lane::Broker(cfg) => {
    let list = slugs.join(",");
    broker_call(&cfg, "GET", &format!("/v1/status?toolkits={list}"), None)
}
```

Guard the slug list the same way the worker does — lowercase, dedupe, cap at 40
— so a long connectors page cannot produce a 400.

### `composio_toolkits`

Broker arm calls `GET /v1/toolkits` and reads `cards`/`source` straight through.
**Keep the curated fallback**: on any `Err`, return the curated list with
`"source": "curated"` exactly as today. The connectors browse page must render
offline and while the broker is down.

### `composio_disconnect`

```rust
Lane::Broker(cfg) => {
    broker_call(&cfg, "DELETE", &format!("/v1/connection/{slug}"), None)?;
    Ok(())
}
```

The worker returns `{"removed": 0}` when nothing was connected — treat that as
success, matching today's `let Some(id) = id else { return Ok(()) }`.

### `composio_broker_setup` (new)

```rust
/// Store broker coordinates and prove them by opening a session.
#[tauri::command]
fn composio_broker_setup(url: String, invite_token: String) -> Result<(), String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let url = url.trim().trim_end_matches('/').to_string();
    if !url.starts_with("https://") {
        return Err("The connection service address must start with https://".into());
    }
    let token = invite_token.trim().to_string();
    if token.len() < 8 {
        return Err("That invite code looks too short.".into());
    }
    let cfg = BrokerCfg { url: url.clone(), token: token.clone(), machine: machine_id(&home)? };
    // Validate before persisting anything.
    broker_call(&cfg, "POST", "/v1/session", Some(serde_json::json!({})))?;
    agent_secret(&home, &["set", "AOS_INVITE_TOKEN", &token])?;
    std::fs::write(
        broker_config_path(&home),
        format!("# Connection service coordinates. Invite code lives in the Keychain.\nurl: {url}\n"),
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}
```

### `composio_mode` (new, for the UI)

```rust
/// Which lane the app is on, so the connectors page can be honest about it.
#[tauri::command]
fn composio_mode() -> serde_json::Value {
    let home = std::env::var("HOME").unwrap_or_default();
    match composio_lane(&home) {
        Some(Lane::Personal(_)) => serde_json::json!({ "mode": "personal", "ready": true }),
        Some(Lane::Broker(_)) => serde_json::json!({ "mode": "broker", "ready": true }),
        None => serde_json::json!({ "mode": "none", "ready": false }),
    }
}
```

Register `composio_broker_setup` and `composio_mode` in the
`tauri::generate_handler![…]` list alongside the existing composio commands
(currently ~line 2100).

## Connector list wiring

`connectors_list` currently marks the `composio` connector connected on
`has("COMPOSIO_API_KEY")` (~line 1070). It must become connected when *either*
lane resolves, with distinct detail copy:

- personal → "Using your own Composio project."
- broker → "Sign-ins are held by the connection service, not on this Mac."
- none → the existing "available" state, now offering *two* actions: enter an
  invite code, or paste your own project key.

## UI notes (App.tsx)

1. **Mode is visible, not inferred.** The connectors page reads `composio_mode`
   once and shows which lane is live. A user must never have to guess whose
   Composio project their sign-ins are sitting in.
2. **Trust banner on the broker lane** (spec §"Composio for every AOS user"):
   *"Sign-ins are held by the connection service, not on your Mac. You can
   revoke any app at any time."* Monochrome, per design law.
3. **"Use your own Composio project" toggle** reveals the existing `ak_` field.
   Because the personal key takes precedence, saving one immediately moves the
   app off the broker with no other state to clear — and clearing the key moves
   it back.
4. **Demo path**: guard both new commands behind `IN_TAURI` with demo data, so
   the connectors page still reviews at `localhost:1420`.

## Verification

- `cargo check` in `src-tauri/` and `bunx tsc --noEmit` both clean.
- With a personal key present, confirm with `wrangler tail` that the broker
  receives **zero** requests — precedence must be provable, not assumed.
- Revoke the invite in KV, then confirm the app surfaces the plain-language
  "This invite has been turned off" rather than a raw HTTP error. Allow up to
  a minute for KV propagation.
