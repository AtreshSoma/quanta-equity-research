#!/usr/bin/env bash
# Double-click in Finder (or drag to the Dock) to start QUANTA.
# Close this Terminal window to stop the server.
exec "$(dirname "$0")/start.sh" "$@"
