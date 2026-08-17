use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};
use tauri::Emitter;

#[derive(serde::Serialize, Default)]
struct SystemInfo {
    installed: bool,
    version: Option<String>,
    operator: Option<String>,
    update_status: Option<String>, // up_to_date | update_available | ...
    last_check: Option<String>,
    /// The deployed release id (symlink target of ~/aos, e.g. "v0.7.5-cc89243").
    /// Unlike VERSION, this CHANGES on every applied update — it is the value
    /// that can actually answer "did my update apply".
    release: Option<String>,
    /// When the current release was deployed, human-readable.
    updated: Option<String>,
}

fn read_system_info() -> SystemInfo {
    let home = match std::env::var("HOME") {
        Ok(h) => h,
        Err(_) => return SystemInfo::default(),
    };

    let version = std::fs::read_to_string(format!("{home}/aos/VERSION"))
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty());

    let installed = version.is_some();

    // First name from operator.yaml's `name:` line (good enough — no YAML dep).
    let operator = std::fs::read_to_string(format!("{home}/.aos/config/operator.yaml"))
        .ok()
        .and_then(|y| {
            y.lines()
                .find(|l| l.starts_with("name:"))
                .map(|l| l.trim_start_matches("name:").trim().trim_matches('"').to_string())
        })
        .and_then(|full| full.split_whitespace().next().map(str::to_string))
        .filter(|n| !n.is_empty());

    let (update_status, last_check) =
        match std::fs::read_to_string(format!("{home}/.aos/data/update-state.json"))
            .ok()
            .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        {
            Some(v) => (
                v["status"].as_str().map(str::to_string),
                v["last_check"].as_str().map(str::to_string),
            ),
            None => (None, None),
        };

    // Release identity: ~/aos is a symlink to aos-releases/<release-id>.
    let (release, updated) = std::fs::read_link(format!("{home}/aos"))
        .ok()
        .map(|target| {
            let id = target
                .file_name()
                .map(|f| f.to_string_lossy().to_string())
                .unwrap_or_default();
            let when = std::fs::metadata(&target)
                .or_else(|_| std::fs::metadata(format!("{home}/aos")))
                .ok()
                .and_then(|m| m.modified().ok())
                .map(|t| {
                    let secs = t
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_secs() as i64)
                        .unwrap_or(0);
                    epoch_to_date(secs)
                });
            (Some(id).filter(|s| !s.is_empty()), when)
        })
        .unwrap_or((None, None));
    SystemInfo { installed, version, operator, update_status, last_check, release, updated }
}

/// Fast, local-only detection: is the system installed, what version, who's
/// the operator, and what did the last update check conclude?
#[tauri::command]
fn detect_system() -> SystemInfo {
    read_system_info()
}

/// Refreshes the update check against the remote (runs `check-update`),
/// then returns the new state. Blocking — call from the frontend async.
#[tauri::command]
fn check_updates() -> Result<SystemInfo, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let script = format!("{home}/aos/core/bin/check-update");
    if std::path::Path::new(&script).exists() {
        let _ = Command::new("bash")
            .arg(&script)
            .env("TERM", "dumb")
            .env("NO_COLOR", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .map_err(|e| format!("update check failed to start: {e}"))?;
    }
    Ok(read_system_info())
}

/// Applies a pending update (`check-update --apply`), streaming output lines
/// as `update:line` events and the exit code as `update:done`.
#[tauri::command]
fn run_update(app: tauri::AppHandle) -> Result<(), String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let script = format!("{home}/aos/core/bin/check-update");
    if !std::path::Path::new(&script).exists() {
        return Err("Update tool not found — is the system installed?".into());
    }

    let mut child = Command::new("bash")
        .arg(&script)
        .arg("--apply")
        .env("TERM", "dumb")
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to start update: {e}"))?;

    let stdout = child.stdout.take().ok_or("no stdout")?;
    let stderr = child.stderr.take().ok_or("no stderr")?;

    let app_out = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let _ = app_out.emit("update:line", line);
        }
    });
    let app_err = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = app_err.emit("update:line", line);
        }
    });
    std::thread::spawn(move || {
        let code = child.wait().ok().and_then(|s| s.code()).unwrap_or(-1);
        let _ = app.emit("update:done", code);
    });

    Ok(())
}

#[derive(serde::Serialize, Clone)]
struct Check {
    id: String,
    label: String,
    status: String, // ok | warn | fail
    detail: String,
}

fn check(id: &str, label: &str, status: &str, detail: String) -> Check {
    Check { id: id.into(), label: label.into(), status: status.into(), detail }
}

fn cmd_ok(bin: &str, args: &[&str]) -> Option<String> {
    Command::new(bin)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
}

/// Run a command and keep everything it said: `(succeeded, stdout, stderr)`.
/// `cmd_ok` throws the exit code and stderr away, which is fine until a CLI
/// answers on the wrong stream — Codex prints its sign-in state to stderr and
/// Tailscale prints a version-skew warning there alongside clean JSON.
fn run_out(bin: &str, args: &[&str]) -> Option<(bool, String, String)> {
    Command::new(bin)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .ok()
        .map(|o| {
            (
                o.status.success(),
                String::from_utf8_lossy(&o.stdout).trim().to_string(),
                String::from_utf8_lossy(&o.stderr).trim().to_string(),
            )
        })
}

/// Absolute path to a CLI, looked for in the places a windowed app can't see.
/// A Tauri app launched from the Dock inherits a bare PATH, so trusting
/// `which` alone would report half the operator's tools as missing. Extra
/// candidates are full paths, tried first.
fn resolve_bin(home: &str, name: &str, extra: &[String]) -> Option<String> {
    for path in extra {
        if std::path::Path::new(path).exists() {
            return Some(path.clone());
        }
    }
    let dirs = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        &format!("{home}/.local/bin"),
        &format!("{home}/.bun/bin"),
        &format!("{home}/.cargo/bin"),
    ];
    for dir in dirs {
        let path = format!("{dir}/{name}");
        if std::path::Path::new(&path).exists() {
            return Some(path);
        }
    }
    cmd_ok("which", &[name])
        .and_then(|out| out.lines().next().map(str::to_string))
        .filter(|p| !p.is_empty())
}

// ── Setup config persistence ────────────────────────────────────────

/// Persist the Configuration screen's answers where the installer and
/// onboarding can consume them.
#[tauri::command]
fn save_setup_config(
    operator_name: String,
    machine_name: String,
    role: String,
) -> Result<(), String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let dir = format!("{home}/.aos/config");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let yaml = format!(
        "# Written by the desktop app's Configuration screen.\n\
         # Consumed by install.sh scaffolding and onboarding.\n\
         schema: 1\n\
         operator_name: {}\n\
         machine_name: {}\n\
         role: {}\n",
        serde_yaml::to_string(&operator_name).map_err(|e| e.to_string())?.trim(),
        serde_yaml::to_string(&machine_name).map_err(|e| e.to_string())?.trim(),
        serde_yaml::to_string(&role).map_err(|e| e.to_string())?.trim(),
    );
    std::fs::write(format!("{dir}/app-setup.yaml"), yaml).map_err(|e| e.to_string())
}

/// Read back the saved setup answers for the Configuration pane.
#[tauri::command]
fn load_setup_config() -> Option<std::collections::BTreeMap<String, String>> {
    let home = std::env::var("HOME").ok()?;
    let text = std::fs::read_to_string(format!("{home}/.aos/config/app-setup.yaml")).ok()?;
    let value: serde_yaml::Value = serde_yaml::from_str(&text).ok()?;
    let map = value.as_mapping()?;
    Some(
        map.iter()
            .filter_map(|(k, v)| {
                Some((
                    k.as_str()?.to_string(),
                    match v {
                        serde_yaml::Value::String(s) => s.clone(),
                        other => serde_yaml::to_string(other).ok()?.trim().to_string(),
                    },
                ))
            })
            .collect(),
    )
}

// ── Home data ───────────────────────────────────────────────────────

#[derive(serde::Serialize)]
struct TaskRow {
    id: String,
    title: String,
    urgent: bool,
}

#[derive(serde::Serialize)]
struct ServiceRow {
    name: String,
    label: String,
}

#[derive(serde::Serialize)]
struct ActivityRow {
    title: String,
    when: String,
}

#[derive(serde::Serialize)]
struct HomeData {
    tasks: Vec<TaskRow>,
    services: Vec<ServiceRow>,
    activity: Vec<ActivityRow>,
}

fn parse_task_line(line: &str) -> Option<TaskRow> {
    let trimmed = line.trim();
    // lines look like: "!! quran-garden#78  Title text [project] [707s] *"
    let urgent = trimmed.starts_with('!');
    let rest = trimmed.trim_start_matches('!').trim();
    let (id, title_part) = rest.split_once(char::is_whitespace)?;
    if !id.contains('#') {
        return None;
    }
    // strip trailing "[...]" annotations and "*"
    let mut title = title_part.trim().to_string();
    while let Some(idx) = title.rfind('[') {
        if title[idx..].contains(']') && idx > 0 {
            title.truncate(idx);
            title = title.trim().trim_end_matches('*').trim().to_string();
        } else {
            break;
        }
    }
    let title = title.trim_end_matches('*').trim().trim_end_matches(':').to_string();
    Some(TaskRow { id: id.trim_end_matches(':').to_string(), title, urgent })
}

#[tauri::command]
fn home_data() -> Result<HomeData, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;

    // Tasks — the work engine's own "today" view.
    let tasks = cmd_ok(
        "python3",
        &[&format!("{home}/aos/core/engine/work/cli.py"), "today"],
    )
    .map(|out| {
        out.lines()
            .filter_map(parse_task_line)
            .take(6)
            .collect::<Vec<_>>()
    })
    .unwrap_or_default();

    // Services — loaded launchd agents in the system's namespaces.
    let services = loaded_launchd_labels()
        .into_iter()
        .filter(|l| l.starts_with("com.aos.") || l.starts_with("com.agent."))
        .map(|label| ServiceRow {
            name: label
                .rsplit('.')
                .next()
                .unwrap_or(&label)
                .replace('-', " "),
            label,
        })
        .collect::<Vec<_>>();

    // Activity — newest knowledge documents in the vault.
    let mut docs: Vec<(std::time::SystemTime, String)> = Vec::new();
    for sub in ["specs", "captures", "research", "synthesis", "decisions", "references"] {
        let dir = format!("{home}/vault/knowledge/{sub}");
        if let Ok(entries) = std::fs::read_dir(&dir) {
            for e in entries.flatten() {
                if let Ok(meta) = e.metadata() {
                    if meta.is_file() {
                        if let Ok(modified) = meta.modified() {
                            let title = std::fs::read_to_string(e.path())
                                .ok()
                                .and_then(|t| {
                                    t.lines()
                                        .take(12)
                                        .find(|l| l.starts_with("title:"))
                                        .map(|l| {
                                            l.trim_start_matches("title:")
                                                .trim()
                                                .trim_matches('"')
                                                .to_string()
                                        })
                                })
                                .unwrap_or_else(|| {
                                    e.file_name().to_string_lossy().trim_end_matches(".md").to_string()
                                });
                            docs.push((modified, title));
                        }
                    }
                }
            }
        }
    }
    docs.sort_by(|a, b| b.0.cmp(&a.0));
    let now = std::time::SystemTime::now();
    let activity = docs
        .into_iter()
        .take(4)
        .map(|(t, title)| {
            let secs = now.duration_since(t).map(|d| d.as_secs()).unwrap_or(0);
            let when = if secs < 3600 {
                format!("{}m ago", secs / 60)
            } else if secs < 86_400 {
                format!("{}h ago", secs / 3600)
            } else {
                format!("{}d ago", secs / 86_400)
            };
            ActivityRow { title, when }
        })
        .collect();

    Ok(HomeData { tasks, services, activity })
}

/// Fast vault search (BM25, no LLM) — returns qmd's raw output text.
#[tauri::command]
fn search_vault(query: String) -> Result<String, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let qmd = format!("{home}/.bun/bin/qmd");
    let out = Command::new(&qmd)
        .args(["search", &query, "-n", "5"])
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("search failed: {e}"))?;
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

// ── Secrets (paste-a-key lane) ──────────────────────────────────────

fn agent_secret(home: &str, args: &[&str]) -> Result<String, String> {
    let cli = format!("{home}/aos/core/bin/cli/agent-secret");
    let out = Command::new("bash")
        .arg(&cli)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
    }
}

/// Store a secret in the macOS Keychain via the system's own secret CLI.
#[tauri::command]
fn save_secret(name: String, value: String) -> Result<(), String> {
    if name.trim().is_empty() || value.trim().is_empty() {
        return Err("Both a name and a value are required.".into());
    }
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    agent_secret(&home, &["set", name.trim(), value.trim()]).map(|_| ())
}

/// Open an https URL in the default browser (key-provider pages, auth links).
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    if !url.starts_with("https://") {
        return Err("only https links".into());
    }
    Command::new("open").arg(&url).spawn().map_err(|e| e.to_string())?;
    Ok(())
}

/// Remove a secret from the Keychain (the disconnect flow for token connectors).
#[tauri::command]
fn delete_secret(name: String) -> Result<(), String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    agent_secret(&home, &["delete", name.trim()]).map(|_| ())
}

/// What a connector does and provides — from the integrations registry,
/// with hand-written entries for the probed core connectors.
#[tauri::command]
fn connector_about(id: String) -> serde_json::Value {
    let builtin: Option<(&str, Vec<&str>)> = match id.as_str() {
        "google" => Some((
            "Gives your system hands inside your Google accounts — reading and sending mail, managing Drive files, calendars, and documents on your behalf.",
            vec!["Gmail read & send", "Drive files", "Calendar events", "Docs & Sheets"],
        )),
        "github" => Some((
            "Lets agents work with your repositories — issues, pull requests, code review, and releases — as you.",
            vec!["repos & code", "issues", "pull requests", "workflows"],
        )),
        "composio" => Some((
            "A hosted sign-in service: it holds the OAuth connections to 500+ apps so this Mac never stores those providers' tokens. Powers the one-click Connect buttons.",
            vec!["hosted OAuth for 500+ apps", "no provider tokens on this Mac", "revocable per app"],
        )),
        "cloudflare" => Some((
            "DNS, domains, tunnels, and hosting control for your web properties.",
            vec!["DNS records", "tunnels", "R2 storage", "page deployments"],
        )),
        _ => None,
    };
    if let Some((about, provides)) = builtin {
        return serde_json::json!({ "about": about, "provides": provides });
    }
    // Registry lookup (both `telegram` and `google_suite` style keys).
    if let Ok(home) = std::env::var("HOME") {
        if let Ok(text) =
            std::fs::read_to_string(format!("{home}/aos/core/infra/integrations/registry.yaml"))
        {
            if let Ok(reg) = serde_yaml::from_str::<serde_yaml::Value>(&text) {
                for section in ["apple_native", "builtin", "catalog"] {
                    if let Some(map) = reg.get(section).and_then(|v| v.as_mapping()) {
                        for (k, v) in map {
                            let key = k.as_str().unwrap_or("");
                            if key == id || key.replace('_', "-") == id {
                                let provides: Vec<String> = v
                                    .get("provides")
                                    .and_then(|p| p.as_sequence())
                                    .map(|s| {
                                        s.iter()
                                            .filter_map(|x| x.as_str().map(str::to_string))
                                            .collect()
                                    })
                                    .unwrap_or_default();
                                return serde_json::json!({
                                    "about": v.get("description").and_then(|d| d.as_str()).unwrap_or(""),
                                    "provides": provides,
                                });
                            }
                        }
                    }
                }
            }
        }
    }
    serde_json::json!({ "about": "", "provides": [] })
}

