use std::process::Command;

const PYTHON: &str = "/usr/bin/python3";
const WORK_CLI: &str = "/Users/agentalhadi/aos/core/work/cli.py";

/// Allowed work CLI subcommands. Prevents arbitrary command injection.
const ALLOWED_SUBCOMMANDS: &[&str] = &[
    "list", "today", "next", "show", "add", "done", "start", "search",
    "projects", "thread", "inbox", "subtask", "handoff", "dispatch", "link",
];

#[tauri::command]
pub async fn run_work_command(args: Vec<String>) -> Result<String, String> {
    if args.is_empty() {
        return Err("No arguments provided. Provide a work CLI subcommand.".to_string());
    }

    // Validate the first argument is an allowed subcommand.
    let subcommand = &args[0];
    if !ALLOWED_SUBCOMMANDS.contains(&subcommand.as_str()) {
        return Err(format!(
            "Unknown subcommand: {subcommand}. Allowed: {}",
            ALLOWED_SUBCOMMANDS.join(", ")
        ));
    }

    let mut cmd_args = vec![WORK_CLI.to_string()];
    cmd_args.extend(args);

    let output = Command::new(PYTHON)
        .args(&cmd_args)
        .output()
        .map_err(|e| format!("Failed to execute work CLI: {e}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !output.status.success() {
        return Err(format!("Work CLI error: {stderr}"));
    }

    // Include stderr in output if there are warnings, but command succeeded.
    if stderr.is_empty() {
        Ok(stdout)
    } else {
        Ok(format!("{stdout}\n---\n{stderr}"))
    }
}
