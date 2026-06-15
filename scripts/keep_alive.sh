#!/bin/bash
# Keep-alive watchdog: if start_everything.py isn't running, restart it.
# Intended to be run hourly from cron:
#   0 * * * * bash /path/to/repo/scripts/keep_alive.sh
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$REPO_DIR/logs"

if ! pgrep -f "start_everything.py" > /dev/null 2>&1; then
    echo "$(date): system not running — restarting" >> "$REPO_DIR/logs/keep_alive.log"
    if [ -x "$HOME/.cosmic_punk_startup.sh" ]; then
        bash "$HOME/.cosmic_punk_startup.sh"
    else
        cd "$REPO_DIR"
        "$(command -v uv || echo "$HOME/.local/bin/uv")" run python scripts/start_everything.py \
            >> "$REPO_DIR/logs/startup.log" 2>&1 &
    fi
fi
