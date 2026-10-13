#!/usr/bin/env bash
# QUANTA daily paper-trading job: builds the public track record one session at a time.
#
#   1. resolve   score every call whose horizon has elapsed (excess return vs SPY / Nifty)
#   2. scan      run the short-term scanner; its calls enter the ledger, immutably
#   3. alerts    report stops / targets touched by open calls (macOS notification)
#   4. report    write the benchmarked track record to the log
#   5. publish   export the tamper-evident record to track_record/ (optionally commit it)
#
# Run it by hand, or schedule it after the US close with scripts/schedule_daily.sh.
# Settings (environment or .env): QUANTA_DAILY_CAPITAL (2500), QUANTA_DAILY_SCAN
# (standard), QUANTA_DAILY_POSITIONS (5). Logs: logs/daily/<date>.log
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "No .venv yet: run ./start.sh once first." >&2; exit 1; }
. "$ROOT/scripts/load_env.sh"
load_env "$ROOT/.env"

export PYTHONUTF8=1 PYTHONPATH="$ROOT/core${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p logs/daily
LOG="logs/daily/$(date +%F).log"
exec >>"$LOG" 2>&1

step() { printf '\n=== %s  %s ===\n' "$(date '+%F %T')" "$*"; }
status=0

step "resolve"
"$PY" -m tracking resolve || status=1

# Scan once per completed New York session, whatever the local time zone: the
# job may run at 17:30 in New York, or the next morning in Europe or India.
# Weekends and holidays add no new session, so the scan is skipped then.
MARKER="logs/daily/.last_scanned_session"
SESSION="$("$PY" -c 'from engine.market_calendar import last_completed_session as l; print(l())')" \
    || { step "scan skipped: could not read the market calendar"; SESSION=""; status=1; }
if [[ -n "$SESSION" && "$SESSION" != "$(cat "$MARKER" 2>/dev/null || true)" ]]; then
    step "scan (session $SESSION)"
    if "$PY" core/SP_ShortTerm.py -y --no-export \
        --capital "${QUANTA_DAILY_CAPITAL:-2500}" \
        --scan "${QUANTA_DAILY_SCAN:-standard}" \
        --max-positions "${QUANTA_DAILY_POSITIONS:-5}"; then
        printf '%s' "$SESSION" > "$MARKER"
    else
        status=1
    fi
elif [[ -n "$SESSION" ]]; then
    step "scan skipped: session $SESSION was already scanned"
fi

step "alerts"
"$PY" -m tracking alerts --notify || status=1

step "report"
"$PY" -m tracking report || status=1

# Export the hash-chained public record. Set QUANTA_PUBLISH_COMMIT=1 to also
# commit it, so git timestamps each day's link (it is never pushed for you).
step "publish"
"$PY" -m tracking publish || status=1
if [[ "${QUANTA_PUBLISH_COMMIT:-0}" == "1" ]]; then
    git add track_record
    git diff --cached --quiet -- track_record \
        || git commit -q -m "track record: $(date +%F)" -- track_record || status=1
fi

step "done (status $status)"
exit $status