/// A detail page asks about every connector it draws, and each answer walks
/// the whole runtime tree with grep. That tree only changes when the system
/// updates, so remembering an answer for two minutes is honest and turns a
/// visibly slow page into an instant one.
static USAGE_CACHE: OnceLock<Mutex<HashMap<String, (Instant, serde_json::Value)>>> =
    OnceLock::new();
const USAGE_TTL: Duration = Duration::from_secs(120);

fn usage_cache() -> &'static Mutex<HashMap<String, (Instant, serde_json::Value)>> {
    USAGE_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Who in the system ACTUALLY consumes this connector — grep'd from the
/// runtime tree, not assumed. A key in the Keychain that nothing reads is
/// dormant, and the UI says so.
#[tauri::command]
fn connector_usage(id: String) -> serde_json::Value {
    if let Ok(cache) = usage_cache().lock() {
        if let Some((at, value)) = cache.get(&id) {
            if at.elapsed() < USAGE_TTL {
                return value.clone();
            }
        }
    }
    let fresh = compute_usage(&id);
    if let Ok(mut cache) = usage_cache().lock() {
        cache.insert(id, (Instant::now(), fresh.clone()));
    }
    fresh
}

fn compute_usage(id: &str) -> serde_json::Value {
    let terms: Vec<&str> = match id {
        "telegram" => vec!["TELEGRAM_BOT_TOKEN"],
        "slack" => vec!["SLACK_BOT_TOKEN"],
        "google" => vec!["GOOGLE_OAUTH_CLIENT_ID", "google_workspace_mcp"],
        "github" => vec!["gh pr", "gh api", "gh auth"],
        "whatsapp" => vec!["whatsmeow"],
        "clickup" => vec!["CLICKUP_API_TOKEN"],
        "plane" => vec!["PLANE_API_TOKEN"],
        "obsidian" => vec!["OBSIDIAN_REST_API_KEY"],
        "elevenlabs" => vec!["elevenlabs-api-key", "ELEVENLABS_API_KEY"],
        "openrouter" => vec!["OPENROUTER_API_KEY"],
        "paypal" => vec!["PAYPAL_CLIENT_ID"],
        "wave" => vec!["WAVE_ACCESS_TOKEN"],
        "chitchats" => vec!["CHITCHATS_API_KEY"],
        "cloudflare" => vec!["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_HISHAM_TOKEN"],
        "composio" => vec!["COMPOSIO_API_KEY"],
        "notion" => vec!["NOTION_API_KEY"],
        "linear" => vec!["LINEAR_API_KEY"],
        "discord" => vec!["DISCORD_BOT_TOKEN"],
        "todoist" => vec!["TODOIST_API_TOKEN"],
        // The engines and the network aren't keys — they're commands the
        // system shells out to, so the trace is the invocation itself.
        "claude" => vec!["claude -p", "claude --print", "cld "],
        "tailscale" => vec!["tailscale "],
        "kimi" => vec!["kimi "],
        "codex" => vec!["codex "],
        _ => vec![],
    };
    let Ok(home) = std::env::var("HOME") else {
        return serde_json::json!({ "in_use": false, "used_by": [] });
    };
    let root = format!("{home}/aos");
    let mut components: std::collections::BTreeSet<String> = Default::default();
    for term in terms {
        let Some(hits) = cmd_ok(
            "grep",
            &[
                "-rl", "--include=*.py", "--include=*.sh", "--include=*.yaml",
                "--include=*.ts", "--include=*.js", "--include=*.go", "--include=*.md",
                "--exclude-dir=.venv", "--exclude-dir=node_modules", "--exclude-dir=vendor",
                term, &format!("{root}/core"), &format!("{root}/config"),
            ],
        ) else {
            continue;
        };
        for path in hits.lines() {
            let rel = path.strip_prefix(&format!("{root}/")).unwrap_or(path);
            // One-time migrations and test files aren't real consumers.
            if rel.contains("/migrations/") || rel.contains("/tests/") || rel.contains("test_") {
                continue;
            }
            let parts: Vec<&str> = rel.split('/').collect();
            let label = match (parts.first().copied(), parts.get(1).copied(), parts.get(2).copied()) {
                (Some("core"), Some("services"), Some(name)) => format!("{name} service"),
                (Some("core"), Some("bin"), Some("crons")) => {
                    format!("{} routine", parts.get(3).map(|s| s.trim_end_matches(".py")).unwrap_or("scheduled"))
                }
                (Some("core"), Some("skills"), Some(name)) => format!("{name} skill"),
                (Some("core"), Some("qareen"), _) => "dashboard".into(),
                (Some("core"), Some("engine"), Some(name)) => format!("{name} engine"),
                (Some("core"), Some("infra"), Some("integrations")) => "integration setup".into(),
                (Some("core"), Some("infra"), Some("lib")) => "core system".into(),
                (Some("core"), Some("automations"), _) => "automations".into(),
                (Some("config"), _, _) => "system configuration".into(),
                _ => continue,
            };
            components.insert(label);
        }
    }
    // Declarations aren't consumers: setup scripts and config registries
    // (capabilities.yaml, accounts.yaml) mention every secret by name without
    // using it — counting them would make everything look "in use".
    let real: Vec<String> = components
        .iter()
        .filter(|c| c.as_str() != "integration setup" && c.as_str() != "system configuration")
        .cloned()
        .collect();
    serde_json::json!({ "in_use": !real.is_empty(), "used_by": real })
}

// ── Composio (hosted OAuth broker — ported from OpenMausBot, MIT) ───
//
// A project key (ak_…) owns one reusable Session. The Session mints
// per-service browser auth links; Composio's hosted page handles the
// provider OAuth, so no callback endpoint and no provider tokens ever
// live on this machine.

const COMPOSIO_API: &str = "https://backend.composio.dev/api/v3.1";

fn curl_json(
    method: &str,
    url: &str,
    api_key: &str,
    body: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    // The API key goes to curl via a stdin config file, never argv — argv is
    // world-readable through `ps` for the lifetime of the request.
    let mut cmd = Command::new("curl");
    cmd.args(["-s", "-m", "25", "-X", method, url, "--config", "-"]);
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
            .write_all(format!("header = \"x-api-key: {api_key}\"\n").as_bytes())
            .map_err(|e| e.to_string())?;
    }
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if text.is_empty() {
        return Err("No response from Composio — check your internet connection.".into());
    }
    serde_json::from_str(&text).map_err(|_| format!("Unexpected reply: {}", &text[..text.len().min(200)]))
}

fn composio_state_path(home: &str) -> String {
    format!("{home}/.aos/config/app-composio.yaml")
}

fn composio_key(home: &str) -> Option<String> {
    agent_secret(home, &["get", "COMPOSIO_API_KEY"]).ok().filter(|v| !v.is_empty())
}

fn composio_saved_session(home: &str) -> Option<(String, String)> {
    let text = std::fs::read_to_string(composio_state_path(home)).ok()?;
    let v: serde_yaml::Value = serde_yaml::from_str(&text).ok()?;
    Some((
        v.get("user_id")?.as_str()?.to_string(),
        v.get("session_id")?.as_str()?.to_string(),
    ))
}

fn composio_ensure_session(home: &str, api_key: &str) -> Result<String, String> {
    if let Some((_, session_id)) = composio_saved_session(home) {
        let check = curl_json("GET", &format!("{COMPOSIO_API}/tool_router/session/{session_id}"), api_key, None);
        if let Ok(v) = check {
            if v.get("session_id").is_some() {
                return Ok(session_id);
            }
        }
    }
    let user_id = composio_saved_session(home)
        .map(|(u, _)| u)
        .or_else(|| cmd_ok("uuidgen", &[]).map(|u| format!("aos_{}", u.to_lowercase())))
        .ok_or("could not generate a user id")?;
    let created = curl_json(
        "POST",
        &format!("{COMPOSIO_API}/tool_router/session"),
        api_key,
        Some(serde_json::json!({ "user_id": user_id })),
    )?;
    let session_id = created["session_id"]
        .as_str()
        .ok_or_else(|| {
            created["message"]
                .as_str()
                .map(str::to_string)
                .unwrap_or_else(|| "Composio rejected this key.".into())
        })?
        .to_string();
    let _ = std::fs::write(
        composio_state_path(home),
        format!("# Non-secret Composio session identifiers (key lives in Keychain).\nuser_id: {user_id}\nsession_id: {session_id}\n"),
    );
    Ok(session_id)
}

/// Validate + store a Composio project key, creating the reusable session.
#[tauri::command]
fn composio_setup(api_key: String) -> Result<(), String> {
    let key = api_key.trim().to_string();
    if !key.starts_with("ak_") {
        return Err("Composio project keys start with ak_".into());
    }
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    // Validate by creating/reusing a session BEFORE persisting the key.
    agent_secret(&home, &["set", "COMPOSIO_API_KEY", &key])?;
    match composio_ensure_session(&home, &key) {
        Ok(_) => Ok(()),
        Err(e) => Err(e),
    }
}

/// Mint a hosted sign-in link for a service and open it in the browser.
#[tauri::command]
fn composio_link(slug: String) -> Result<String, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let key = composio_key(&home).ok_or("Connect Composio first — paste your project key.")?;
    let session = composio_ensure_session(&home, &key)?;
    let v = curl_json(
        "POST",
        &format!("{COMPOSIO_API}/tool_router/session/{session}/link"),
        &key,
        Some(serde_json::json!({ "toolkit": slug })),
    )?;
    let url = v["redirect_url"]
        .as_str()
        .ok_or_else(|| {
            v["message"]
                .as_str()
                .map(str::to_string)
                .unwrap_or_else(|| format!("No sign-in link returned for {slug}."))
        })?
        .to_string();
    let _ = Command::new("open").arg(&url).spawn();
    Ok(url)
}

/// Connection status for a set of service slugs: slug → connected/pending.
#[tauri::command]
fn composio_status(slugs: Vec<String>) -> Result<serde_json::Value, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let key = composio_key(&home).ok_or("no Composio key")?;
    let session = composio_ensure_session(&home, &key)?;
    let list = slugs.join(",");
    let v = curl_json(
        "GET",
        &format!("{COMPOSIO_API}/tool_router/session/{session}/toolkits?limit=50&toolkits={list}"),
        &key,
        None,
    )?;
    let mut out = serde_json::Map::new();
    let empty = vec![];
    let items = v["items"].as_array().unwrap_or(&empty);
    for slug in &slugs {
        let item = items
            .iter()
            .find(|i| i["slug"].as_str().map(str::to_lowercase) == Some(slug.to_lowercase()));
        let state = item
            .and_then(|i| i["connected_account"]["status"].as_str())
            .unwrap_or(if item.and_then(|i| i["is_no_auth"].as_bool()).unwrap_or(false) {
                "ACTIVE"
            } else {
                "not_connected"
            });
        out.insert(
            slug.clone(),
            serde_json::json!({
                "connected": state.eq_ignore_ascii_case("active"),
                "pending": matches!(state.to_lowercase().as_str(), "initiated" | "initializing" | "pending"),
            }),
        );
    }
    Ok(serde_json::Value::Object(out))
}

/// Marketplace catalog: official names, blurbs, logos from Composio's
/// toolkit directory; curated fallback when no key / offline.
#[tauri::command]
fn composio_toolkits() -> Result<serde_json::Value, String> {
    let curated = serde_json::json!([
        { "slug": "gmail", "label": "Gmail", "blurb": "Read and send email", "logo": null },
        { "slug": "googlecalendar", "label": "Google Calendar", "blurb": "Read and create events", "logo": null },
        { "slug": "googledrive", "label": "Google Drive", "blurb": "Browse and manage files", "logo": null },
        { "slug": "notion", "label": "Notion", "blurb": "Pages and databases", "logo": null },
        { "slug": "slack", "label": "Slack", "blurb": "Post updates and read channels", "logo": null },
        { "slug": "github", "label": "GitHub", "blurb": "Issues, pull requests, and code", "logo": null },
        { "slug": "linear", "label": "Linear", "blurb": "Issues and project tracking", "logo": null },
        { "slug": "discord", "label": "Discord", "blurb": "Messages and channels", "logo": null },
        { "slug": "x", "label": "X (Twitter)", "blurb": "Post and read on X", "logo": null },
        { "slug": "reddit", "label": "Reddit", "blurb": "Browse and post", "logo": null },
        { "slug": "hubspot", "label": "HubSpot", "blurb": "CRM search & updates", "logo": null },
        { "slug": "jira", "label": "Jira", "blurb": "Issues and sprints", "logo": null },
        { "slug": "asana", "label": "Asana", "blurb": "Tasks and projects", "logo": null },
        { "slug": "trello", "label": "Trello", "blurb": "Boards and cards", "logo": null },
        { "slug": "dropbox", "label": "Dropbox", "blurb": "Files and folders", "logo": null },
        { "slug": "airtable", "label": "Airtable", "blurb": "Bases and records", "logo": null },
        { "slug": "figma", "label": "Figma", "blurb": "Files and comments", "logo": null },
        { "slug": "stripe", "label": "Stripe", "blurb": "Payments and customers", "logo": null },
        { "slug": "shopify", "label": "Shopify", "blurb": "Products, orders, customers", "logo": null },
        { "slug": "todoist", "label": "Todoist", "blurb": "Tasks and projects", "logo": null }
    ]);
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let Some(key) = composio_key(&home) else {
        return Ok(serde_json::json!({ "cards": curated, "source": "curated" }));
    };
    match curl_json(
        "GET",
        "https://backend.composio.dev/api/v3/toolkits?limit=500&sort_by=usage",
        &key,
        None,
    ) {
        Ok(v) => {
            let empty = vec![];
            let items = v["items"].as_array().or_else(|| v["data"].as_array()).unwrap_or(&empty);
            if items.is_empty() {
                return Ok(serde_json::json!({ "cards": curated, "source": "curated" }));
            }
            let cards: Vec<serde_json::Value> = items
                .iter()
                .map(|t| {
                    serde_json::json!({
                        "slug": t["slug"].as_str().or(t["key"].as_str()).unwrap_or("").to_lowercase(),
                        "label": t["name"].as_str().or(t["slug"].as_str()).unwrap_or(""),
                        "blurb": t["meta"]["description"].as_str().or(t["description"].as_str()).unwrap_or("").chars().take(90).collect::<String>(),
                        "logo": t["meta"]["logo"].as_str().or(t["logo"].as_str()),
                    })
                })
                .collect();
            Ok(serde_json::json!({ "cards": cards, "source": "api" }))
        }
        Err(_) => Ok(serde_json::json!({ "cards": curated, "source": "curated" })),
    }
}

