#!/usr/bin/env bash
# QUANTA web app launcher for macOS and Linux (the counterpart of start.ps1).
#
#   ./start.sh                 create .venv if needed, install what is missing, serve on :5500
#   ./start.sh --port 5600     serve on another port
#   ./start.sh --no-install    skip the dependency check
#   ./start.sh --reinstall     force a full dependency reinstall
#   ./start.sh --no-browser    do not open a browser tab
#
# Double-click "Start QUANTA.command" in Finder for the same thing without a terminal.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PORT="${QUANTA_PORT:-5500}"
INSTALL=1
REINSTALL=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) [[ $# -ge 2 ]] || { echo "--port needs a value" >&2; exit 2; }; PORT="$2"; shift 2 ;;
        --no-install) INSTALL=0; shift ;;
        --reinstall) REINSTALL=1; shift ;;
        --no-browser) export QUANTA_NO_BROWSER=1; shift ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

cyan() { printf '\033[36m%s\033[0m\n' "$*"; }
grey() { printf '\033[90m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }

# --- Python environment ------------------------------------------------------
# Always run from the project's own .venv so system Python upgrades cannot
# break the app. Created on first launch from the newest python3 on PATH.
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
if [[ ! -x "$PY" ]]; then
    command -v python3 >/dev/null || { echo "python3 was not found. Install Python 3.11+ first." >&2; exit 1; }
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
        || { echo "QUANTA needs Python 3.11+; python3 is $(python3 -V 2>&1)." >&2; exit 1; }
    cyan "Creating the project environment in .venv (first launch only) ..."
    python3 -m venv "$VENV"
    REINSTALL=1
fi

# --- Dependencies ------------------------------------------------------------
# Same contract as start.ps1: the cache marker is a hash of requirements.txt and
# pyproject.toml, and the fast path also probes that every critical module
# imports, so a half-finished install is never remembered as success.
CRITICAL=(flask numpy pandas requests rich ta yfinance vaderSentiment dateutil scipy)
PROBE='import importlib.util as u,sys; m=[n for n in sys.argv[1:] if u.find_spec(n) is None]; print(", ".join(m)); sys.exit(len(m))'
MARKER="$VENV/.quanta_reqs_hash"

if [[ $INSTALL -eq 1 ]]; then
    if command -v shasum >/dev/null; then SHA=(shasum -a 256); else SHA=(sha256sum); fi
    HASH="$(cat webapp/requirements.txt pyproject.toml | "${SHA[@]}" | cut -d' ' -f1)"
    CACHED="$(cat "$MARKER" 2>/dev/null || true)"
    MISSING="$("$PY" -c "$PROBE" "${CRITICAL[@]}" || true)"
    if [[ $REINSTALL -eq 0 && "$HASH" == "$CACHED" && -z "$MISSING" ]]; then
        grey "Dependencies satisfied."
    else
        [[ -n "$MISSING" ]] && warn "Missing Python modules: $MISSING"
        cyan "Installing QUANTA dependencies (a minute or two the first time) ..."
        "$PY" -m pip install -q --upgrade pip || warn "Could not upgrade pip (offline?); continuing."
        "$PY" -m pip install -q -e ".[webapp,providers]" || "$PY" -m pip install -q -e ".[webapp]" \
            || warn "Dependency install failed; checking what already imports."
        if "$PY" -c "$PROBE" "${CRITICAL[@]}" >/dev/null; then
            printf '%s' "$HASH" > "$MARKER"
            grey "All dependencies verified."
        else
            warn "Some modules still fail to import; analysis requests may error."
        fi
    fi
fi

# --- Settings ----------------------------------------------------------------
# Keys in the git-ignored .env (see .env.example) reach the engines as
# environment variables, however the server was started.
. "$ROOT/scripts/load_env.sh"
load_env "$ROOT/.env"
if [[ -z "${QUANTA_SEC_CONTACT:-}${QUANTA_SEC_USER_AGENT:-}" ]]; then
    warn "QUANTA_SEC_CONTACT is not set: the corporate-event check is unavailable and no Picks name can be funded."
    warn "Fix: cp .env.example .env, put your email address on the QUANTA_SEC_CONTACT line, restart."
fi

export QUANTA_PORT="$PORT" QUANTA_HOST="127.0.0.1" PYTHONUTF8=1

echo
grey "============================================================"
echo " QUANTA Elite"
cyan " http://127.0.0.1:$PORT"
grey " The browser opens automatically. Press Ctrl+C to stop."
grey "============================================================"
echo

exec "$PY" webapp/app.py
