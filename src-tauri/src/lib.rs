use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use tauri::Emitter;

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
        .invoke_handler(tauri::generate_handler![run_install])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