/// Disconnect a Composio-connected service, revoking its account upstream.
#[tauri::command]
fn composio_disconnect(slug: String) -> Result<(), String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let key = composio_key(&home).ok_or("no Composio key")?;
    let session = composio_ensure_session(&home, &key)?;
    let list = curl_json(
        "GET",
        &format!("{COMPOSIO_API}/tool_router/session/{session}/toolkits?limit=50&toolkits={slug}"),
        &key,
        None,
    )?;
    let empty = vec![];
    let id = list["items"]
        .as_array()
        .unwrap_or(&empty)
        .iter()
        .find(|i| i["slug"].as_str().map(str::to_lowercase) == Some(slug.to_lowercase()))
        .and_then(|i| i["connected_account"]["id"].as_str())
        .map(str::to_string);
    let Some(id) = id else { return Ok(()) };
    curl_json(
        "DELETE",
        &format!("{COMPOSIO_API}/connected_accounts/{id}?revoke_on_delete=true"),
        &key,
        None,
    )?;
    Ok(())
}

// ── Connectors ──────────────────────────────────────────────────────

#[derive(serde::Serialize)]
struct ConnectorAccount {
    identity: String,
    detail: String,
}

#[derive(serde::Serialize, Clone)]
struct KeyField {
    secret: String,
    label: String,
    get_url: String,
}

#[derive(serde::Serialize)]
struct Connector {
    id: String,
    name: String,
    category: String,
    auth_kind: String, // oauth | token | session | cli | apple
    status: String,    // connected | attention | available
    detail: String,
    accounts: Vec<ConnectorAccount>,
    connect_hint: String,
    key_fields: Vec<KeyField>,
    composio_slug: Option<String>,
}

/// The two seamless connect lanes for a not-yet-connected service:
/// paste-a-key fields, and/or a Composio hosted-OAuth slug.
fn connector_lanes(id: &str) -> (Vec<KeyField>, Option<String>) {
    let kf = |secret: &str, label: &str, url: &str| KeyField {
        secret: secret.into(),
        label: label.into(),
        get_url: url.into(),
    };
    match id {
        "notion" => (
            vec![kf("NOTION_API_KEY", "Internal integration secret", "https://www.notion.so/my-integrations")],
            Some("notion".into()),
        ),
        "linear" => (
            vec![kf("LINEAR_API_KEY", "Personal API key", "https://linear.app/settings/api")],
            Some("linear".into()),
        ),
        "discord" => (
            vec![kf("DISCORD_BOT_TOKEN", "Bot token", "https://discord.com/developers/applications")],
            Some("discord".into()),
        ),
        "todoist" => (
            vec![kf("TODOIST_API_TOKEN", "API token", "https://todoist.com/app/settings/integrations/developer")],
            Some("todoist".into()),
        ),
        "slack" => (vec![], Some("slack".into())),
        _ => (vec![], None),
    }
}

fn keychain_names(home: &str) -> std::collections::HashSet<String> {
    cmd_ok(
        "bash",
        &[&format!("{home}/aos/core/bin/cli/agent-secret"), "list"],
    )
    .map(|out| out.lines().map(|l| l.trim().to_string()).collect())
    .unwrap_or_default()
}

// ── Intelligence (the subscription CLIs that do the thinking) ───────
//
// These are not integrations the system talks to — they are what the system
// thinks with. A signed-out engine is byte-identical on disk to a signed-in
// one, so presence alone would be a lie: every row here is a real sign-in
// check against the CLI itself.

#[derive(Default)]
#[derive(Clone)]
struct LlmProbe {
    installed: bool,
    version: Option<String>,
    signed_in: bool,
    /// Whoever the sign-in belongs to, when the CLI volunteers it.
    identity: Option<String>,
}

/// The slow probes behind the connectors and arms panes — CLI version+auth
/// checks (~300ms each), venv interpreter checks (a python spawn each), and
/// dead-port TCP timeouts (400ms each) — get short-lived memory. Sign-in and
/// service state don't flip second-to-second; a two-minute-old answer is
/// honest, and the panes go from a visible wait to instant.
static PROBE_CACHE: OnceLock<Mutex<HashMap<String, (Instant, LlmProbe)>>> = OnceLock::new();
static FLAG_CACHE: OnceLock<Mutex<HashMap<String, (Instant, bool)>>> = OnceLock::new();
const PROBE_TTL: Duration = Duration::from_secs(120);
const PORT_TTL: Duration = Duration::from_secs(30);

fn cached_probe(key: &str, fresh: impl FnOnce() -> LlmProbe) -> LlmProbe {
    let cache = PROBE_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(map) = cache.lock() {
        if let Some((at, probe)) = map.get(key) {
            if at.elapsed() < PROBE_TTL {
                return probe.clone();
            }
        }
    }
    let value = fresh();
    if let Ok(mut map) = cache.lock() {
        map.insert(key.to_string(), (Instant::now(), value.clone()));
    }
    value
}

fn cached_flag(key: &str, ttl: Duration, fresh: impl FnOnce() -> bool) -> bool {
    let cache = FLAG_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(map) = cache.lock() {
        if let Some((at, value)) = map.get(key) {
            if at.elapsed() < ttl {
                return *value;
            }
        }
    }
    let value = fresh();
    if let Ok(mut map) = cache.lock() {
        map.insert(key.to_string(), (Instant::now(), value));
    }
    value
}

/// CLIs disagree on where the number goes — "2.1.233 (Claude Code)",
/// "codex-cli 0.145.0", plain "0.29.1" — so take the first word that looks
/// like a version instead of guessing at a position.
fn version_number(text: &str) -> Option<String> {
    text.split_whitespace()
        .find(|w| w.starts_with(|c: char| c.is_ascii_digit()))
        .map(|w| {
            w.trim_matches(|c: char| !(c.is_ascii_alphanumeric() || c == '.'))
                .to_string()
        })
        .filter(|v| !v.is_empty())
}

fn probe_claude(home: &str) -> LlmProbe {
    let Some(bin) = resolve_bin(
        home,
        "claude",
        &["/opt/homebrew/bin/claude".to_string(), format!("{home}/.local/bin/claude")],
    ) else {
        return LlmProbe::default();
    };
    let version = run_out(&bin, &["--version"])
        .filter(|(ok, ..)| *ok)
        .and_then(|(_, out, _)| version_number(&out));

    // Newer builds answer `auth status` in JSON. Older ones don't have the
    // subcommand at all, and then a written-out config is the honest fallback:
    // sign-in is what creates it.
    let (signed_in, identity) = match run_out(&bin, &["auth", "status"]) {
        Some((true, out, _)) => match serde_json::from_str::<serde_json::Value>(&out) {
            Ok(v) => (
                v["loggedIn"].as_bool().unwrap_or(false),
                v["email"].as_str().map(str::to_string),
            ),
            Err(_) => (out.to_lowercase().contains("logged in"), None),
        },
        _ => (
            std::path::Path::new(&format!("{home}/.claude.json")).exists(),
            None,
        ),
    };
    LlmProbe { installed: true, version, signed_in, identity }
}

fn probe_kimi(home: &str) -> LlmProbe {
    let Some(bin) = resolve_bin(home, "kimi", &[format!("{home}/.kimi-code/bin/kimi")]) else {
        return LlmProbe::default();
    };
    let version = run_out(&bin, &["--version"])
        .filter(|(ok, ..)| *ok)
        .and_then(|(_, out, _)| version_number(&out));
    let signed_in =
        std::path::Path::new(&format!("{home}/.kimi-code/credentials/kimi-code.json")).exists();
    LlmProbe { installed: true, version, signed_in, identity: None }
}

fn probe_codex(home: &str) -> LlmProbe {
    let has_home = std::path::Path::new(&format!("{home}/.codex")).exists();
    let Some(bin) = resolve_bin(
        home,
        "codex",
        &[format!("{home}/.local/bin/codex"), "/opt/homebrew/bin/codex".to_string()],
    ) else {
        // Settings without the CLI: installed enough to show, not enough to probe.
        return LlmProbe { installed: has_home, ..Default::default() };
    };
    let version = run_out(&bin, &["--version"])
        .filter(|(ok, ..)| *ok)
        .and_then(|(_, out, _)| version_number(&out));
    // Codex answers "Logged in using ChatGPT" on stderr in some builds and
    // stdout in others, so read whichever one spoke.
    let (signed_in, identity) = match run_out(&bin, &["login", "status"]) {
        Some((ok, out, err)) => {
            let said = if out.is_empty() { err } else { out };
            let account = said
                .rsplit_once(" using ")
                .map(|(_, who)| who.trim().to_string())
                .filter(|w| !w.is_empty());
            (ok, account)
        }
        None => (false, None),
    };
    LlmProbe { installed: true, version, signed_in, identity }
}

/// The engines, as connector rows. Claude Code is always listed even when it
/// is missing — it is what runs this system, so its absence is the single most
/// important thing the panel can say.
fn intelligence_connectors(home: &str) -> Vec<Connector> {
    let engines = [
        (
            "claude",
            "Claude Code",
            cached_probe("claude", || probe_claude(home)),
            "Run `claude` in a terminal and follow sign-in.",
            "Claude Code not found",
        ),
        (
            "kimi",
            "Kimi Code",
            cached_probe("kimi", || probe_kimi(home)),
            "Run `kimi login`.",
            "Kimi Code not found",
        ),
        (
            "codex",
            "Codex (ChatGPT)",
            cached_probe("codex", || probe_codex(home)),
            "Run `codex login`.",
            "Codex not found",
        ),
    ];

    let mut out = Vec::new();
    for (id, name, probe, hint, missing) in engines {
        if !probe.installed && id != "claude" {
            continue;
        }
        let stamped = |suffix: &str| match &probe.version {
            Some(v) => format!("v{v} · {suffix}"),
            None => suffix.to_string(),
        };
        let detail = if !probe.installed {
            missing.to_string()
        } else if probe.signed_in {
            stamped("subscription signed in")
        } else {
            stamped("installed, not signed in")
        };
        let (accounts, token_warning) = llm_token_life(id, home, &probe);
        out.push(Connector {
            id: id.into(),
            name: name.into(),
            category: "intelligence".into(),
            auth_kind: "cli".into(),
            status: if !(probe.installed && probe.signed_in) {
                "attention"
            } else if token_warning {
                "attention"
            } else {
                "connected"
            }
            .into(),
            detail,
            accounts,
            connect_hint: hint.into(),
            key_fields: vec![],
            composio_slug: None,
        });
    }

    // Claude in Chrome — the browser hands Claude Code drives. Enabled per
    // session via the harness config; probed, not assumed.
    let chrome_installed = std::path::Path::new("/Applications/Google Chrome.app").exists();
    let enabled = std::fs::read_to_string(format!("{home}/.claude.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| v["claudeInChromeDefaultEnabled"].as_bool())
        .unwrap_or(false);
    if chrome_installed {
        out.push(Connector {
            id: "claude-chrome".into(),
            name: "Claude in Chrome".into(),
            category: "intelligence".into(),
            auth_kind: "cli".into(),
            status: if enabled { "connected" } else { "attention" }.into(),
            detail: if enabled {
                "enabled for every session".into()
            } else {
                "Chrome installed — extension disabled".into()
            },
            accounts: vec![],
            connect_hint: "Enable Chrome in Claude Code settings (/config → Claude in Chrome).".into(),
            key_fields: vec![],
            composio_slug: None,
        });
    }
    out
}

/// Epoch seconds → "Mon D, YYYY" without pulling a date crate for one label.
fn epoch_to_date(secs: i64) -> String {
    // Howard Hinnant's civil-from-days, the standard closed-form conversion.
    let days = secs.div_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    format!("{} {}, {}", MONTHS[(m - 1) as usize], d, y)
}

fn now_epoch() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// The session-lifetime story per engine: when the sign-in would actually cut
/// off, so the operator can re-login BEFORE losing the model mid-work.
/// Returns account rows + whether the cutoff is close enough to warrant an
/// attention state (within 14 days).
fn llm_token_life(id: &str, home: &str, probe: &LlmProbe) -> (Vec<ConnectorAccount>, bool) {
    if !probe.signed_in {
        return (vec![], false);
    }
    let identity = probe.identity.clone().unwrap_or_else(|| "signed in".into());
    let warn_window: i64 = 14 * 86_400;

    match id {
        "claude" => {
            // Keychain item owned by Claude Code itself; read locally, expose
            // only dates and tier — never token material.
            let json = cmd_ok("security", &["find-generic-password", "-s", "Claude Code-credentials", "-w"])
                .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok());
            let oauth = json.as_ref().map(|v| v["claudeAiOauth"].clone());
            let tier = oauth
                .as_ref()
                .and_then(|o| o["subscriptionType"].as_str().map(str::to_string))
                .map(|t| format!("{} subscription", capitalize(&t)))
                .unwrap_or_else(|| "subscription".into());
            let refresh_exp = oauth
                .as_ref()
                .and_then(|o| o["refreshTokenExpiresAt"].as_i64())
                .map(|ms| ms / 1000);
            let (life, warn) = match refresh_exp {
                Some(exp) if exp - now_epoch() < warn_window => (
                    format!("re-login by {} to avoid cutoff", epoch_to_date(exp)),
                    true,
                ),
                Some(exp) => (format!("session auto-renews · valid until {}", epoch_to_date(exp)), false),
                None => ("session auto-renews".into(), false),
            };
            (
                vec![ConnectorAccount { identity, detail: format!("{tier} · {life}") }],
                warn,
            )
        }
        "kimi" => {
            let json = std::fs::read_to_string(format!("{home}/.kimi-code/credentials/kimi-code.json"))
                .ok()
                .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok());
            let has_refresh = json
                .as_ref()
                .and_then(|v| v["refresh_token"].as_str())
                .is_some();
            let exp = json.as_ref().and_then(|v| v["expires_at"].as_i64());
            let (life, warn) = if has_refresh {
                ("session auto-renews".into(), false)
            } else {
                match exp {
                    Some(e) if e - now_epoch() < warn_window => {
                        (format!("expires {} — re-login soon", epoch_to_date(e)), true)
                    }
                    Some(e) => (format!("valid until {}", epoch_to_date(e)), false),
                    None => ("signed in".into(), false),
                }
            };
            (vec![ConnectorAccount { identity, detail: format!("subscription · {life}") }], warn)
        }
        "codex" => {
            let last = std::fs::read_to_string(format!("{home}/.codex/auth.json"))
                .ok()
                .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
                .and_then(|v| v["last_refresh"].as_str().map(str::to_string));
            let life = match last {
                Some(ts) => format!("auto-renews · last refreshed {}", ts.split('T').next().unwrap_or(&ts)),
                None => "session auto-renews".into(),
            };
            (vec![ConnectorAccount { identity, detail: format!("subscription · {life}") }], false)
        }
        _ => (vec![ConnectorAccount { identity, detail: "signed in".into() }], false),
    }
}

fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

// ── Tailscale (the private network the fleet lives on) ──────────────

fn tailscale_bin(home: &str) -> Option<String> {
    resolve_bin(
        home,
        "tailscale",
        &[
            "/opt/homebrew/bin/tailscale".to_string(),
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale".to_string(),
        ],
    )
}

/// `tailscale status --json`, stdout only — the CLI writes a version-skew
/// warning to stderr that would otherwise corrupt the JSON.
fn tailscale_status(home: &str) -> Option<serde_json::Value> {
    let bin = tailscale_bin(home)?;
    let (ok, out, _) = run_out(&bin, &["status", "--json"])?;
    if !ok {
        return None;
    }
    serde_json::from_str(&out).ok()
}

