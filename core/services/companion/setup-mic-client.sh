#!/bin/bash
# AOS Mic Client — one-command setup for MacBook
# Run from MacBook terminal:
#   curl -sL http://100.112.113.53:7603/mic-client/setup | bash
#
# What this does:
#   1. Creates ~/aos-mic/ with a venv
#   2. Installs sounddevice, numpy, onnxruntime, websockets
#   3. Downloads mic-client.py from the Mac Mini
#   4. Downloads Silero VAD model
#   5. Starts streaming

set -e

MAC_MINI="100.112.113.53"
PORT="7603"
DIR="$HOME/aos-mic"

echo ""
echo "  AOS Mic Client Setup"
echo "  ─────────────────────"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "  ERROR: python3 not found. Install Python first."
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python: $PY_VERSION"

# Check connectivity
echo "  Checking Mac Mini ($MAC_MINI:$PORT)..."
if ! curl -sf "http://$MAC_MINI:$PORT/health" >/dev/null 2>&1; then
    echo "  ERROR: Cannot reach companion service at $MAC_MINI:$PORT"
    echo "  Make sure Tailscale is connected and companion is running."
    exit 1
fi
echo "  Connected!"

# Create directory
echo "  Setting up $DIR..."
mkdir -p "$DIR"
cd "$DIR"

# Create venv if needed
if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv .venv
fi

# Install deps
echo "  Installing dependencies..."
.venv/bin/pip install -q sounddevice numpy onnxruntime websockets 2>&1 | tail -1

# Download mic-client.py
echo "  Downloading mic-client.py..."
curl -sf "http://$MAC_MINI:$PORT/mic-client/script" -o mic-client.py

# Download VAD model
mkdir -p "$HOME/.aos/models"
if [ ! -f "$HOME/.aos/models/silero_vad.onnx" ]; then
    echo "  Downloading Silero VAD model..."
    curl -sfL "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx" -o "$HOME/.aos/models/silero_vad.onnx"
fi

echo ""
echo "  ✓ Setup complete!"
echo ""
echo "  To start streaming:"
echo "    cd ~/aos-mic && .venv/bin/python3 mic-client.py"
echo ""
echo "  To list mic devices:"
echo "    cd ~/aos-mic && .venv/bin/python3 mic-client.py --list-devices"
echo ""
echo "  Starting now..."
echo ""

.venv/bin/python3 mic-client.py --host "$MAC_MINI" --port "$PORT"
