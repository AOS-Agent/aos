"""Config validation for AOS.

Validates instance config files and reports errors and warnings.
Does not crash on missing or malformed files — all issues are returned
as ValidationResult objects so callers can decide how to surface them.

Usage:
    from lib.validate import validate_all_configs

    results = validate_all_configs()  # validates ~/.aos/config/ by default
    for r in results:
        print(f"[{r.level}] {r.path}: {r.message}")
"""

import os
from dataclasses import dataclass

import yaml


@dataclass
class ValidationResult:
    path: str
    level: str  # "error" | "warning"
    message: str


# Keys we expect in operator.yaml. Unknown keys produce a warning.
_OPERATOR_REQUIRED = {"name", "timezone", "schedule"}
_OPERATOR_KNOWN = _OPERATOR_REQUIRED | {
    "communication", "daily_loop", "trust", "agent_name",
    "initiatives", "projects",
}


def _load_yaml(path: str) -> tuple[object, str | None]:
    """Load a YAML file. Returns (data, error_message)."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data, None
    except yaml.YAMLError as e:
        return None, f"YAML parse error: {e}"
    except OSError as e:
        return None, f"Cannot read file: {e}"


def _validate_operator(path: str) -> list[ValidationResult]:
    results = []

    if not os.path.exists(path):
        return [ValidationResult(path, "error", "operator.yaml is required but missing")]

    data, err = _load_yaml(path)
    if err:
        return [ValidationResult(path, "error", err)]
    if not isinstance(data, dict):
        return [ValidationResult(path, "error", "Expected a YAML mapping at top level")]

    for field in sorted(_OPERATOR_REQUIRED):
        if field not in data:
            results.append(ValidationResult(path, "error", f"Missing required field: {field}"))

    for key in data:
        if key not in _OPERATOR_KNOWN:
            results.append(ValidationResult(path, "warning", f"Unknown key: {key}"))

    return results


def _validate_crons(path: str) -> list[ValidationResult]:
    results = []

    if not os.path.exists(path):
        return [ValidationResult(path, "error", "crons.yaml not found")]

    data, err = _load_yaml(path)
    if err:
        return [ValidationResult(path, "error", err)]
    if not isinstance(data, dict):
        return [ValidationResult(path, "error", "Expected a YAML mapping at top level")]

    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        return [ValidationResult(path, "error", "Missing or invalid 'jobs' key")]

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            results.append(ValidationResult(path, "error", f"Job '{job_name}': expected a mapping"))
            continue

        command = job.get("command")
        if not command:
            results.append(ValidationResult(path, "error", f"Job '{job_name}': missing 'command' field"))
            continue

        # Extract the script path — first token after the interpreter
        tokens = str(command).split()
        script_path = None
        if len(tokens) >= 2 and tokens[0] in ("bash", "python3", "python"):
            script_path = tokens[1]
        elif tokens:
            script_path = tokens[0]

        if script_path:
            expanded = os.path.expanduser(script_path)
            if not os.path.exists(expanded):
                results.append(ValidationResult(
                    path, "warning",
                    f"Job '{job_name}': command file not found: {script_path}"
                ))

    return results


def _validate_bridge_topics(path: str) -> list[ValidationResult]:
    results = []

    if not os.path.exists(path):
        return []  # optional — skip silently

    data, err = _load_yaml(path)
    if err:
        return [ValidationResult(path, "error", err)]
    if not isinstance(data, dict):
        return [ValidationResult(path, "error", "Expected a YAML mapping at top level")]

    if "topics" not in data and "projects" not in data:
        results.append(ValidationResult(
            path, "warning",
            "Neither 'topics' nor 'projects' key found — file may be empty or malformed"
        ))

    return results


def validate_all_configs(
    instance_config: str = "~/.aos/config/",
    framework_config: str = "~/aos/config/",
) -> list[ValidationResult]:
    """Validate AOS config files.

    Args:
        instance_config: Path to instance config directory (default ~/.aos/config/).
        framework_config: Path to framework config directory (default ~/aos/config/).

    Returns:
        List of ValidationResult objects. Empty list means everything is valid.
    """
    instance = os.path.expanduser(instance_config)
    framework = os.path.expanduser(framework_config)

    results = []
    results += _validate_operator(os.path.join(instance, "operator.yaml"))
    results += _validate_crons(os.path.join(framework, "crons.yaml"))
    results += _validate_bridge_topics(os.path.join(instance, "bridge-topics.yaml"))
    return results