/// Peers as account rows: hostname, platform, and whether they're reachable
/// right now. Sorted by name so the list doesn't reshuffle between probes.
fn tailscale_peers(status: &serde_json::Value) -> Vec<ConnectorAccount> {
    let Some(peers) = status["Peer"].as_object() else {
        return vec![];
    };
    let mut rows: Vec<ConnectorAccount> = peers
        .values()
        .map(|p| {
            let os = p["OS"].as_str().unwrap_or("device");
            let online = if p["Online"].as_bool().unwrap_or(false) { "online" } else { "offline" };
            ConnectorAccount {
                identity: p["HostName"].as_str().unwrap_or("device").to_string(),
                detail: format!("{os} · {online}"),
            }
        })
        .collect();
    rows.sort_by(|a, b| a.identity.to_lowercase().cmp(&b.identity.to_lowercase()));
    rows.truncate(8);
    rows
}

fn tailscale_connector(home: &str) -> Option<Connector> {
    tailscale_bin(home)?;
    let row = |status: &str, detail: String, accounts: Vec<ConnectorAccount>| Connector {
        id: "tailscale".into(),
        name: "Tailscale".into(),
        category: "network".into(),
        auth_kind: "session".into(),
        status: status.into(),
        detail,
        accounts,
        connect_hint: "Run `tailscale up` and sign in to your tailnet.".into(),
        key_fields: vec![],
        composio_slug: None,
    };

    let Some(status) = tailscale_status(home) else {
        return Some(row("attention", "installed, not running".into(), vec![]));
    };
    if status["BackendState"].as_str() != Some("Running") {
        return Some(row("attention", "installed, not running".into(), vec![]));
    }

    let host = status["Self"]["HostName"].as_str().unwrap_or("this Mac").to_string();
    let peers = status["Peer"].as_object().map(|p| p.len()).unwrap_or(0);
    let tailnet = status["MagicDNSSuffix"]
        .as_str()
        .or_else(|| status["CurrentTailnet"]["Name"].as_str())
        .unwrap_or("the tailnet");
    // "other devices" because the count is peers — this Mac isn't in it.
    Some(row(
        "connected",
        format!("{host} · {peers} other devices on {tailnet}"),
        tailscale_peers(&status),
    ))
}

#[tauri::command]
fn list_connectors() -> Result<Vec<Connector>, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let secrets = keychain_names(&home);
    let has = |n: &str| secrets.contains(n);
    let loaded = loaded_launchd_labels();
    let mut out: Vec<Connector> = Vec::new();

    // Intelligence first — these are what the system thinks with.
    out.extend(intelligence_connectors(&home));

    // Tailscale — the private network the fleet talks over.
    if let Some(ts) = tailscale_connector(&home) {
        out.push(ts);
    }

    // Google Workspace — OAuth, per-account credential files with expiry.
    let mut google_accounts = Vec::new();
    if let Ok(entries) = std::fs::read_dir(format!("{home}/.google_workspace_mcp/credentials")) {
        for e in entries.flatten() {
            let path = e.path();
            if path.extension().and_then(|x| x.to_str()) != Some("json") {
                continue;
            }
            let identity = path
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("account")
                .to_string();
            let detail = std::fs::read_to_string(&path)
                .ok()
                .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
                .map(|v| {
                    let scopes = v["scopes"].as_array().map(|a| a.len()).unwrap_or(0);
                    // No refresh token means the access token dies at `expiry`
                    // and nothing can renew it — the operator must sign in again.
                    let renewable = v["refresh_token"]
                        .as_str()
                        .map(|t| !t.trim().is_empty())
                        .unwrap_or(false);
                    if renewable {
                        format!("{scopes} permissions · auto-refreshes")
                    } else {
                        format!("{scopes} permissions · needs re-authentication")
                    }
                })
                .unwrap_or_else(|| "credentials on file".into());
            google_accounts.push(ConnectorAccount { identity, detail });
        }
    }
    if !google_accounts.is_empty() {
        out.push(Connector {
            id: "google".into(),
            name: "Google Workspace".into(),
            category: "productivity".into(),
            auth_kind: "oauth".into(),
            status: "connected".into(),
            detail: format!("{} accounts — Gmail, Drive, Calendar, Docs", google_accounts.len()),
            accounts: google_accounts,
            connect_hint: "Add another account through the onboarding assistant — it walks the Google sign-in and stores only the refresh token.".into(),
                key_fields: vec![],
                composio_slug: None,
        });
    }

    // GitHub — gh CLI keyring.
    if let Some(gh) = cmd_ok("gh", &["auth", "status"]) {
        let account = gh
            .lines()
            .find(|l| l.contains("account"))
            .and_then(|l| l.split("account").nth(1))
            .map(|s| s.trim().split_whitespace().next().unwrap_or("").to_string())
            .unwrap_or_default();
        let scopes = gh
            .lines()
            .find(|l| l.contains("Token scopes"))
            .map(|l| l.split(':').nth(1).unwrap_or("").replace('\'', "").trim().to_string())
            .unwrap_or_default();
        out.push(Connector {
            id: "github".into(),
            name: "GitHub".into(),
            category: "development".into(),
            auth_kind: "cli".into(),
            status: "connected".into(),
            detail: format!("@{account}"),
            accounts: vec![ConnectorAccount {
                identity: format!("@{account}"),
                detail: format!("permissions: {scopes}"),
            }],
            connect_hint: "gh auth login --web".into(),
                key_fields: vec![],
                composio_slug: None,
        });
    }

    // Telegram — bot token(s) in Keychain.
    if has("TELEGRAM_BOT_TOKEN") {
        let mut accounts = vec![ConnectorAccount {
            identity: "primary bot".into(),
            detail: "bot token in Keychain · tokens don't expire".into(),
        }];
        if has("TELEGRAM_BOT_TOKEN_TABIB") {
            accounts.push(ConnectorAccount {
                identity: "tabib bot".into(),
                detail: "bot token in Keychain".into(),
            });
        }
        out.push(Connector {
            id: "telegram".into(),
            name: "Telegram".into(),
            category: "communication".into(),
            auth_kind: "token".into(),
            status: if loaded.contains("com.aos.bridge") { "connected" } else { "attention" }.into(),
            detail: if loaded.contains("com.aos.bridge") {
                "bridge running".into()
            } else {
                "token on file, bridge not running".into()
            },
            accounts,
            connect_hint: "The onboarding assistant creates the bot with you and stores its token in the Keychain.".into(),
                key_fields: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"].iter().map(|s| KeyField { secret: (*s).into(), label: "bot credential".into(), get_url: String::new() }).collect(),
                composio_slug: None,
        });
    }

    // WhatsApp — QR-paired session via whatsmeow.
    let wa_running = loaded.contains("com.aos.whatsmeow") || loaded.contains("com.agent.whatsmeow");
    if wa_running {
        out.push(Connector {
            id: "whatsapp".into(),
            name: "WhatsApp".into(),
            category: "communication".into(),
            auth_kind: "session".into(),
            status: "connected".into(),
            detail: "phone-paired session".into(),
            accounts: vec![ConnectorAccount {
                identity: "paired device".into(),
                detail: "QR session · re-pair if your phone unlinks it".into(),
            }],
            connect_hint: "Pair by scanning a QR code from WhatsApp on your phone.".into(),
                key_fields: vec![],
                composio_slug: None,
        });
    }

    // Slack — bot + app tokens.
    if has("SLACK_BOT_TOKEN") {
        out.push(Connector {
            id: "slack".into(),
            name: "Slack".into(),
            category: "communication".into(),
            auth_kind: "token".into(),
            status: "connected".into(),
            detail: "bot + app tokens in Keychain".into(),
            accounts: vec![],
            connect_hint: "Create a Slack app, install it to the workspace, store its tokens.".into(),
                key_fields: ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_ALLOWED_USER_ID"].iter().map(|s| KeyField { secret: (*s).into(), label: "app credential".into(), get_url: String::new() }).collect(),
                composio_slug: None,
        });
    }

    // Simple token-backed connectors: (id, name, category, [required secrets], detail)
    let token_connectors: &[(&str, &str, &str, &[&str], &str)] = &[
        ("clickup", "ClickUp", "productivity", &["CLICKUP_API_TOKEN"], "API token"),
        ("plane", "Plane", "productivity", &["PLANE_API_TOKEN"], "API token"),
        ("obsidian", "Obsidian", "knowledge", &["OBSIDIAN_REST_API_KEY"], "local REST API"),
        ("elevenlabs", "ElevenLabs", "voice", &["elevenlabs-api-key"], "API key"),
        ("openrouter", "OpenRouter", "ai", &["OPENROUTER_API_KEY"], "API key"),
        ("paypal", "PayPal", "business", &["PAYPAL_CLIENT_ID"], "client credentials"),
        ("wave", "Wave", "business", &["WAVE_ACCESS_TOKEN"], "access token"),
        ("chitchats", "Chit Chats", "business", &["CHITCHATS_API_KEY"], "API key"),
        ("notion", "Notion", "knowledge", &["NOTION_API_KEY"], "integration secret"),
        ("linear", "Linear", "development", &["LINEAR_API_KEY"], "API key"),
        ("discord", "Discord", "communication", &["DISCORD_BOT_TOKEN"], "bot token"),
        ("todoist", "Todoist", "productivity", &["TODOIST_API_TOKEN"], "API token"),
    ];
    for (id, name, category, needed, kind) in token_connectors {
        if needed.iter().all(|s| has(s)) {
            out.push(Connector {
                id: (*id).into(),
                name: (*name).into(),
                category: (*category).into(),
                auth_kind: "token".into(),
                status: "connected".into(),
                detail: format!("{kind} in Keychain"),
                accounts: vec![],
                connect_hint: String::new(),
                key_fields: needed
                    .iter()
                    .map(|s| KeyField {
                        secret: (*s).to_string(),
                        label: (*kind).to_string(),
                        get_url: String::new(),
                    })
                    .collect(),
                composio_slug: None,
            });
        }
    }

    // Cloudflare — two separate accounts by design.
    if has("CLOUDFLARE_HISHAM_TOKEN") || has("CLOUDFLARE_API_TOKEN") {
        let mut accounts = Vec::new();
        if has("CLOUDFLARE_HISHAM_TOKEN") {
            accounts.push(ConnectorAccount { identity: "personal (hish.am)".into(), detail: "API token".into() });
        }
        if has("CLOUDFLARE_ELORAGREENS_API_TOKEN") {
            accounts.push(ConnectorAccount { identity: "Elora Greens".into(), detail: "API token".into() });
        }
        out.push(Connector {
            id: "cloudflare".into(),
            name: "Cloudflare".into(),
            category: "infrastructure".into(),
            auth_kind: "token".into(),
            status: "connected".into(),
            detail: format!("{} accounts", accounts.len().max(1)),
            accounts,
            connect_hint: String::new(),
                key_fields: vec![],
                composio_slug: None,
        });
    }

    // Composio — the hosted OAuth broker that powers one-click connects.
    let composio_connected = has("COMPOSIO_API_KEY");
    out.push(Connector {
        id: "composio".into(),
        name: "Composio".into(),
        category: "infrastructure".into(),
        auth_kind: "token".into(),
        status: if composio_connected { "connected" } else { "available" }.into(),
        detail: if composio_connected {
            "hosted sign-in enabled — powers one-click connects".into()
        } else {
            "Enable one-click sign-in for 500+ apps".into()
        },
        accounts: vec![],
        connect_hint: String::new(),
        key_fields: vec![KeyField {
            secret: "COMPOSIO_API_KEY".into(),
            label: "Project API key (ak_…)".into(),
            get_url: "https://app.composio.dev/developers".into(),
        }],
        composio_slug: None,
    });

    // Available (not yet connected) — from the integrations registry catalog.
    if let Ok(text) =
        std::fs::read_to_string(format!("{home}/aos/core/infra/integrations/registry.yaml"))
    {
        if let Ok(reg) = serde_yaml::from_str::<serde_yaml::Value>(&text) {
            let connected_ids: std::collections::HashSet<String> =
                out.iter().map(|c| c.id.clone()).collect();
            for section in ["builtin", "catalog"] {
                let Some(map) = reg.get(section).and_then(|v| v.as_mapping()) else { continue };
                for (k, v) in map {
                    let id = k.as_str().unwrap_or("").replace('_', "-");
                    let plain = k.as_str().unwrap_or("");
                    if connected_ids.contains(&id)
                        || connected_ids.contains(plain)
                        || ["google-suite", "superwhisper", "health", "email"].contains(&id.as_str())
                    {
                        continue;
                    }
                    let (key_fields, composio_slug) = connector_lanes(&id);
                    out.push(Connector {
                        id: id.clone(),
                        name: v.get("name").and_then(|x| x.as_str()).unwrap_or(plain).to_string(),
                        category: v.get("category").and_then(|x| x.as_str()).unwrap_or("other").to_string(),
                        auth_kind: if composio_slug.is_some() { "oauth" } else { "token" }.into(),
                        status: "available".into(),
                        detail: v.get("description").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                        accounts: vec![],
                        connect_hint: String::new(),
                        key_fields,
                        composio_slug,
                    });
                }
            }
        }
    }

    Ok(out)
}

// ── Connector probes (live, not cached) ─────────────────────────────
//
// Every function here talks to the real service. Nothing reports health
// from a file on disk, and no secret is ever put in a message, a log line,
// or a process argument — tokens go over a pipe or stay in the Keychain.

/// GET a JSON endpoint that carries its own credential in the path or
/// needs none at all. The URL is never echoed back in an error, because
/// for the Telegram Bot API the URL *is* the token.
fn curl_get_json(url: &str) -> Result<serde_json::Value, String> {
    let out = Command::new("curl")
        .args(["-s", "-m", "10", url])
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("Couldn't run the connection test: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if text.is_empty() {
        return Err("No response — check your internet connection.".into());
    }
    serde_json::from_str(&text).map_err(|_| "The service sent back something unexpected.".to_string())
}

/// Live `getMe` for a bot token held in the Keychain, returning the API's
/// `result` object (bot name, @handle, capabilities). The token is read,
/// used, and dropped here — callers never see it.
fn telegram_get_me(home: &str, secret: &str) -> Result<serde_json::Value, String> {
    let token = agent_secret(home, &["get", secret])
        .map_err(|_| "No bot token in the Keychain for this slot.".to_string())?;
    let token = token.trim();
    if token.is_empty() {
        return Err("No bot token in the Keychain for this slot.".into());
    }
    let v = curl_get_json(&format!("https://api.telegram.org/bot{token}/getMe"))?;
    if !v["ok"].as_bool().unwrap_or(false) {
        return Err(v["description"]
            .as_str()
            .unwrap_or("Telegram rejected this bot token.")
            .to_string());
    }
    v.get("result")
        .cloned()
        .ok_or_else(|| "Telegram answered without any bot details.".to_string())
}

