"""Safe file I/O for AOS.

Atomic writes prevent data corruption when processes crash mid-write.
Every file write in AOS should use these functions instead of raw open().

Usage:
    from lib.safe_io import atomic_write, safe_yaml_dump, safe_yaml_load

    atomic_write("~/.aos/work/work.yaml", content_string)
    safe_yaml_dump("~/.aos/work/work.yaml", data_dict)
    data = safe_yaml_load("~/.aos/work/work.yaml")
"""

import os
import tempfile

import yaml


def atomic_write(path: str, content: str | bytes, mode: int = 0o644) -> None:
    """Write content to a file atomically.

    Writes to a temporary file in the same directory, then renames.
    If the process crashes mid-write, the original file is untouched.

    Args:
        path: Destination file path (~ expanded automatically).
        content: String or bytes to write.
        mode: File permission mode (default: 0o644).
    """
    path = os.path.expanduser(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)

    is_bytes = isinstance(content, bytes)

    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".aos_tmp_")
    try:
        if is_bytes:
            os.write(fd, content)
        else:
            os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = -1  # Mark as closed

        os.chmod(tmp_path, mode)
        os.rename(tmp_path, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def safe_yaml_dump(path: str, data, mode: int = 0o644) -> None:
    """Serialize data to YAML and write atomically.

    Args:
        path: Destination file path.
        data: Python object to serialize (dict, list, etc.).
        mode: File permission mode (default: 0o644).
    """
    content = yaml.dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    atomic_write(path, content, mode=mode)


def safe_yaml_load(path: str) -> dict | list | None:
    """Load and parse a YAML file safely.

    Returns None if file doesn't exist. Raises on parse errors.

    Args:
        path: File path to read.

    Returns:
        Parsed YAML data, or None if file not found.
    """
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return yaml.safe_load(f)


def safe_jsonl_append(path: str, record: dict) -> None:
    """Append a JSON record to a JSONL file atomically.

    Uses append mode with a trailing newline. Each write is a single
    os.write() call, which is atomic on POSIX for small writes.

    Args:
        path: JSONL file path.
        record: Dict to serialize as JSON.
    """
    import json

    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
