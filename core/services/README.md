# AOS Services

Each subdirectory here is one AOS service. Every service declares its identity
**once**, in a `service.yaml` manifest, and every consumer derives from it.

## Why the registry exists

Service identity used to be scattered across 6+ independently-drifting places:
the watchdog's hardcoded service list, `~/.aos/config/state.yaml`, per-check
port constants in the reconcile suite, the bridge heartbeat's probes, the
intent-classifier's service menu, and the `instance_hygiene` allowlist. Every
service incident in the aos#180 batch traced to that scatter — a transcriber
killed by a wrong-port (:7601 vs :7602) health check, a bridge health-probed on
an endpoint it doesn't serve, `listen` dead for three months because monitoring
derived from deployed plists instead of intent.

One declarative manifest per service kills that bug class: change a service's
port, health, or status in exactly one file and every consumer follows.

## The manifest — `service.yaml`

The loader (`core/infra/lib/service_registry.py`) discovers, strictly
validates, and returns manifests from:

- `core/services/*/service.yaml` — one per service directory (required; a guard
  test fails CI if a service dir has no manifest)
- `core/qareen/service.yaml` — qareen lives outside `core/services`
- `config/services.d/*.yaml` — services with a framework launchd presence but no
  code dir here (e.g. `n8n`, an external tool AOS wraps)

### Schema

| key | req? | values | notes |
|-----|------|--------|-------|
| `name` | ✓ | str | must match the directory name for `core/services` manifests |
| `purpose` | ✓ | str | one line |
| `status` | ✓ | `active` \| `optional` \| `retired` | see below |
| `type` | ✓ | `resident` \| `interval` \| `oneshot` | |
| `owner_layer` | ✓ | `framework` \| `instance` | ships in `~/aos` vs lives in `~/.aos` |
| `liveness` | ✓ | `http` \| `poll_timestamp` \| `keepalive` \| `interval` \| `none` | how liveness is judged |
| `port` | cond | int | **required if** `liveness: http`; may also be set informationally otherwise |
| `health_endpoint` | cond | str | **required if** `liveness: http` (e.g. `/health`); forbidden otherwise |
| `start_interval` | cond | int | **required if** `type: interval` (seconds) |
| `plist_template` | — | str \| null | basename in `config/launchagents/`, the literal `generated`, or null |
| `label` | — | str | launchd label; defaults to `com.aos.<name>` |
| `keepalive` | — | bool | whether the plist sets `KeepAlive` |
| `depends_on` | — | list[str] | services that must be up first |

Validation is **strict**: an unknown key, a missing required key, a bad enum, or
a cross-field violation makes the whole registry raise. A service declares
itself correctly or it does not ship.

### `status`

- **active** — should be deployed and monitored on every node. `service_loaded`
  enforces that an active resident is loaded (and, for `liveness: http`, healthy).
- **optional** — may be deployed (feature-gated, per-node, or an initiative
  still rolling out: `mesh`, `companion`, and the MCP-stdio servers `crawler`
  and `memory`). Never flagged as an orphan; never force-restarted if absent.
- **retired** — must **not** be loaded. Its directory is kept as an archive.
  Monitoring must never probe it or report it DOWN. Flipping a service to
  `retired` (and letting consumers derive) is how `listen` and `eventd` were
  retired — see below.

### `liveness`

- **http** — probe `http://127.0.0.1:<port><health_endpoint>`.
- **poll_timestamp** — a heartbeat file, not an HTTP endpoint. The **bridge**
  uses this: its liveness is the Telegram `getUpdates` poll loop, and
  `BridgePollLivenessCheck` owns wedge detection. Its `:4098` aiohttp API is
  optional (it was *missing* during aos#180) and must never be the liveness
  signal — so `service_loaded` only asserts the bridge job is loaded.
- **keepalive** — the launchd `KeepAlive` restart is the only signal;
  "loaded" is enough (`companion` — a resident with no `/health` route).
- **interval** — a periodic job; "loaded" is enough.
- **none** — no out-of-band signal (the MCP-stdio servers, launched by a client).

## Interval and calendar crons

Not everything with a plist is a resident service:

- **`config/services.d/*.yaml` with `type: interval`** is where a framework
  interval job that warrants registry tracking declares itself (`start_interval`
  in seconds).
- **`com.aos.scheduler`** is a *calendar* cron (`StartCalendarInterval`, every
  5 min) — it is declared by its plist, not the registry, and is intentionally
  out of the resident-monitoring set.
- **`slack-watch`** is an instance-layer (`~/.aos/services/slack-watch`)
  single-shot poller (`StartInterval` + `RunAtLoad`, no `KeepAlive`). It is
  declared by its own instance plist; "exits immediately" is its by-design
  behavior, not a death. It is not — and must not be — monitored as a resident.

## Consumers that derive from the registry

`service_registry.load_registry()` (and helpers `active_residents()`,
`active_health_urls()`, `by_label()`, `ports()`) is read by:

- `core/infra/reconcile/checks/service_loaded.py` — enforces active residents
  loaded/healthy; treats interval/keepalive/poll as loaded-is-enough; flags a
  retired service that is still loaded.
- `core/bin/crons/watchdog` — reads `~/.aos/config/state.yaml`, which migration
  `083` regenerates **from the registry** (active + deployed only).
- `core/services/bridge/heartbeat.py` and `intent_classifier.py` — the service
  summary and the "check services" menu.
- `core/infra/reconcile/checks/context_freshness.py`, `transcriber.py`, `n8n.py`
  — ports and health URLs.
- `core/infra/reconcile/checks/instance_hygiene.py` — an `active`/`optional`
  service dir is never an orphan; a `retired` dir is expected-archived.
- `core/infra/lib/service_ctl.py` — its `KNOWN_HEALTH_URLS` is derived from the
  registry (the choke-point no longer hardcodes ports).

Inspect the registry directly:

```bash
aos-python core/infra/lib/service_registry.py list      # table of all services
aos-python core/infra/lib/service_registry.py validate  # strict schema check
aos-python core/infra/lib/service_registry.py health    # name → health URL
```