/// Percent-encode one value for an `application/x-www-form-urlencoded` body.
fn form_encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for b in value.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// POST a form body to `url` with the body handed to curl over stdin, so
/// client secrets and refresh tokens never appear in this machine's process
/// list. Fields are `(name, value)` pairs, encoded here.
fn curl_post_form(url: &str, fields: &[(&str, &str)]) -> Result<serde_json::Value, String> {
    let body = fields
        .iter()
        .map(|(k, v)| format!("{}={}", form_encode(k), form_encode(v)))
        .collect::<Vec<_>>()
        .join("&");

    let mut child = Command::new("curl")
        .args([
            "-s",
            "-m",
            "10",
            "-X",
            "POST",
            url,
            "-H",
            "content-type: application/x-www-form-urlencoded",
            "--data-binary",
            "@-",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Couldn't run the connection test: {e}"))?;

    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(body.as_bytes())
            .map_err(|e| format!("Couldn't send the request: {e}"))?;
    }

    let out = child
        .wait_with_output()
        .map_err(|e| format!("Couldn't run the connection test: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if text.is_empty() {
        return Err("No response — check your internet connection.".into());
    }
    serde_json::from_str(&text).map_err(|_| "The service sent back something unexpected.".to_string())
}

/// Refresh the first stored Google account's access token against Google's
/// own token endpoint — the only honest way to know the connection still
/// works. One account only: this runs behind a button and must feel instant.
/// Returns `(ok, plain-language message, account reached)`; never a token.
fn google_refresh_probe(home: &str) -> (bool, String, Option<String>) {
    let dir = format!("{home}/.google_workspace_mcp/credentials");
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return (false, "No Google accounts are connected on this Mac.".into(), None);
    };
    let mut files: Vec<std::path::PathBuf> = entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().and_then(|x| x.to_str()) == Some("json"))
        .collect();
    files.sort();
    let Some(path) = files.first() else {
        return (false, "No Google accounts are connected on this Mac.".into(), None);
    };
    let identity = path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("account")
        .to_string();

    let creds = std::fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok());
    let Some(creds) = creds else {
        return (
            false,
            format!("Couldn't read the stored credentials for {identity}."),
            Some(identity),
        );
    };
    let refresh_token = creds["refresh_token"]
        .as_str()
        .map(str::trim)
        .filter(|t| !t.is_empty());
    let Some(refresh_token) = refresh_token else {
        return (
            false,
            format!("{identity} has no refresh token — sign in to Google again."),
            Some(identity),
        );
    };

    let client_id = agent_secret(home, &["get", "GOOGLE_OAUTH_CLIENT_ID"]).unwrap_or_default();
    let client_secret =
        agent_secret(home, &["get", "GOOGLE_OAUTH_CLIENT_SECRET"]).unwrap_or_default();
    if client_id.trim().is_empty() || client_secret.trim().is_empty() {
        return (
            false,
            "Google's own app credentials aren't in the Keychain yet.".into(),
            Some(identity),
        );
    }

    let reply = curl_post_form(
        "https://oauth2.googleapis.com/token",
        &[
            ("client_id", client_id.trim()),
            ("client_secret", client_secret.trim()),
            ("refresh_token", refresh_token),
            ("grant_type", "refresh_token"),
        ],
    );
    match reply {
        Err(e) => (false, e, Some(identity)),
        Ok(v) => {
            let got_token = v["access_token"]
                .as_str()
                .map(|t| !t.is_empty())
                .unwrap_or(false);
            if got_token {
                (
                    true,
                    format!("Refreshed access for {identity} — connection healthy"),
                    Some(identity),
                )
            } else {
                let why = v["error_description"]
                    .as_str()
                    .or_else(|| v["error"].as_str())
                    .unwrap_or("Google refused to refresh this account.")
                    .to_string();
                (false, why, Some(identity))
            }
        }
    }
}

/// Live identities for every Telegram bot token on file — one `getMe` per
/// slot, so the UI can show real names and @handles instead of "primary bot".
/// Tokens stay in the Keychain; only names come back.
#[tauri::command]
fn telegram_bot_info() -> Result<Vec<serde_json::Value>, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for (slot, secret) in [
        ("primary", "TELEGRAM_BOT_TOKEN"),
        ("tabib", "TELEGRAM_BOT_TOKEN_TABIB"),
    ] {
        match telegram_get_me(&home, secret) {
            Ok(bot) => out.push(serde_json::json!({
                "slot": slot,
                "name": bot["first_name"].as_str().unwrap_or(""),
                "username": bot["username"].as_str().unwrap_or(""),
                "ok": true,
            })),
            Err(e) => out.push(serde_json::json!({
                "slot": slot,
                "ok": false,
                "error": e,
            })),
        }
    }
    Ok(out)
}

/// One real end-to-end probe per connector, timed. Every message is written
/// for a person: what happened, and what to do about it if it failed.
/// `identity` names whoever we reached, when the probe learns it.
#[tauri::command]
fn test_connector(id: String) -> Result<serde_json::Value, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let started = std::time::Instant::now();

    let (ok, message, identity): (bool, String, Option<String>) = match id.as_str() {
        "github" => match cmd_ok("gh", &["auth", "status"]) {
            Some(status) => {
                let account = status
                    .lines()
                    .find(|l| l.contains("account"))
                    .and_then(|l| l.split("account").nth(1))
                    .map(|s| s.trim().split_whitespace().next().unwrap_or("").to_string())
                    .unwrap_or_default();
                if account.is_empty() {
                    (true, "Signed in to GitHub".to_string(), None)
                } else {
                    (true, format!("Signed in as @{account}"), Some(format!("@{account}")))
                }
            }
            None => (
                false,
                "Not signed in — run `gh auth login --web` in a terminal.".to_string(),
                None,
            ),
        },

        "telegram" => match telegram_get_me(&home, "TELEGRAM_BOT_TOKEN") {
            Ok(bot) => {
                let handle = bot["username"].as_str().unwrap_or("unknown").to_string();
                (true, format!("Bot @{handle} is alive"), Some(format!("@{handle}")))
            }
            Err(e) => (false, e, None),
        },

        "google" => google_refresh_probe(&home),

        "whatsapp" => match http_probe("http://127.0.0.1:7601/health") {
            Some(code) => (
                true,
                format!("The WhatsApp bridge answered (HTTP {code})"),
                None,
            ),
            None => (
                false,
                "The WhatsApp bridge isn't answering on this Mac.".to_string(),
                None,
            ),
        },

        "obsidian" => match http_probe("http://127.0.0.1:27123/") {
            Some(code) => (
                true,
                format!("Obsidian's local API answered (HTTP {code})"),
                None,
            ),
            None => (
                false,
                "Obsidian isn't answering — open it and check the Local REST API plugin.".to_string(),
                None,
            ),
        },

        "claude" => {
            let probe = probe_claude(&home);
            let version = probe.version.clone().unwrap_or_else(|| "?".into());
            if !probe.installed {
                (false, "Claude Code isn't installed on this Mac.".to_string(), None)
            } else if probe.signed_in {
                (true, format!("Claude Code v{version} · signed in"), probe.identity)
            } else {
                (
                    false,
                    format!("Claude Code v{version} is installed but signed out — run `claude` in a terminal."),
                    None,
                )
            }
        }

        "kimi" => {
            let probe = probe_kimi(&home);
            let version = probe.version.clone().unwrap_or_else(|| "?".into());
            if !probe.installed {
                (false, "Kimi Code isn't installed on this Mac.".to_string(), None)
            } else if probe.signed_in {
                (true, format!("Kimi Code v{version} · signed in"), None)
            } else {
                (
                    false,
                    format!("Kimi Code v{version} is installed but signed out — run `kimi login`."),
                    None,
                )
            }
        }

        "codex" => {
            let probe = probe_codex(&home);
            let version = probe.version.clone().unwrap_or_else(|| "?".into());
            if !probe.installed {
                (false, "Codex isn't installed on this Mac.".to_string(), None)
            } else if probe.signed_in {
                (true, format!("Codex v{version} · signed in"), probe.identity)
            } else {
                (
                    false,
                    format!("Codex v{version} is installed but signed out — run `codex login`."),
                    None,
                )
            }
        }

        "tailscale" => match tailscale_status(&home) {
            Some(status) if status["BackendState"].as_str() == Some("Running") => {
                let host = status["Self"]["HostName"].as_str().unwrap_or("this Mac").to_string();
                let peers = status["Peer"].as_object().map(|p| p.len()).unwrap_or(0);
                (
                    true,
                    format!("Connected to tailnet as {host}, {peers} peers"),
                    Some(host),
                )
            }
            Some(_) => (
                false,
                "Tailscale is installed but not running — run `tailscale up`.".to_string(),
                None,
            ),
            None => (
                false,
                "Tailscale isn't answering on this Mac.".to_string(),
                None,
            ),
        },

        "composio" => match composio_key(&home) {
            None => (
                false,
                "No Composio project key is stored on this Mac yet.".to_string(),
                None,
            ),
            Some(key) => match composio_ensure_session(&home, &key) {
                Err(e) => (false, e, None),
                Ok(session) => match curl_json(
                    "GET",
                    &format!("{COMPOSIO_API}/tool_router/session/{session}"),
                    &key,
                    None,
                ) {
                    Ok(v) if v.get("session_id").is_some() => (
                        true,
                        "Hosted sign-in session healthy".to_string(),
                        v["user_id"].as_str().map(str::to_string),
                    ),
                    Ok(v) => (
                        false,
                        v["message"]
                            .as_str()
                            .unwrap_or("Composio didn't recognise this session.")
                            .to_string(),
                        None,
                    ),
                    Err(e) => (false, e, None),
                },
            },
        },

        _ => return Err("No test available yet for this connector.".into()),
    };

    // `ms` and `latency_ms` carry the same number: the app reads `ms`, the
    // spec named `latency_ms`, and neither side should have to guess.
    let elapsed = started.elapsed().as_millis() as u64;
    Ok(serde_json::json!({
        "ok": ok,
        "message": message,
        "identity": identity,
        "ms": elapsed,
        "latency_ms": elapsed,
    }))
}

/// Forget one Google account: back its credentials up, then delete them.
/// Destructive — the UI confirms before this runs, and the backup is the
/// half-finished run must not fail on the accounts it already removed.
#[tauri::command]
fn remove_google_account(identity: String) -> Result<(), String> {
    let identity = identity.trim();
    if identity.is_empty() || identity.contains('/') || identity.contains("..") {
        return Err("That doesn't look like an account name.".into());
    }
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let path = format!("{home}/.google_workspace_mcp/credentials/{identity}.json");
    if !std::path::Path::new(&path).exists() {
        return Ok(());
    }

    let backup_dir = format!("{home}/.aos/backups/google-credentials");
    std::fs::create_dir_all(&backup_dir)
        .map_err(|e| format!("Couldn't prepare the backup folder: {e}"))?;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    std::fs::copy(&path, format!("{backup_dir}/{identity}.json.{stamp}.bak"))
        .map_err(|e| format!("Couldn't back up the credentials: {e}"))?;

    std::fs::remove_file(&path).map_err(|e| format!("Couldn't remove the credentials: {e}"))
}

// ── Tools & permissions ─────────────────────────────────────────────
//
// A connector's tools come from truth, not from a hand-written list: if its
// MCP server is registered on this Mac we ask the server itself what it can
// do. Curated catalogs cover the connectors whose servers aren't registered
// here yet. Either way the per-tool permission is read from the file the
// agent runtime actually enforces — ~/.claude/settings.json — so what the
// panel shows and what an agent may do can never drift apart.

struct McpTool {
    name: String,
    description: String,
    read_only: bool,
}

struct McpServerSpec {
    command: String,
    args: Vec<String>,
    cwd: Option<String>,
    env: Vec<(String, String)>,
}

/// Which registered MCP server backs a connector. Deliberately a table:
/// pointing a connector at a server is one line here, not new plumbing.
/// Nothing in this Mac's current registry corresponds to a connector, so
/// today every lookup falls through to the curated catalogs below.
const MCP_SERVER_FOR_CONNECTOR: &[(&str, &str)] = &[
    // connector id → key under `mcpServers` in ~/.claude.json
    ("obsidian", "obsidian"),
];

/// The stdio MCP servers Claude Code itself is configured with. Remote
/// entries have no command to run, so they're skipped rather than guessed at.
fn registered_mcp_server(home: &str, name: &str) -> Option<McpServerSpec> {
    let text = std::fs::read_to_string(format!("{home}/.claude.json")).ok()?;
    let registry: serde_json::Value = serde_json::from_str(&text).ok()?;
    let entry = registry.get("mcpServers")?.get(name)?;
    if let Some(kind) = entry.get("type").and_then(|t| t.as_str()) {
        if kind != "stdio" {
            return None;
        }
    }
    Some(McpServerSpec {
        command: entry.get("command")?.as_str()?.to_string(),
        args: entry
            .get("args")
            .and_then(|a| a.as_array())
            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
            .unwrap_or_default(),
        cwd: entry.get("cwd").and_then(|c| c.as_str()).map(str::to_string),
        env: entry
            .get("env")
            .and_then(|e| e.as_object())
            .map(|e| {
                e.iter()
                    .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                    .collect()
            })
            .unwrap_or_default(),
    })
}

/// Ask a stdio MCP server what tools it has, speaking the protocol it already
/// speaks to Claude Code: initialize → initialized → tools/list.
///
/// A tool server that never answers must not freeze the app, so the whole
/// exchange runs under one deadline and the child is killed either way. The
/// reply is read on a worker thread because a blocked read cannot be timed out.
fn mcp_list_tools(spec: &McpServerSpec) -> Result<Vec<McpTool>, String> {
    let mut cmd = Command::new(&spec.command);
    cmd.args(&spec.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    if let Some(dir) = &spec.cwd {
        cmd.current_dir(dir);
    }
    for (k, v) in &spec.env {
        cmd.env(k, v);
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Couldn't start the tool server: {e}"))?;

    let deadline = Instant::now() + Duration::from_secs(6);
    let stdout = child.stdout.take().ok_or("no stdout")?;
    // Held open until the read finishes: some servers exit the moment their
    // input closes, before they've written the reply we're waiting for.
    let mut stdin = child.stdin.take().ok_or("no stdin")?;

    for message in [
        serde_json::json!({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": { "name": "aos-app", "version": "1" }
            }
        }),
        serde_json::json!({ "jsonrpc": "2.0", "method": "notifications/initialized" }),
        serde_json::json!({ "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {} }),
    ] {
        if writeln!(stdin, "{message}").is_err() {
            break;
        }
    }
    let _ = stdin.flush();

    let (tx, rx) = std::sync::mpsc::channel::<String>();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if tx.send(line).is_err() {
                break;
            }
        }
    });

    let mut listed: Option<Vec<McpTool>> = None;
    while let Some(remaining) = deadline.checked_duration_since(Instant::now()) {
        let Ok(line) = rx.recv_timeout(remaining) else { break };
        let Ok(reply) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
        if reply["id"].as_i64() != Some(2) {
            continue;
        }
        listed = reply["result"]["tools"].as_array().map(|tools| {
            tools
                .iter()
                .map(|t| McpTool {
                    name: t["name"].as_str().unwrap_or("").to_string(),
                    description: t["description"]
                        .as_str()
                        .unwrap_or("")
                        .lines()
                        .next()
                        .unwrap_or("")
                        .trim()
                        .to_string(),
                    // Unannotated tools are treated as writers — the cautious
                    // side of the read/write split is the safe default.
                    read_only: t["annotations"]["readOnlyHint"].as_bool().unwrap_or(false),
                })
                .filter(|t| !t.name.is_empty())
                .collect()
        });
        break;
    }

    let _ = child.kill();
    let _ = child.wait();
    drop(stdin);
    listed.ok_or_else(|| "The tool server didn't answer in time.".to_string())
}

/// Catalogs for connectors whose MCP servers aren't registered on this Mac —
/// the inventory the operator gets once the arm is installed. A live
/// `tools/list` always wins over these when the server is present.
type ToolGroup = (&'static str, &'static [(&'static str, &'static str)]);

