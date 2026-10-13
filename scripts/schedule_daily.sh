#!/usr/bin/env bash
# Schedule scripts/quanta_daily.sh with launchd (macOS), Monday to Saturday.
#
#   scripts/schedule_daily.sh            install, 17:30 local time (any time works: the job
#                                        scans each completed New York session once)
#   scripts/schedule_daily.sh 18:15      install at another local time
#   scripts/schedule_daily.sh --remove   uninstall
#   scripts/schedule_daily.sh --status   show whether it is loaded and the last log
#
# The Mac must be awake (or set to wake) at that time; a missed run happens at next wake.
# launchd cannot read a checkout under ~/Documents, ~/Desktop or ~/Downloads unless
# /bin/bash has Full Disk Access (macOS privacy protection); elsewhere needs nothing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.quanta.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
[[ "$(uname)" == "Darwin" ]] || { echo "launchd is macOS only; on Linux add a cron line:"; echo "30 17 * * 1-5 $ROOT/scripts/quanta_daily.sh"; exit 1; }

case "${1:-}" in
    --remove)
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PLIST"; echo "Removed $LABEL."; exit 0 ;;
    --status)
        launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && echo "$LABEL is scheduled." || echo "$LABEL is not scheduled."
        last="$(ls -t "$ROOT"/logs/daily/*.log 2>/dev/null | head -1 || true)"
        [[ -n "$last" ]] && { echo "Last log: $last"; tail -5 "$last"; }
        exit 0 ;;
esac

TIME="${1:-17:30}"
[[ "$TIME" =~ ^([01]?[0-9]|2[0-3]):([0-5][0-9])$ ]] || { echo "Time must be HH:MM, got '$TIME'." >&2; exit 2; }
HOUR=$((10#${BASH_REMATCH[1]})); MIN=$((10#${BASH_REMATCH[2]}))

intervals=""
for wd in 1 2 3 4 5 6; do  # Saturday: zones ahead of New York scan Friday then
    intervals+="<dict><key>Weekday</key><integer>$wd</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>"
done

xml() { local s="${1//&/&amp;}"; s="${s//</&lt;}"; printf '%s' "${s//>/&gt;}"; }
XROOT="$(xml "$ROOT")"
mkdir -p "$(dirname "$PLIST")" "$ROOT/logs/daily"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$XROOT/scripts/quanta_daily.sh</string></array>
  <key>StartCalendarInterval</key><array>$intervals</array>
  <key>StandardOutPath</key><string>$XROOT/logs/daily/launchd.log</string>
  <key>StandardErrorPath</key><string>$XROOT/logs/daily/launchd.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
# bootstrap right after bootout can fail with "Input/output error" while launchd
# is still tearing the old job down; retry briefly.
for attempt in 1 2 3; do
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break
    [[ $attempt -eq 3 ]] && { launchctl bootstrap "gui/$(id -u)" "$PLIST"; exit 1; }
    sleep 1
done
printf 'Scheduled %s Monday-Saturday at %02d:%02d local time.\nLogs: %s/logs/daily/\nRun now: launchctl kickstart gui/%s/%s\n' \
    "$LABEL" "$HOUR" "$MIN" "$ROOT" "$(id -u)" "$LABEL"
