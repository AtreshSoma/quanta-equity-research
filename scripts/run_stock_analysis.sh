#!/usr/bin/env bash
# Kept for existing habits and docs: the macOS / Linux launcher is now ./start.sh,
# which also creates the environment and installs what is missing.
exec "$(dirname "$0")/../start.sh" "$@"
