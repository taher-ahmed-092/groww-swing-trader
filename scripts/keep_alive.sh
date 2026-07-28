#!/usr/bin/env bash
# Auto-restart wrapper for unattended operation.
#
#   bash scripts/keep_alive.sh
#
# Loops forever running scripts/start_everything.py. On a non-zero exit,
# logs the exit code + timestamp to logs/restarts.log and restarts after
# 15s. Caps at 20 restarts/hour to avoid a crash loop burning resources —
# once the cap is hit it waits out the rest of the hour before resuming.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs
RESTART_LOG="logs/restarts.log"
MAX_RESTARTS_PER_HOUR=20
RESTART_TIMESTAMPS=()

rotate_logs() {
    # Rotate any logs/*.log exceeding 20MB to .1 (overwriting any prior .1),
    # then truncate the live file so it keeps being written to in place.
    for f in logs/*.log; do
        [ -e "$f" ] || continue
        size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null || echo 0)
        if [ "$size" -gt $((20 * 1024 * 1024)) ]; then
            mv -f "$f" "$f.1"
            : > "$f"
        fi
    done
}

while true; do
    rotate_logs

    now=$(date +%s)
    fresh=()
    for ts in "${RESTART_TIMESTAMPS[@]:-}"; do
        [ -n "$ts" ] || continue
        if [ $((now - ts)) -lt 3600 ]; then
            fresh+=("$ts")
        fi
    done
    RESTART_TIMESTAMPS=("${fresh[@]:-}")

    if [ "${#RESTART_TIMESTAMPS[@]}" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) crash-loop guard: ${MAX_RESTARTS_PER_HOUR} restarts in the last hour — waiting 300s" >> "$RESTART_LOG"
        sleep 300
        continue
    fi

    "$(command -v uv || echo "$HOME/.local/bin/uv")" run python scripts/start_everything.py
    exit_code=$?

    if [ "$exit_code" -eq 0 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) start_everything.py exited cleanly (0) — stopping keep_alive." >> "$RESTART_LOG"
        break
    fi

    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) start_everything.py exited with code $exit_code — restarting in 15s" >> "$RESTART_LOG"
    RESTART_TIMESTAMPS+=("$(date +%s)")
    sleep 15
done
