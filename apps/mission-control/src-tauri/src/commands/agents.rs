use serde::Serialize;
use std::path::PathBuf;

fn home_dir() -> Result<PathBuf, String> {
    dirs::home_dir().ok_or_else(|| "Could not determine home directory".to_string())
}

#[derive(Serialize)]
pub struct AgentMeta {
    pub name: String,
    pub path: String,
    pub frontmatter: Option<serde_json::Value>,
    pub content: String,
}

/// Parse YAML frontmatter from a markdown file.
/// Expects the file to start with "---\n", then YAML, then "---\n".
fn parse_frontmatter(text: &str) -> (Option<serde_json::Value>, &str) {
    if !text.starts_with("---") {
        return (None, text);
    }

    // Find the closing "---" after the opening one.
    let after_open = &text[3..];
    let after_open = after_open.strip_prefix('\n').unwrap_or(after_open);

    if let Some(end_idx) = after_open.find("\n---") {
        let yaml_str = &after_open[..end_idx];
        let body_start = end_idx + 4; // skip "\n---"
        let body = after_open.get(body_start..).unwrap_or("");
        let body = body.strip_prefix('\n').unwrap_or(body);

        // Try to parse YAML as JSON value. We do a simple approach:
        // serde_yaml would be ideal, but to keep dependencies minimal,
        // we'll convert to a JSON object with a "raw_yaml" field.
        // For a proper app, add serde_yaml. Here we include the raw YAML
        // and do basic key:value parsing for common frontmatter fields.
        let mut map = serde_json::Map::new();
        map.insert(
            "_raw".to_string(),
            serde_json::Value::String(yaml_str.to_string()),
        );

        for line in yaml_str.lines() {
            let trimmed = line.trim();
            if let Some((key, value)) = trimmed.split_once(':') {
                let key = key.trim().to_string();
                let value = value.trim().to_string();
                if !key.is_empty() && !key.starts_with('#') && !key.starts_with('-') {
                    map.insert(key, serde_json::Value::String(value));
                }
            }
        }

        (Some(serde_json::Value::Object(map)), body)
    } else {
        (None, text)
    }
}

#[tauri::command]
pub async fn list_agents() -> Result<Vec<AgentMeta>, String> {
    let home = home_dir()?;
    let agents_dir = home.join(".claude/agents");

    if !agents_dir.exists() {
        return Ok(Vec::new());
    }

    let mut agents = Vec::new();
    let entries =
        std::fs::read_dir(&agents_dir).map_err(|e| format!("Failed to read agents directory: {e}"))?;

    for entry in entries {
        let entry = entry.map_err(|e| format!("Failed to read entry: {e}"))?;
        let path = entry.path();

        if path.is_file() {
            if let Some(ext) = path.extension() {
                if ext == "md" || ext == "markdown" {
                    let content = std::fs::read_to_string(&path)
                        .map_err(|e| format!("Failed to read {}: {e}", path.display()))?;

                    let (frontmatter, body) = parse_frontmatter(&content);

                    let name = path
                        .file_stem()
                        .map(|s| s.to_string_lossy().to_string())
                        .unwrap_or_default();

                    agents.push(AgentMeta {
                        name,
                        path: path.to_string_lossy().to_string(),
                        frontmatter,
                        content: body.to_string(),
                    });
                }
            }
        }
    }

    agents.sort_by(|a, b| a.name.cmp(&b.name));

    Ok(agents)
}

#[tauri::command]
pub async fn get_agent(name: String) -> Result<AgentMeta, String> {
    let home = home_dir()?;

    // Prevent path traversal.
    if name.contains('/') || name.contains('\\') || name.contains("..") {
        return Err("Invalid agent name".to_string());
    }

    let path = home.join(format!(".claude/agents/{name}.md"));

    if !path.exists() {
        return Err(format!("Agent not found: {name}"));
    }

    let content = std::fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read agent file: {e}"))?;

    let (frontmatter, body) = parse_frontmatter(&content);

    Ok(AgentMeta {
        name,
        path: path.to_string_lossy().to_string(),
        frontmatter,
        content: body.to_string(),
    })
}