fn curated_tools(id: &str) -> Option<(&'static str, &'static [ToolGroup])> {
    const GOOGLE: &[ToolGroup] = &[
        (
            "Read-only",
            &[
                ("search_gmail_messages", "Find mail by sender, subject, or words in the body."),
                ("read_gmail_thread", "Open one conversation with every reply in it."),
                ("get_calendar_events", "List what's on a calendar over a date range."),
                ("list_drive_files", "Browse and search the files in Drive."),
                ("read_doc", "Read the text of a Google Doc."),
            ],
        ),
        (
            "Write",
            &[
                ("send_gmail_message", "Send mail from your account, as you."),
                ("create_calendar_event", "Put a new event on a calendar, guests included."),
                ("upload_drive_file", "Add a new file to Drive."),
                ("edit_doc", "Change what a Google Doc says."),
                ("trash_file", "Move a Drive file to the trash."),
            ],
        ),
    ];
    const TELEGRAM: &[ToolGroup] = &[
        ("Read-only", &[("get_updates", "Fetch messages that have come in.")]),
        (
            "Write",
            &[
                ("send_message", "Send a text message to a chat."),
                ("send_voice", "Send a voice note to a chat."),
            ],
        ),
    ];
    match id {
        "google" => Some(("gws", GOOGLE)),
        "telegram" => Some(("bridge", TELEGRAM)),
        _ => None,
    }
}

fn claude_settings_path(home: &str) -> String {
    format!("{home}/.claude/settings.json")
}

fn read_claude_settings(home: &str) -> serde_json::Value {
    std::fs::read_to_string(claude_settings_path(home))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| serde_json::json!({}))
}

fn permission_list(settings: &serde_json::Value, key: &str) -> Vec<String> {
    settings["permissions"][key]
        .as_array()
        .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

/// `mcp__gws` in a permission list covers every tool on that server;
/// `mcp__gws__send_gmail_message` covers exactly one. A trailing `*` is
/// accepted because operators write patterns that way by hand.
fn permission_matches(pattern: &str, tool_id: &str) -> bool {
    let prefix = pattern.trim().trim_end_matches('*').trim_end_matches("__");
    !prefix.is_empty() && (tool_id == prefix || tool_id.starts_with(&format!("{prefix}__")))
}

/// The three states as the runtime actually resolves them: a denial wins over
/// everything, an allow grants, and a tool named in neither is asked about
/// every time it's used.
fn tool_permission(tool_id: &str, allow: &[String], deny: &[String]) -> &'static str {
    if deny.iter().any(|p| permission_matches(p, tool_id)) {
        "deny"
    } else if allow.iter().any(|p| permission_matches(p, tool_id)) {
        "allow"
    } else {
        "ask"
    }
}

fn tool_row(
    server: &str,
    name: &str,
    description: &str,
    allow: &[String],
    deny: &[String],
) -> serde_json::Value {
    let tool_id = format!("mcp__{server}__{name}");
    let permission = tool_permission(&tool_id, allow, deny);
    serde_json::json!({
        "name": name,
        "tool_id": tool_id,
        "description": description,
        "permission": permission,
    })
}

/// What this connector lets agents actually do, and what they're allowed to do
/// with it today. Live from the connector's MCP server when one is registered
/// here, curated otherwise; `supported: false` when we'd only be guessing.
#[tauri::command]
fn connector_tools(id: String) -> Result<serde_json::Value, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let settings = read_claude_settings(&home);
    let allow = permission_list(&settings, "allow");
    let deny = permission_list(&settings, "deny");

    // Live inventory, when this connector's server is registered on this Mac.
    let mapped = MCP_SERVER_FOR_CONNECTOR
        .iter()
        .find(|(connector, _)| *connector == id)
        .map(|(_, server)| *server);
    if let Some(server) = mapped {
        if let Some(spec) = registered_mcp_server(&home, server) {
            if let Ok(tools) = mcp_list_tools(&spec) {
                let mut groups = Vec::new();
                for (label, read_only) in [("Read-only", true), ("Write", false)] {
                    let rows: Vec<serde_json::Value> = tools
                        .iter()
                        .filter(|t| t.read_only == read_only)
                        .map(|t| tool_row(server, &t.name, &t.description, &allow, &deny))
                        .collect();
                    if !rows.is_empty() {
                        groups.push(serde_json::json!({ "name": label, "tools": rows }));
                    }
                }
                return Ok(serde_json::json!({
                    "supported": true,
                    "server": server,
                    "groups": groups,
                }));
            }
        }
    }

    // Curated catalog for the connectors whose servers aren't registered here.
    if let Some((server, catalog)) = curated_tools(&id) {
        let groups: Vec<serde_json::Value> = catalog
            .iter()
            .map(|(label, tools)| {
                let rows: Vec<serde_json::Value> = tools
                    .iter()
                    .map(|(name, description)| tool_row(server, name, description, &allow, &deny))
                    .collect();
                serde_json::json!({ "name": label, "tools": rows })
            })
            .collect();
        return Ok(serde_json::json!({
            "supported": true,
            "server": server,
            "groups": groups,
        }));
    }

    Ok(serde_json::json!({
        "supported": false,
        "server": serde_json::Value::Null,
        "groups": [],
    }))
}

/// `mcp__server` (a whole server) and `mcp__server__tool` (one tool) are the
/// only shapes the agent runtime understands as permission entries.
fn valid_tool_id(id: &str) -> bool {
    match id.strip_prefix("mcp__") {
        Some(rest) => {
            !rest.is_empty()
                && rest
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        }
        None => false,
    }
}

/// One backup per run of the app, taken before the first edit, so the
/// operator's original hand-written settings survive a session of clicking.
static SETTINGS_BACKED_UP: OnceLock<()> = OnceLock::new();

/// Grant, revoke, or fall back to asking for one tool — written straight into
/// the file the agent runtime reads, which is what makes this panel the real
/// permission editor rather than a picture of one. Unknown settings keys are
/// carried through untouched.
#[tauri::command]
fn set_tool_permission(tool_id: String, state: String) -> Result<(), String> {
    let tool_id = tool_id.trim().to_string();
    if !valid_tool_id(&tool_id) {
        return Err("That isn't a tool this system can grant.".into());
    }
    if !matches!(state.as_str(), "allow" | "ask" | "deny") {
        return Err("A tool is either allowed, asked about, or denied.".into());
    }

    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let path = claude_settings_path(&home);
    let mut settings = read_claude_settings(&home);
    let Some(root) = settings.as_object_mut() else {
        return Err("The agent settings file isn't in a shape this app can edit.".into());
    };

    if SETTINGS_BACKED_UP.set(()).is_ok() {
        let _ = std::fs::copy(&path, format!("{path}.bak"));
    }

    let permissions = root.entry("permissions").or_insert_with(|| serde_json::json!({}));
    if !permissions.is_object() {
        *permissions = serde_json::json!({});
    }
    let permissions = permissions
        .as_object_mut()
        .ok_or("The permissions section isn't in a shape this app can edit.")?;

    // The tool leaves both lists first: "ask" is simply being in neither, and
    // a stale entry on the other side would silently outrank the new one.
    for key in ["allow", "deny"] {
        let list = permissions.entry(key).or_insert_with(|| serde_json::json!([]));
        if !list.is_array() {
            *list = serde_json::json!([]);
        }
        if let Some(entries) = list.as_array_mut() {
            entries.retain(|v| v.as_str() != Some(tool_id.as_str()));
        }
    }
    if state != "ask" {
        if let Some(entries) = permissions.get_mut(state.as_str()).and_then(|v| v.as_array_mut()) {
            entries.push(serde_json::Value::String(tool_id));
        }
    }

    if let Some(parent) = std::path::Path::new(&path).parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("Couldn't reach the settings folder: {e}"))?;
    }
    let text = serde_json::to_string_pretty(&settings).map_err(|e| e.to_string())?;
    std::fs::write(&path, format!("{text}\n"))
        .map_err(|e| format!("Couldn't save the permission: {e}"))
}

// ── Health ──────────────────────────────────────────────────────────

#[derive(serde::Serialize)]
struct SvcHealth {
    label: String,
    name: String,
    running: bool,
    last_exit: i64,
}

#[derive(serde::Serialize)]
struct EndpointHealth {
    name: String,
    ok: bool,
    detail: String,
}

#[derive(serde::Serialize)]
struct HealthReport {
    mem_total_gb: u64,
    mem_free_pct: Option<u64>,
    disk_total_gb: u64,
    disk_avail_gb: u64,
    services: Vec<SvcHealth>,
    endpoints: Vec<EndpointHealth>,
    issues: Vec<String>,
}

fn http_probe(url: &str) -> Option<String> {
    cmd_ok("curl", &["-s", "-m", "2", "-o", "/dev/null", "-w", "%{http_code}", url])
        .filter(|code| code != "000" && !code.is_empty())
}

fn signal_name(sig: i64) -> String {
    match sig {
        9 => "killed (SIGKILL)".into(),
        11 => "crashed (segfault)".into(),
        15 => "terminated (SIGTERM)".into(),
        6 => "aborted".into(),
        s => format!("stopped by signal {s}"),
    }
}

#[tauri::command]
fn health_check() -> Result<HealthReport, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let mut issues: Vec<String> = Vec::new();

    // Memory
    let mem_total_gb = cmd_ok("sysctl", &["-n", "hw.memsize"])
        .and_then(|s| s.parse::<u64>().ok())
        .map(|b| b / 1_073_741_824)
        .unwrap_or(0);
    let mem_free_pct = cmd_ok("memory_pressure", &["-Q"]).and_then(|out| {
        out.lines()
            .find(|l| l.contains("free percentage"))
            .and_then(|l| l.split(':').nth(1))
            .and_then(|v| v.trim().trim_end_matches('%').parse::<u64>().ok())
    });
    if let Some(pct) = mem_free_pct {
        if pct < 15 {
            issues.push(format!("Memory is tight — only {pct}% free. Agent runs may slow down."));
        }
    }

    // Disk
    let (mut disk_total_gb, mut disk_avail_gb) = (0u64, 0u64);
    if let Some(df) = cmd_ok("df", &["-g", "/"]) {
        if let Some(row) = df.lines().nth(1) {
            let cols: Vec<&str> = row.split_whitespace().collect();
            disk_total_gb = cols.get(1).and_then(|s| s.parse().ok()).unwrap_or(0);
            disk_avail_gb = cols.get(3).and_then(|s| s.parse().ok()).unwrap_or(0);
        }
    }
    if disk_avail_gb > 0 && disk_avail_gb < 20 {
        issues.push(format!("Low disk space — {disk_avail_gb} GB left."));
    }

    // Services via launchd: "PID\tStatus\tLabel"
    let mut services: Vec<SvcHealth> = Vec::new();
    if let Some(list) = cmd_ok("launchctl", &["list"]) {
        for line in list.lines() {
            let cols: Vec<&str> = line.split_whitespace().collect();
            if cols.len() != 3 {
                continue;
            }
            let label = cols[2];
            if !(label.starts_with("com.aos.") || label.starts_with("com.agent.")) {
                continue;
            }
            let running = cols[0] != "-";
            let last_exit = cols[1].parse::<i64>().unwrap_or(0);
            let name = label.rsplit('.').next().unwrap_or(label).replace('-', " ");
            if last_exit != 0 {
                let what = if last_exit < 0 {
                    signal_name(-last_exit)
                } else {
                    format!("exited with code {last_exit}")
                };
                if running {
                    issues.push(format!("{name}: {what} on its last run — restarted and running now."));
                } else {
                    issues.push(format!("{name}: {what} and is not running."));
                }
            }
            services.push(SvcHealth { label: label.into(), name, running, last_exit });
        }
    }
    services.sort_by(|a, b| a.name.cmp(&b.name));

    // Known local endpoints
    let mut endpoints = Vec::new();
    for (name, url) in [
        ("qareen dashboard", "http://127.0.0.1:4096/"),
        ("bridge", "http://127.0.0.1:4098/health"),
        ("transcriber", "http://127.0.0.1:7602/health"),
        ("n8n", "http://127.0.0.1:5678/"),
    ] {
        let code = http_probe(url);
        let ok = code.is_some();
        endpoints.push(EndpointHealth {
            name: name.into(),
            ok,
            detail: code.map(|c| format!("HTTP {c}")).unwrap_or_else(|| "no response".into()),
        });
    }

    // Recent errors in instance logs (last 24h, tail of each file)
    let log_dir = format!("{home}/.aos/logs");
    if let Ok(entries) = std::fs::read_dir(&log_dir) {
        let now = std::time::SystemTime::now();
        let mut flagged = 0;
        for e in entries.flatten() {
            if flagged >= 4 {
                break;
            }
            let path = e.path();
            if path.extension().and_then(|x| x.to_str()) != Some("log") {
                continue;
            }
            let fresh = e
                .metadata()
                .ok()
                .and_then(|m| m.modified().ok())
                .and_then(|t| now.duration_since(t).ok())
                .map(|d| d.as_secs() < 86_400)
                .unwrap_or(false);
            if !fresh {
                continue;
            }
            if let Ok(text) = std::fs::read_to_string(&path) {
                let tail: String = text.chars().rev().take(16_000).collect::<String>().chars().rev().collect();
                let count = tail
                    .lines()
                    .filter(|l| {
                        let low = l.to_lowercase();
                        low.contains("error") || low.contains("traceback") || low.contains("failed")
                    })
                    .count();
                if count > 3 {
                    let fname = path.file_name().and_then(|f| f.to_str()).unwrap_or("log");
                    issues.push(format!("{count} recent error lines in {fname}."));
                    flagged += 1;
                }
            }
        }
    }

    Ok(HealthReport {
        mem_total_gb,
        mem_free_pct,
        disk_total_gb,
        disk_avail_gb,
        services,
        endpoints,
        issues,
    })
}

// ── Operator config ─────────────────────────────────────────────────

fn operator_yaml_path() -> Result<String, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    Ok(format!("{home}/.aos/config/operator.yaml"))
}

fn yaml_get<'a>(v: &'a serde_yaml::Value, path: &[&str]) -> Option<&'a serde_yaml::Value> {
    let mut cur = v;
    for key in path {
        cur = cur.get(key)?;
    }
    Some(cur)
}

const OPERATOR_FIELDS: &[(&str, &[&str])] = &[
    ("name", &["name"]),
    ("agent_name", &["agent_name"]),
    ("timezone", &["timezone"]),
    ("style", &["communication", "style"]),
    ("language", &["communication", "language"]),
    ("morning_briefing", &["daily_loop", "morning_briefing"]),
    ("evening_checkin", &["daily_loop", "evening_checkin"]),
    ("trust_level", &["trust", "default_level"]),
];

#[tauri::command]
fn operator_config() -> Result<std::collections::BTreeMap<String, String>, String> {
    let text = std::fs::read_to_string(operator_yaml_path()?).map_err(|e| e.to_string())?;
    let value: serde_yaml::Value = serde_yaml::from_str(&text).map_err(|e| e.to_string())?;
    let mut out = std::collections::BTreeMap::new();
    for (field, path) in OPERATOR_FIELDS {
        if let Some(v) = yaml_get(&value, path) {
            let s = match v {
                serde_yaml::Value::String(s) => s.clone(),
                serde_yaml::Value::Number(n) => n.to_string(),
                other => serde_yaml::to_string(other).unwrap_or_default().trim().to_string(),
            };
            out.insert(field.to_string(), s);
        }
    }
    Ok(out)
}

