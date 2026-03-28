use serde::Serialize;
use std::process::Command;

/// Known AOS services managed via launchctl.
const AOS_SERVICES: &[&str] = &[
    "com.aos.bridge",
    "com.aos.dashboard",
    "com.aos.eventd",
    "com.aos.listen",
    "com.aos.transcriber",
    "com.aos.whatsmeow",
];

#[derive(Serialize)]
pub struct ServiceStatus {
    pub name: String,
    pub label: String,
    pub running: bool,
    pub pid: Option<i64>,
}

#[tauri::command]
pub async fn get_service_status() -> Result<Vec<ServiceStatus>, String> {
    let mut statuses = Vec::new();

    for &service in AOS_SERVICES {
        let output = Command::new("/bin/launchctl")
            .args(["list", service])
            .output();

        let (running, pid) = match output {
            Ok(out) if out.status.success() => {
                let stdout = String::from_utf8_lossy(&out.stdout);
                // launchctl list <label> outputs lines like:
                //   "PID" = 12345;
                // or shows the full plist. A simpler heuristic: if the command
                // succeeds, the service is loaded. Parse PID from first line.
                let pid = stdout
                    .lines()
                    .find_map(|line| {
                        let trimmed = line.trim();
                        if trimmed.starts_with("\"PID\"") || trimmed.starts_with("PID") {
                            // Try to find a number in the line.
                            trimmed
                                .split(|c: char| !c.is_ascii_digit() && c != '-')
                                .find_map(|s| s.parse::<i64>().ok())
                        } else {
                            None
                        }
                    });
                (true, pid)
            }
            _ => (false, None),
        };

        // Derive a human-readable label from the service identifier.
        let label = service
            .strip_prefix("com.aos.")
            .unwrap_or(service)
            .to_string();

        statuses.push(ServiceStatus {
            name: service.to_string(),
            label,
            running,
            pid,
        });
    }

    Ok(statuses)
}

#[tauri::command]
pub async fn restart_service(name: String) -> Result<String, String> {
    // Validate the service name against known services.
    if !AOS_SERVICES.contains(&name.as_str()) {
        return Err(format!("Unknown service: {name}. Must be one of the known AOS services."));
    }

    let uid = unsafe { libc::getuid() };
    let target = format!("gui/{uid}/{name}");

    let output = Command::new("/bin/launchctl")
        .args(["kickstart", "-k", &target])
        .output()
        .map_err(|e| format!("Failed to execute launchctl kickstart: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("Failed to restart {name}: {stderr}"));
    }

    Ok(format!("Service {name} restarted successfully"))
}

#[tauri::command]
pub async fn get_health() -> Result<String, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .map_err(|e| format!("Failed to create HTTP client: {e}"))?;

    let response = client
        .get("http://localhost:4097/health")
        .send()
        .await
        .map_err(|e| format!("Health check failed: {e}"))?;

    let status = response.status();
    let body = response
        .text()
        .await
        .map_err(|e| format!("Failed to read health response: {e}"))?;

    if !status.is_success() {
        return Err(format!("Health endpoint returned {status}: {body}"));
    }

    Ok(body)
}
