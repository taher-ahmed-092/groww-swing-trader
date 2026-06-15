#!/bin/bash
# Sets up the Cosmic Punk trading system to start automatically on boot/login.
# Idempotent: safe to re-run. No terminal needed after setup.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_BIN="$(command -v uv || echo "$HOME/.local/bin/uv")"

echo "Setting up auto-start for Cosmic Punk Trading System"
echo "Repo: $REPO_DIR"
echo "uv:   $UV_BIN"

mkdir -p "$REPO_DIR/logs"

# Startup script the OS will invoke.
cat > "$HOME/.cosmic_punk_startup.sh" <<STARTUP
#!/bin/bash
cd "$REPO_DIR"
sleep 15  # wait for network on boot
"$UV_BIN" run python scripts/start_everything.py >> "$REPO_DIR/logs/startup.log" 2>&1 &
echo "Cosmic Punk started at \$(date)" >> "$REPO_DIR/logs/startup.log"
STARTUP
chmod +x "$HOME/.cosmic_punk_startup.sh"

if grep -qi microsoft /proc/version 2>/dev/null; then
    echo ""
    echo "WSL detected. To auto-start on Windows login:"
    echo "  1. Press Win+R, type: shell:startup"
    echo "  2. Copy the generated cosmic_punk.bat (below) into that folder."
    cat > /tmp/cosmic_punk.bat <<'BAT'
@echo off
wsl -d Ubuntu -e bash -c "bash ~/.cosmic_punk_startup.sh"
BAT
    echo "  BAT file written to /tmp/cosmic_punk.bat"
    echo "  (If your distro isn't 'Ubuntu', edit the -d name in the .bat.)"
else
    # Native Linux: register an @reboot crontab entry (de-duplicated).
    CRON_LINE="@reboot bash $HOME/.cosmic_punk_startup.sh"
    ( crontab -l 2>/dev/null | grep -vF "$HOME/.cosmic_punk_startup.sh" ; echo "$CRON_LINE" ) | crontab -
    echo "Added @reboot crontab entry."
fi

echo ""
echo "=== Auto-start configured ==="
echo "The trading system will start automatically on next boot/login."
echo "Logs: $REPO_DIR/logs/startup.log"
echo "Tip: also add an hourly keep-alive cron:"
echo "  (crontab -l; echo \"0 * * * * bash $REPO_DIR/scripts/keep_alive.sh\") | crontab -"