#[tauri::command]
fn save_operator_config(
    fields: std::collections::BTreeMap<String, String>,
) -> Result<(), String> {
    let path = operator_yaml_path()?;
    let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let mut value: serde_yaml::Value = serde_yaml::from_str(&text).map_err(|e| e.to_string())?;

    for (field, keypath) in OPERATOR_FIELDS {
        let Some(new_val) = fields.get(*field) else { continue };
        // Walk to the parent mapping, creating intermediate maps as needed.
        let mut cur = &mut value;
        for key in &keypath[..keypath.len() - 1] {
            if cur.get(key).is_none() {
                if let Some(map) = cur.as_mapping_mut() {
                    map.insert(
                        serde_yaml::Value::String(key.to_string()),
                        serde_yaml::Value::Mapping(Default::default()),
                    );
                }
            }
            cur = cur.get_mut(key).ok_or("config structure error")?;
        }
        let leaf = keypath[keypath.len() - 1];
        let yaml_val = if *field == "trust_level" {
            new_val
                .parse::<i64>()
                .map(serde_yaml::Value::from)
                .unwrap_or_else(|_| serde_yaml::Value::String(new_val.clone()))
        } else {
            serde_yaml::Value::String(new_val.clone())
        };
        if let Some(map) = cur.as_mapping_mut() {
            map.insert(serde_yaml::Value::String(leaf.to_string()), yaml_val);
        }
    }

    // Backup, then atomic-ish write.
    let _ = std::fs::copy(&path, format!("{path}.bak"));
    let out = serde_yaml::to_string(&value).map_err(|e| e.to_string())?;
    std::fs::write(&path, out).map_err(|e| e.to_string())
}

// ── Release notes ───────────────────────────────────────────────────

/// Latest sections of the system changelog for the Updates pane.
#[tauri::command]
fn release_notes() -> Result<String, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let text = std::fs::read_to_string(format!("{home}/aos/CHANGELOG.md"))
        .map_err(|_| "No changelog found".to_string())?;
    let mut sections = 0;
    let mut out: Vec<&str> = Vec::new();
    for line in text.lines() {
        if line.starts_with("## ") {
            sections += 1;
            if sections > 2 {
                break;
            }
        }
        if sections >= 1 {
            out.push(line);
        }
    }
    Ok(out.join("\n").trim().to_string())
}

// ── Modules (arms & connectors) ─────────────────────────────────────

#[derive(serde::Deserialize)]
struct Manifest {
    modules: Vec<ModuleDef>,
    /// Things running on this machine that AOS observes but does not own.
    /// Shown read-only so nothing on the Mac is invisible. Never touched.
    #[serde(default)]
    foreign: Vec<ForeignDef>,
}

#[derive(serde::Deserialize, serde::Serialize, Clone)]
struct ForeignDef {
    label: String,
    name: String,
    #[serde(default)]
    note: Option<String>,
}

#[derive(serde::Serialize)]
struct ForeignStatus {
    #[serde(flatten)]
    def: ForeignDef,
    loaded: bool,
}

/// What a module IS, which decides what "healthy" means for it.
/// Judging a periodic job by daemon rules produced four false BROKEN reports
/// in the 2026-08-17 audit — hence this is explicit, never inferred.
#[derive(serde::Deserialize, serde::Serialize, Clone, Default)]
struct Health {
    /// TCP port that must be LISTENing on 127.0.0.1.
    #[serde(default)]
    port: Option<u16>,
    /// A venv whose interpreter must still be able to install packages.
    #[serde(default)]
    venv: Option<String>,
    /// File whose mtime proves a periodic job is still firing.
    #[serde(default)]
    log: Option<String>,
    /// Seconds; log older than this ⇒ degraded.
    #[serde(default)]
    max_silence: Option<u64>,
}

#[derive(serde::Deserialize, serde::Serialize, Clone, Default)]
struct Detect {
    #[serde(default)]
    services: Vec<String>,
    #[serde(default)]
    paths: Vec<String>,
    #[serde(default)]
    commands: Vec<String>,
}

#[derive(serde::Deserialize, serde::Serialize, Clone)]
struct ModuleDef {
    id: String,
    name: String,
    category: String,
    tagline: String,
    #[serde(default)]
    ask: Option<String>,
    #[serde(default)]
    consent: Option<String>,
    #[serde(default)]
    costs: std::collections::BTreeMap<String, String>,
    #[serde(default)]
    services: Vec<String>,
    #[serde(default)]
    secrets: Vec<String>,
    #[serde(default)]
    status_note: Option<String>,
    /// schema 2: daemon | periodic | oneshot | resource.
    /// (In schema 1 this field meant "connector" — that moved to `connector`.)
    #[serde(default)]
    kind: Option<String>,
    /// schema 2: core | experimental. Drives grouping and install defaults.
    #[serde(default)]
    tier: Option<String>,
    /// schema 2: surfaces in the Connectors pane.
    #[serde(default)]
    connector: bool,
    #[serde(default)]
    health: Option<Health>,
    #[serde(default)]
    detect: Option<Detect>,
}

#[derive(serde::Serialize)]
struct ModuleStatus {
    #[serde(flatten)]
    def: ModuleDef,
    /// active | degraded | broken | absent — COMPUTED, never declared.
    status: String,
    /// Why it is degraded/broken, straight from the probe that found it.
    /// Lives here rather than in the UI so the reason can never drift from
    /// the check that produced it.
    why: String,
    can_toggle: bool,   // service-backed and plist present on disk
}

const BUNDLED_MANIFEST: &str = include_str!("../modules.yaml");

fn load_manifest() -> Result<Manifest, String> {
    let home = std::env::var("HOME").unwrap_or_default();
    let runtime = format!("{home}/aos/config/modules.yaml");
    let text = std::fs::read_to_string(&runtime).unwrap_or_else(|_| BUNDLED_MANIFEST.to_string());
    serde_yaml::from_str(&text).map_err(|e| format!("modules.yaml parse error: {e}"))
}

fn loaded_launchd_labels() -> std::collections::HashSet<String> {
    cmd_ok("launchctl", &["list"])
        .map(|out| {
            out.lines()
                .filter_map(|l| l.split_whitespace().nth(2).map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// Labels with a LIVE process, as opposed to merely loaded.
///
/// `launchctl list` prints "-" in the PID column for a job that is loaded but
/// not currently executing. For a StartInterval/StartCalendarInterval job that
/// is the normal resting state; for a daemon it means the thing is down. The
/// 2026-08-17 audit conflated the two and reported four healthy periodic jobs
/// as BROKEN, which is why liveness and loadedness are separate sets here.
fn running_launchd_labels() -> std::collections::HashSet<String> {
    cmd_ok("launchctl", &["list"])
        .map(|out| {
            out.lines()
                .skip(1)
                .filter_map(|l| {
                    let mut cols = l.split_whitespace();
                    let pid = cols.next()?;
                    let _status = cols.next()?;
                    let label = cols.next()?;
                    (pid != "-").then(|| label.to_string())
                })
                .collect()
        })
        .unwrap_or_default()
}

fn expand_home(value: &str, home: &str) -> String {
    value.replace("$HOME", home)
}

fn port_listening(port: u16) -> bool {
    cached_flag(&format!("port:{port}"), PORT_TTL, move || port_listening_uncached(port))
}

fn port_listening_uncached(port: u16) -> bool {
    use std::net::{Ipv4Addr, SocketAddr, TcpStream};
    use std::time::Duration;
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok()
}

/// True when a venv's interpreter can no longer install packages.
///
/// Homebrew's python@3.14 reports a perfectly good version string but its
/// pyexpat is linked against a newer expat than macOS ships, and
/// platform.mac_ver() comes back empty — so pip and uv both refuse it. A
/// service on such a venv looks green everywhere and is frozen forever.
/// Invisible to launchctl, which is exactly why it is probed here.
fn venv_broken(venv: &str) -> bool {
    let key = format!("venv:{venv}");
    let owned = venv.to_string();
    cached_flag(&key, PROBE_TTL, move || venv_broken_uncached(&owned))
}

fn venv_broken_uncached(venv: &str) -> bool {
    let python = format!("{venv}/bin/python");
    if !std::path::Path::new(&python).exists() {
        return false; // nothing to judge
    }
    cmd_ok(
        &python,
        &["-c", "import platform, pyexpat, sys; sys.exit(0 if platform.mac_ver()[0] else 1)"],
    )
    .is_none()
}

fn command_exists(name: &str, home: &str) -> bool {
    let dirs = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        &format!("{home}/.bun/bin"),
        &format!("{home}/.local/bin"),
        &format!("{home}/.cargo/bin"),
    ];
    dirs.iter()
        .any(|d| std::path::Path::new(&format!("{d}/{name}")).exists())
        || cmd_ok("which", &[name]).is_some()
}

/// The Arms & Connectors panel data: manifest merged with live system state.
#[tauri::command]
fn list_modules() -> Result<Vec<ModuleStatus>, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let manifest = load_manifest()?;
    let loaded = loaded_launchd_labels();
    let running = running_launchd_labels();

    Ok(manifest
        .modules
        .into_iter()
        .map(|m| {
            // 1. Is it INSTALLED at all?
            let installed = match &m.detect {
                Some(d) if !d.services.is_empty() => d.services.iter().any(|s| loaded.contains(s)),
                Some(d) if !d.paths.is_empty() => d
                    .paths
                    .iter()
                    .any(|p| std::path::Path::new(&expand_home(p, &home)).exists()),
                Some(d) if !d.commands.is_empty() => d.commands.iter().any(|c| command_exists(c, &home)),
                _ => false,
            };

            let can_toggle = m.services.iter().any(|label| {
                std::path::Path::new(&format!("{home}/Library/LaunchAgents/{label}.plist")).exists()
            });

            if !installed {
                return ModuleStatus { def: m, status: "absent".into(), why: String::new(), can_toggle };
            }

            // 2. Does it WORK? Judged by kind — a periodic job idling between
            //    ticks is healthy; a daemon with no process is not.
            let kind = m.kind.clone().unwrap_or_else(|| "resource".into());
            let health = m.health.clone().unwrap_or_default();
            let mut why: Vec<String> = Vec::new();

            if kind == "daemon" {
                if let Some(dead) = m.services.iter().find(|s| !running.contains(*s)) {
                    let label = dead.clone();
                    return ModuleStatus {
                        def: m,
                        status: "broken".into(),
                        why: format!("{label} is not running"),
                        can_toggle,
                    };
                }
                if let Some(p) = health.port {
                    if !port_listening(p) {
                        why.push(format!("port {p} not listening"));
                    }
                }
            }

            if kind == "periodic" {
                if let (Some(log), Some(max)) = (health.log.as_ref(), health.max_silence) {
                    let path = expand_home(log, &home);
                    if let Ok(age) = std::fs::metadata(&path)
                        .and_then(|md| md.modified())
                        .and_then(|t| t.elapsed().map_err(std::io::Error::other))
                    {
                        if age.as_secs() > max {
                            why.push(format!("last ran {} min ago", age.as_secs() / 60));
                        }
                    }
                }
            }

            // Applies to every kind: a venv on a broken interpreter looks green
            // and can never take an update again. This is invisible to launchctl.
            if let Some(v) = health.venv.as_ref() {
                if venv_broken(&expand_home(v, &home)) {
                    why.push("venv interpreter broken — cannot update".into());
                }
            }

            ModuleStatus {
                def: m,
                status: if why.is_empty() { "active" } else { "degraded" }.into(),
                why: why.join("; "),
                can_toggle,
            }
        })
        .collect())
}

#[derive(serde::Serialize)]
struct ServiceFact {
    label: String,
    plist_exists: bool,
    loaded: bool,
    running: bool,
    pid: Option<String>,
    last_exit: Option<String>,
}

#[derive(serde::Serialize)]
struct ModuleFacts {
    id: String,
    services: Vec<ServiceFact>,
    port: Option<u16>,
    port_listening: Option<bool>,
    venv: Option<String>,
    venv_usable: Option<bool>,
    log: Option<String>,
    log_age_seconds: Option<u64>,
}

/// Per-module live facts for the detail page.
///
/// Everything here is probed on demand rather than declared, because the point
/// of the detail page is to answer "what is this actually doing right now".
/// The list view deliberately shows less; this is where you come to find out
/// WHY something says degraded.
#[tauri::command]
fn module_facts(id: String) -> Result<ModuleFacts, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let manifest = load_manifest()?;
    let m = manifest
        .modules
        .into_iter()
        .find(|m| m.id == id)
        .ok_or_else(|| format!("unknown module {id}"))?;

    // One launchctl call, parsed for both loadedness and liveness.
    let mut table: std::collections::HashMap<String, (String, String)> = std::collections::HashMap::new();
    if let Some(out) = cmd_ok("launchctl", &["list"]) {
        for line in out.lines().skip(1) {
            let cols: Vec<&str> = line.split_whitespace().collect();
            if cols.len() >= 3 {
                table.insert(cols[2].to_string(), (cols[0].to_string(), cols[1].to_string()));
            }
        }
    }

    let services = m
        .services
        .iter()
        .map(|label| {
            let entry = table.get(label);
            let pid = entry.map(|(p, _)| p.clone());
            let running = pid.as_deref().map(|p| p != "-").unwrap_or(false);
            ServiceFact {
                plist_exists: std::path::Path::new(&format!(
                    "{home}/Library/LaunchAgents/{label}.plist"
                ))
                .exists(),
                loaded: entry.is_some(),
                running,
                pid: pid.filter(|p| p != "-"),
                last_exit: entry.map(|(_, e)| e.clone()),
                label: label.clone(),
            }
        })
        .collect();

    let health = m.health.clone().unwrap_or_default();
    let venv_path = health.venv.as_ref().map(|v| expand_home(v, &home));
    let log_age = health.log.as_ref().and_then(|l| {
        std::fs::metadata(expand_home(l, &home))
            .and_then(|md| md.modified())
            .ok()
            .and_then(|t| t.elapsed().ok())
            .map(|d| d.as_secs())
    });

    Ok(ModuleFacts {
        id: m.id.clone(),
        services,
        port: health.port,
        port_listening: health.port.map(port_listening),
        venv_usable: venv_path.as_ref().map(|p| !venv_broken(p)),
        venv: venv_path,
        log: health.log.clone(),
        log_age_seconds: log_age,
    })
}

/// Foreign agents: observed, never owned. Read-only in the UI.
#[tauri::command]
fn list_foreign() -> Result<Vec<ForeignStatus>, String> {
    let manifest = load_manifest()?;
    let loaded = loaded_launchd_labels();
    Ok(manifest
        .foreign
        .into_iter()
        .map(|f| ForeignStatus { loaded: loaded.contains(&f.label), def: f })
        .collect())
}

/// Start or stop a service-backed module NOW (launchctl), and record the
/// desired state for the system engine to reconcile.
#[tauri::command]
fn set_module_enabled(id: String, enabled: bool) -> Result<(), String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let manifest = load_manifest()?;
    let module = manifest
        .modules
        .into_iter()
        .find(|m| m.id == id)
        .ok_or_else(|| format!("unknown module {id}"))?;
    if module.services.is_empty() {
        return Err("This module isn't service-backed — it's managed by the system engine.".into());
    }
    let uid = cmd_ok("id", &["-u"]).ok_or("cannot resolve uid")?;

    let mut errors = Vec::new();
    for label in &module.services {
        let plist = format!("{home}/Library/LaunchAgents/{label}.plist");
        if !std::path::Path::new(&plist).exists() {
            continue;
        }
        let result = if enabled {
            Command::new("launchctl")
                .args(["bootstrap", &format!("gui/{uid}"), &plist])
                .output()
        } else {
            Command::new("launchctl")
                .args(["bootout", &format!("gui/{uid}/{label}")])
                .output()
        };
        match result {
            Ok(o) if o.status.success() => {}
            Ok(o) => {
                let msg = String::from_utf8_lossy(&o.stderr).trim().to_string();
                // bootout on an already-stopped service returns an error we can ignore
                if !(enabled == false && msg.contains("No such process")) {
                    errors.push(format!("{label}: {msg}"));
                }
            }
            Err(e) => errors.push(format!("{label}: {e}")),
        }
    }

    // Record the operator's choice in ~/.aos/config/services.yaml — the file
    // reconcile ALREADY reads (service_registry::disabled_services). This panel
    // briefly wrote its own app-modules.yaml instead; two files answering one
    // question meant reconcile would have honoured one and the panel the other,
    // and they would disagree the moment either was used. Keys are SERVICE
    // names, not labels: com.aos.transcriber is recorded as `transcriber`.
    let names: Vec<String> = module
        .services
        .iter()
        .map(|l| {
            l.strip_prefix("com.aos.")
                .or_else(|| l.strip_prefix("com.agent."))
                .unwrap_or(l)
                .to_string()
        })
        .collect();

    if !names.is_empty() {
        let path = format!("{home}/.aos/config/services.yaml");
        let existing = std::fs::read_to_string(&path).unwrap_or_default();

        // Preserve whatever header is there so the file stays self-documenting.
        let header: String = existing
            .lines()
            .take_while(|l| l.starts_with('#') || l.trim().is_empty())
            .collect::<Vec<_>>()
            .join("\n");

        let mut disabled: std::collections::BTreeSet<String> = serde_yaml::from_str::<
            serde_yaml::Value,
        >(&existing)
        .ok()
        .and_then(|v| v.get("disabled").cloned())
        .and_then(|v| v.as_sequence().cloned())
        .map(|seq| {
            seq.iter()
                .filter_map(|x| x.as_str().map(|s| s.trim().to_string()))
                .filter(|s| !s.is_empty())
                .collect()
        })
        .unwrap_or_default();

        for n in &names {
            if enabled {
                disabled.remove(n);
            } else {
                disabled.insert(n.clone());
            }
        }

        let body = disabled
            .iter()
            .map(|n| format!("- {n}\n"))
            .collect::<String>();
        let text = if header.trim().is_empty() {
            format!("# Operator service preferences for THIS machine.\n#\n# Services listed under `disabled:` are switched off by your choice.\n# Instance data — never committed, never shared between machines.\n\ndisabled:\n{body}")
        } else {
            format!("{header}\n\ndisabled:\n{body}")
        };
        let _ = std::fs::write(&path, text);
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("; "))
    }
}

