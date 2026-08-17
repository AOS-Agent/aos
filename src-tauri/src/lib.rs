use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use tauri::Emitter;

#[derive(serde::Serialize, Default)]
struct SystemInfo {
    installed: bool,
    version: Option<String>,
    operator: Option<String>,
    update_status: Option<String>, // up_to_date | update_available | ...
    last_check: Option<String>,
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

    SystemInfo { installed, version, operator, update_status, last_check }
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
    let mut cmd = Command::new("curl");
    cmd.args(["-s", "-m", "25", "-X", method, url, "-H"]);
    cmd.arg(format!("x-api-key: {api_key}"));
    if let Some(b) = body {
        cmd.args(["-H", "content-type: application/json", "--data-binary"]);
        cmd.arg(b.to_string());
    }
    let out = cmd.stdin(Stdio::null()).output().map_err(|e| e.to_string())?;
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

#[tauri::command]
fn list_connectors() -> Result<Vec<Connector>, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let secrets = keychain_names(&home);
    let has = |n: &str| secrets.contains(n);
    let loaded = loaded_launchd_labels();
    let mut out: Vec<Connector> = Vec::new();

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
                    format!("{scopes} permissions · token auto-refreshes")
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
    #[serde(default)]
    kind: Option<String>, // connector | arm (default)
    #[serde(default)]
    detect: Option<Detect>,
}

#[derive(serde::Serialize)]
struct ModuleStatus {
    #[serde(flatten)]
    def: ModuleDef,
    status: String,     // active | available
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

    Ok(manifest
        .modules
        .into_iter()
        .map(|m| {
            let active = match &m.detect {
                Some(d) if !d.services.is_empty() => d.services.iter().any(|s| loaded.contains(s)),
                Some(d) if !d.paths.is_empty() => d
                    .paths
                    .iter()
                    .any(|p| std::path::Path::new(&p.replace("$HOME", &home)).exists()),
                Some(d) if !d.commands.is_empty() => d.commands.iter().any(|c| command_exists(c, &home)),
                _ => false,
            };
            let can_toggle = m.services.iter().any(|label| {
                std::path::Path::new(&format!("{home}/Library/LaunchAgents/{label}.plist")).exists()
            });
            ModuleStatus {
                def: m,
                status: if active { "active" } else { "available" }.into(),
                can_toggle,
            }
        })
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

    // Record desired state for the system engine (reconciled on update).
    let intent_path = format!("{home}/.aos/config/app-modules.yaml");
    let mut state: std::collections::BTreeMap<String, bool> = std::fs::read_to_string(&intent_path)
        .ok()
        .and_then(|t| serde_yaml::from_str(&t).ok())
        .unwrap_or_default();
    state.insert(id, enabled);
    if let Ok(text) = serde_yaml::to_string(&state) {
        let _ = std::fs::write(&intent_path, text);
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            run_install,
            detect_system,
            check_updates,
            run_update,
            run_preflight,
            list_modules,
            set_module_enabled,
            save_setup_config,
            load_setup_config,
            home_data,
            health_check,
            list_connectors,
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
