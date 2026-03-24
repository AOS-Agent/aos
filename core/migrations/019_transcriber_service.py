"""
Migration 019: Set up the unified transcriber service.

Creates:
  - ~/.aos/services/transcriber/.venv with mlx-whisper + fastapi + uvicorn
  - Downloads whisper-large-v3-turbo-mlx model to HuggingFace cache

Cleans up:
  - Old ~/.aos/services/mlx-whisper/ standalone venv (if exists)
  - Old cached small models (base, small) — turbo replaces them all
"""

DESCRIPTION = "Set up unified transcriber service (Whisper Large V3 Turbo)"

import os
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
SERVICE_DIR = HOME / ".aos" / "services" / "transcriber"
VENV_DIR = SERVICE_DIR / ".venv"
SOURCE_DIR = HOME / "aos" / "core" / "services" / "transcriber"
OLD_MLX_VENV = HOME / ".aos" / "services" / "mlx-whisper"


def check() -> bool:
    """Applied if venv exists with mlx-whisper installed."""
    python = VENV_DIR / "bin" / "python"
    if not python.exists():
        return False

    # Verify mlx_whisper is importable
    result = subprocess.run(
        [str(python), "-c", "import mlx_whisper; import fastapi; print('ok')"],
        capture_output=True, text=True, timeout=15)
    return result.returncode == 0 and "ok" in result.stdout


def up() -> bool:
    """Create venv and install dependencies."""
    SERVICE_DIR.mkdir(parents=True, exist_ok=True)

    # Create venv
    if not VENV_DIR.exists():
        print("       Creating transcriber venv...")
        result = subprocess.run(
            ["uv", "venv", str(VENV_DIR), "--python", "3.14"],
            capture_output=True, text=True)
        if result.returncode != 0:
            # Fallback to system python
            result = subprocess.run(
                ["uv", "venv", str(VENV_DIR)],
                capture_output=True, text=True)
            if result.returncode != 0:
                print(f"       Failed to create venv: {result.stderr}")
                return False

    pip = VENV_DIR / "bin" / "pip"
    python = VENV_DIR / "bin" / "python"
    uv = shutil.which("uv")

    # Install dependencies from pyproject.toml
    pyproject = SOURCE_DIR / "pyproject.toml"
    if pyproject.exists() and uv:
        print("       Installing transcriber dependencies...")
        result = subprocess.run(
            [uv, "pip", "install", "--python", str(python),
             "-r", str(pyproject)],
            capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"       uv pip install failed: {result.stderr[:200]}")
            # Try direct install
            result = subprocess.run(
                [uv, "pip", "install", "--python", str(python),
                 "mlx-whisper>=0.4.0", "fastapi>=0.115.0", "uvicorn>=0.32.0",
                 "pyyaml>=6.0", "setproctitle>=1.3.0"],
                capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                print(f"       Direct install also failed: {result.stderr[:200]}")
                return False

    # Verify installation
    result = subprocess.run(
        [str(python), "-c", "import mlx_whisper; import fastapi; print('ok')"],
        capture_output=True, text=True, timeout=15)
    if result.returncode != 0 or "ok" not in result.stdout:
        print(f"       Verification failed: {result.stderr[:200]}")
        return False

    print("       Transcriber venv ready")

    # Pre-download the model (background-friendly)
    print("       Pre-downloading Whisper Large V3 Turbo model...")
    dl_result = subprocess.run(
        [str(python), "-c",
         "from huggingface_hub import snapshot_download; "
         "snapshot_download('mlx-community/whisper-large-v3-turbo-mlx')"],
        capture_output=True, text=True, timeout=600)
    if dl_result.returncode == 0:
        print("       Model downloaded/verified")
    else:
        print(f"       Model download deferred (will download on first use): {dl_result.stderr[:100]}")

    # Clean up old mlx-whisper standalone venv
    if OLD_MLX_VENV.exists():
        print("       Removing old mlx-whisper standalone venv...")
        shutil.rmtree(OLD_MLX_VENV, ignore_errors=True)

    return True