/// Machine-readiness checks, run before install and before adding any arm.
/// Missing-but-installable tools are warnings (the installer handles them);
/// only conditions the installer cannot fix are failures.
#[tauri::command]
fn run_preflight() -> Vec<Check> {
    let mut out = Vec::new();

    // Chip
    if std::env::consts::ARCH == "aarch64" {
        out.push(check("chip", "Apple Silicon", "ok", "Full support, including local voice models".into()));
    } else {
        out.push(check("chip", "Apple Silicon", "warn", "Intel Mac — core works; on-device voice models unavailable".into()));
    }

    // macOS version
    match cmd_ok("sw_vers", &["-productVersion"]) {
        Some(v) => out.push(check("macos", "macOS version", "ok", format!("macOS {v}"))),
        None => out.push(check("macos", "macOS version", "warn", "Could not determine version".into())),
    }

    // Memory
    if let Some(bytes) = cmd_ok("sysctl", &["-n", "hw.memsize"]).and_then(|s| s.parse::<u64>().ok()) {
        let gb = bytes / 1_073_741_824;
        let (status, note) = if gb >= 16 {
            ("ok", "Comfortable headroom for agent runs")
        } else if gb >= 8 {
            ("warn", "Workable — keep optional services lean")
        } else {
            ("fail", "Below the minimum for agent workloads")
        };
        out.push(check("ram", "Memory", status, format!("{gb} GB — {note}")));
    }

    // Free disk
    if let Some(df) = cmd_ok("df", &["-g", "/"]) {
        if let Some(avail) = df
            .lines()
            .nth(1)
            .and_then(|l| l.split_whitespace().nth(3))
            .and_then(|s| s.parse::<u64>().ok())
        {
            let (status, note) = if avail >= 40 {
                ("ok", "Plenty of room")
            } else if avail >= 15 {
                ("warn", "Enough for core; large optional models may not fit")
            } else {
                ("fail", "Not enough free space to install safely")
            };
            out.push(check("disk", "Free disk space", status, format!("{avail} GB available — {note}")));
        }
    }

    // Xcode Command Line Tools
    let clt = std::path::Path::new("/Library/Developer/CommandLineTools").exists()
        || cmd_ok("xcode-select", &["-p"]).is_some();
    out.push(if clt {
        check("clt", "Developer tools", "ok", "Xcode Command Line Tools present".into())
    } else {
        check("clt", "Developer tools", "warn", "Missing — installed automatically during setup".into())
    });

    // git
    out.push(match cmd_ok("git", &["--version"]) {
        Some(v) => check("git", "Git", "ok", v),
        None => check("git", "Git", "warn", "Missing — comes with developer tools during setup".into()),
    });

    // Homebrew
    let brew = ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]
        .iter()
        .find(|p| std::path::Path::new(p).exists());
    out.push(match brew {
        Some(_) => check("brew", "Package manager", "ok", "Homebrew present".into()),
        None => check("brew", "Package manager", "warn", "Missing — installed automatically during setup".into()),
    });

    // Network
    let net = cmd_ok("curl", &["-sI", "--max-time", "6", "https://github.com"]).is_some();
    out.push(if net {
        check("net", "Internet connection", "ok", "Reachable".into())
    } else {
        check("net", "Internet connection", "fail", "Can't reach the internet — setup needs to download components".into())
    });

    out
}

/// Runs the system installer (`~/aos/install.sh`), streaming each output line
/// to the frontend as `install:line` events and the exit code as `install:done`.
///
/// The installer itself is idempotent and resumable — this command is a thin,
/// honest wrapper around it, not a reimplementation.
#[tauri::command]
fn run_install(app: tauri::AppHandle, dry_run: bool) -> Result<(), String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let script = format!("{home}/aos/install.sh");

    if !std::path::Path::new(&script).exists() {
        return Err(format!(
            "Installer not found at {script}. The system source isn't on this Mac yet."
        ));
    }

    let mut cmd = Command::new("bash");
    cmd.arg(&script);
    if dry_run {
        cmd.arg("--dry-run");
        cmd.env("INSTALL_DRY_RUN", "1");
    }
    // Plain output — no cursor tricks to strip.
    cmd.env("TERM", "dumb")
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = cmd.spawn().map_err(|e| format!("Failed to start installer: {e}"))?;

    let stdout = child.stdout.take().ok_or("no stdout")?;
    let stderr = child.stderr.take().ok_or("no stderr")?;

    let app_out = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let _ = app_out.emit("install:line", line);
        }
    });

    let app_err = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = app_err.emit("install:line", line);
        }
    });

    std::thread::spawn(move || {
        let code = child
            .wait()
            .ok()
            .and_then(|s| s.code())
            .unwrap_or(-1);
        let _ = app.emit("install:done", code);
    });

    Ok(())
}

// ── Self-update (invite-gated) ──────────────────────────────────────

/// The invite broker. Update manifests move behind it so that an invite — not
/// a guessable URL — decides who may install and who may keep up to date.
#[cfg(not(debug_assertions))]
const BROKER_URL: &str = "https://aos-connect-broker.aoshq.workers.dev";

/// Where manifests were served before the broker existed. Installs that predate
/// the gate have no invite on file, so they keep updating from here until one
/// is issued — a gate that bricks the apps already in the field is not a gate,
/// it is an outage.
#[cfg(not(debug_assertions))]
const PUBLIC_UPDATER_URL: &str = "https://aos.hish.am/updater/latest.json";

/// Ask the updater whether there is anything newer, presenting this install's
/// invite when it has one.
///
/// Silent by design: a missing invite, a revoked one, or no network all return
/// `None`, and the app simply stays on the version it already has.
#[cfg(not(debug_assertions))]
async fn check_for_update(handle: &tauri::AppHandle) -> Option<tauri_plugin_updater::Update> {
    use tauri_plugin_updater::UpdaterExt;

    let home = std::env::var("HOME").ok()?;
    let invite = agent_secret(&home, &["get", "AOS_INVITE_TOKEN"])
        .ok()
        .filter(|token| !token.is_empty());
    let machine_id = std::fs::read_to_string(format!("{home}/.aos/.machine-id"))
        .ok()
        .map(|id| id.trim().to_string())
        .filter(|id| !id.is_empty());

    let builder = handle.updater_builder();
    let builder = match (invite, machine_id) {
        (Some(invite), Some(machine_id)) => {
            let endpoint = tauri::Url::parse(&format!("{BROKER_URL}/v1/updater/latest.json")).ok()?;
            builder
                .endpoints(vec![endpoint])
                .ok()?
                .header("x-invite-token", invite)
                .ok()?
                .header("x-machine-id", machine_id)
                .ok()?
        }
        _ => builder
            .endpoints(vec![tauri::Url::parse(PUBLIC_UPDATER_URL).ok()?])
            .ok()?,
    };

    builder.build().ok()?.check().await.ok().flatten()
}

/// The updater, narrated: every state lands on the frontend as an
/// "app-update" event so the operator can SEE what happened. Silence was a
/// design flaw — a finished update and a failed one felt identical.
#[cfg(not(debug_assertions))]
async fn run_app_update(handle: tauri::AppHandle) {
    use tauri::Emitter as _;
    let emit = |state: &str, extra: serde_json::Value| {
        let mut payload = serde_json::json!({ "state": state });
        if let (Some(obj), Some(add)) = (payload.as_object_mut(), extra.as_object()) {
            for (k, v) in add {
                obj.insert(k.clone(), v.clone());
            }
        }
        let _ = handle.emit("app-update", payload);
    };
    emit("checking", serde_json::json!({}));
    match check_for_update(&handle).await {
        Some(update) => {
            let version = update.version.clone();
            emit("downloading", serde_json::json!({ "version": version }));
            match update.download_and_install(|_, _| {}, || {}).await {
                Ok(()) => emit("ready", serde_json::json!({ "version": version })),
                Err(e) => emit("error", serde_json::json!({ "message": e.to_string() })),
            }
        }
        None => emit("uptodate", serde_json::json!({})),
    }
}

/// The app's own version — distinct from the SYSTEM version, and the thing
/// the operator could not see anywhere when they asked "did the app update?".
#[tauri::command]
fn app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// Re-run the update check on demand (Updates pane button).
#[tauri::command]
async fn check_app_update(handle: tauri::AppHandle) -> Result<(), String> {
    #[cfg(not(debug_assertions))]
    {
        run_app_update(handle).await;
    }
    #[cfg(debug_assertions)]
    {
        use tauri::Emitter as _;
        let _ = handle.emit("app-update", serde_json::json!({ "state": "uptodate", "dev": true }));
    }
    Ok(())
}

/// Apply a staged update: relaunch into the new version.
#[tauri::command]
fn restart_app(handle: tauri::AppHandle) {
    handle.restart();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            // Self-update, Claude-app style: check quietly on launch, download
            // and stage in the background; the new version runs on next start.
            #[cfg(not(debug_assertions))]
            {
                let _ = app.handle().plugin(tauri_plugin_updater::Builder::new().build());
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    run_app_update(handle).await;
                });
            }

            // Open at 70% of the screen, centered — big enough to feel like a
            // home, small enough to stay a window.
            use tauri::Manager as _;
            if let Some(window) = app.get_webview_window("main") {
                if let Ok(Some(monitor)) = window.current_monitor() {
                    let size = monitor.size();
                    let w = (size.width as f64 * 0.7) as u32;
                    let h = (size.height as f64 * 0.7) as u32;
                    let _ = window.set_size(tauri::PhysicalSize::new(w, h));
                    let _ = window.center();
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            run_install,
            app_version,
            check_app_update,
            restart_app,
            detect_system,
            check_updates,
            run_update,
            run_preflight,
            list_modules,
            list_foreign,
            module_facts,
            set_module_enabled,
            save_setup_config,
            load_setup_config,
            home_data,
            health_check,
            list_connectors,
            connector_usage,
            connector_tools,
            set_tool_permission,
            telegram_bot_info,
            test_connector,
            remove_google_account,
            save_secret,
            delete_secret,
            open_url,
            connector_about,
            composio_disconnect,
            composio_setup,
            composio_link,
            composio_status,
            composio_toolkits,
            operator_config,
            save_operator_config,
            release_notes,
            search_vault
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The bundled manifest is the app's fallback on a machine with no runtime
    /// copy, so a parse failure here is a black screen for every fresh install.
    /// `cargo check` cannot catch it — serde only runs at runtime.
    #[test]
    fn bundled_manifest_parses_as_schema_2() {
        let m: Manifest = serde_yaml::from_str(BUNDLED_MANIFEST)
            .expect("bundled modules.yaml must parse");
        assert!(!m.modules.is_empty(), "manifest has no modules");
        assert!(!m.foreign.is_empty(), "foreign list should not be empty");
    }

    /// Every module must declare what it IS. Without `kind`, health is
    /// unjudgeable: a periodic job idling between ticks looks identical to a
    /// dead daemon, which is exactly how four healthy jobs were once reported
    /// BROKEN. Without `tier`, the panel cannot separate spine from extras.
    #[test]
    fn every_module_declares_tier_and_kind() {
        let m: Manifest = serde_yaml::from_str(BUNDLED_MANIFEST).unwrap();
        for module in &m.modules {
            let kind = module.kind.as_deref().unwrap_or("");
            let tier = module.tier.as_deref().unwrap_or("");
            assert!(
                matches!(kind, "daemon" | "periodic" | "oneshot" | "resource"),
                "module '{}' has invalid kind '{kind}'", module.id
            );
            assert!(
                matches!(tier, "core" | "experimental"),
                "module '{}' has invalid tier '{tier}'", module.id
            );
        }
    }

    /// Regression guard for the schema 1 → 2 migration: `kind` no longer means
    /// "connector". If any module ever carries kind: connector again, the Arms
    /// grouping and the health probes both silently misread it.
    #[test]
    fn kind_is_never_used_for_connectors() {
        let m: Manifest = serde_yaml::from_str(BUNDLED_MANIFEST).unwrap();
        assert!(
            m.modules.iter().all(|x| x.kind.as_deref() != Some("connector")),
            "kind: connector is schema 1 — use the `connector` flag instead"
        );
        assert!(
            m.modules.iter().any(|x| x.connector),
            "no module is flagged as a connector — the Connectors pane would be empty"
        );
    }
}
