use serde::Serialize;
use std::path::PathBuf;
use std::process::Command;

fn home_dir() -> Result<PathBuf, String> {
    dirs::home_dir().ok_or_else(|| "Could not determine home directory".to_string())
}

#[derive(Serialize)]
pub struct VaultFile {
    pub name: String,
    pub path: String,
    pub size: u64,
    pub modified: Option<u64>,
}

#[tauri::command]
pub async fn list_vault_files(collection: String) -> Result<Vec<VaultFile>, String> {
    let home = home_dir()?;
    let vault_dir = home.join("vault").join(&collection);

    if !vault_dir.exists() {
        return Err(format!("Vault collection not found: {collection}"));
    }

    // Validate the resolved path stays inside ~/vault/
    let canonical_vault = home
        .join("vault")
        .canonicalize()
        .map_err(|e| format!("Cannot resolve vault root: {e}"))?;
    let canonical_dir = vault_dir
        .canonicalize()
        .map_err(|e| format!("Cannot resolve collection path: {e}"))?;
    if !canonical_dir.starts_with(&canonical_vault) {
        return Err("Path traversal not allowed".to_string());
    }

    let mut files = Vec::new();
    let entries =
        std::fs::read_dir(&canonical_dir).map_err(|e| format!("Failed to read directory: {e}"))?;

    for entry in entries {
        let entry = entry.map_err(|e| format!("Failed to read entry: {e}"))?;
        let path = entry.path();

        if path.is_file() {
            if let Some(ext) = path.extension() {
                if ext == "md" || ext == "markdown" {
                    let metadata = entry
                        .metadata()
                        .map_err(|e| format!("Failed to read metadata: {e}"))?;

                    let modified = metadata
                        .modified()
                        .ok()
                        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                        .map(|d| d.as_secs());

                    files.push(VaultFile {
                        name: entry.file_name().to_string_lossy().to_string(),
                        path: path.to_string_lossy().to_string(),
                        size: metadata.len(),
                        modified,
                    });
                }
            }
        }
    }

    // Sort by modification time, newest first.
    files.sort_by(|a, b| b.modified.cmp(&a.modified));

    Ok(files)
}

#[tauri::command]
pub async fn read_vault_file(path: String) -> Result<String, String> {
    let home = home_dir()?;
    let canonical_vault = home
        .join("vault")
        .canonicalize()
        .map_err(|e| format!("Cannot resolve vault root: {e}"))?;

    let file_path = if path.starts_with("~/") {
        home.join(&path[2..])
    } else if path.starts_with('/') {
        PathBuf::from(&path)
    } else {
        home.join("vault").join(&path)
    };

    let canonical_file = file_path
        .canonicalize()
        .map_err(|e| format!("Cannot resolve file path: {e}"))?;

    if !canonical_file.starts_with(&canonical_vault) {
        return Err("Path must be within ~/vault/".to_string());
    }

    std::fs::read_to_string(&canonical_file)
        .map_err(|e| format!("Failed to read vault file: {e}"))
}

#[tauri::command]
pub async fn search_vault(
    query: String,
    collection: Option<String>,
    limit: Option<u32>,
) -> Result<String, String> {
    let qmd_path = "/Users/agentalhadi/.bun/bin/qmd";

    let mut args = vec!["query".to_string(), query];

    if let Some(col) = collection {
        args.push("-c".to_string());
        args.push(col);
    }

    let n = limit.unwrap_or(10);
    args.push("-n".to_string());
    args.push(n.to_string());

    // Output as JSON for easier frontend parsing.
    args.push("--json".to_string());

    let output = Command::new(qmd_path)
        .args(&args)
        .output()
        .map_err(|e| format!("Failed to execute qmd: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("qmd query failed: {stderr}"));
    }

    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}
