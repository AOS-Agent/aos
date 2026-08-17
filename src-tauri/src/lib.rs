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
            run_preflight
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
