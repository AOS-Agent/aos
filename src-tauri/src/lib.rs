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
            run_update
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
