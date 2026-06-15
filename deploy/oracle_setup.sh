#!/bin/bash
# One-command Oracle Cloud (ARM Ubuntu) deployment for Cosmic Punk.
# Installs uv + deps + cloudflared, creates a systemd service, starts it.
# Does NOT touch .env — configure that yourself (see deploy/ORACLE_SETUP_GUIDE.md).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="cosmic-punk"
USER_NAME="$(whoami)"

echo "=== Cosmic Punk — Oracle deployment ==="
echo "Repo: $REPO_DIR  User: $USER_NAME"

# 1. System deps.
sudo apt-get update
sudo apt-get install -y curl git ca-certificates

# 2. uv (Astral) — user-local install.
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV_BIN="$(command -v uv || echo "$HOME/.local/bin/uv")"
echo "uv: $UV_BIN"

# 3. Python deps.
cd "$REPO_DIR"
"$UV_BIN" sync

# 4. cloudflared (ARM64) for the tunnel — best-effort, dashboard works locally without it.
if ! command -v cloudflared >/dev/null 2>&1; then
    ARCH="$(dpkg --print-architecture || echo arm64)"
    URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}"
    if curl -fsSL "$URL" -o /tmp/cloudflared; then
        sudo install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared
        echo "cloudflared installed."
    else
        echo "cloudflared download failed — dashboard will be local-only."
    fi
fi

# 5. systemd service.
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
echo "Writing $SERVICE_PATH"
sudo tee "$SERVICE_PATH" >/dev/null <<UNIT
[Unit]
Description=Cosmic Punk Trading System
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${REPO_DIR}
ExecStart=${UV_BIN} run python scripts/start_everything.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

if [ ! -f "$REPO_DIR/.env" ]; then
    echo ""
    echo ">>> No .env found. Create it before starting:"
    echo "    cp .env.example .env && nano .env"
    echo ">>> Then: sudo systemctl start $SERVICE_NAME"
else
    sudo systemctl restart "$SERVICE_NAME"
    echo "Service started. Logs: journalctl -u $SERVICE_NAME -f"
fi

echo "=== Done ==="
