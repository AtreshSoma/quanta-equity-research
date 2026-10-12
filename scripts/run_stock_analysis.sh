#!/usr/bin/env bash
set -euo pipefail
# Launcher: QUANTA Flask web app (macOS / Linux)
cd "$(dirname "$0")/../webapp"
echo "Starting the QUANTA web app from: $(pwd)"
python3 app.py
