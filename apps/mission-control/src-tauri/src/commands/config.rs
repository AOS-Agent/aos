use std::path::PathBuf;

/// Returns the resolved home directory.
fn home_dir() -> Result<PathBuf, String> {
    dirs::home_dir().ok_or_else(|| "Could not determine home directory".to_string())
}

/// Checks whether a given path is within the allowed config scope (~/.aos/).
fn validate_config_path(path: &str) -> Result<PathBuf, String> {
    let home = home_dir()?;
    let allowed_root = home.join(".aos");

    let resolved = if path.starts_with("~/") {
        home.join(&path[2..])
    } else if path.starts_with('/') {
        PathBuf::from(path)
    } else {
        allowed_root.join(path)
    };

    // Canonicalize the allowed root (it must exist).
    let canonical_root = allowed_root
        .canonicalize()
        .map_err(|e| format!("Cannot resolve config root: {e}"))?;

    // For the target path, canonicalize the parent (the file may not exist yet).
    let parent = resolved
        .parent()
        .ok_or_else(|| "Invalid path: no parent directory".to_string())?;
    let canonical_parent = parent
        .canonicalize()
        .map_err(|e| format!("Cannot resolve parent directory: {e}"))?;

    if !canonical_parent.starts_with(&canonical_root) {
        return Err(format!(
            "Path is outside allowed scope. Must be under {}",
            allowed_root.display()
        ));
    }

    Ok(resolved)
}

#[tauri::command]
pub async fn read_operator_config() -> Result<String, String> {
    let home = home_dir()?;
    let path = home.join(".aos/config/operator.yaml");
    let content =
        std::fs::read_to_string(&path).map_err(|e| format!("Failed to read operator config: {e}"))?;
    Ok(content)
}

#[tauri::command]
pub async fn read_trust_config() -> Result<String, String> {
    let home = home_dir()?;
    let path = home.join(".aos/config/trust.yaml");
    let content =
        std::fs::read_to_string(&path).map_err(|e| format!("Failed to read trust config: {e}"))?;
    Ok(content)
}

#[tauri::command]
pub async fn check_onboarding_status() -> Result<serde_json::Value, String> {
    let home = home_dir()?;
    let path = home.join(".aos/config/onboarding.yaml");

    if !path.exists() {
        return Ok(serde_json::json!({
            "exists": false,
            "completed": false
        }));
    }

    let content =
        std::fs::read_to_string(&path).map_err(|e| format!("Failed to read onboarding config: {e}"))?;

    // Simple check: look for "completed: true" in the YAML content.
    let completed = content
        .lines()
        .any(|line| {
            let trimmed = line.trim();
            trimmed == "completed: true" || trimmed == "completed: yes"
        });

    Ok(serde_json::json!({
        "exists": true,
        "completed": completed,
        "raw": content
    }))
}

#[tauri::command]
pub async fn write_config(path: String, content: String) -> Result<(), String> {
    let resolved = validate_config_path(&path)?;

    // Ensure parent directory exists.
    if let Some(parent) = resolved.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create parent directory: {e}"))?;
    }

    // Atomic write: write to a temp file in the same directory, then rename.
    let tmp_path = resolved.with_extension("tmp");
    std::fs::write(&tmp_path, &content)
        .map_err(|e| format!("Failed to write temp file: {e}"))?;
    std::fs::rename(&tmp_path, &resolved)
        .map_err(|e| format!("Failed to rename temp file: {e}"))?;

    Ok(())
}
