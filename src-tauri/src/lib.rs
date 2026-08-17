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
            search_vault
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
