#!/usr/bin/env bash
# One-shot setup for groww-swing-trader on a fresh Ubuntu 24.04 host.
# Usage:  curl -fsSL <raw-url>/deploy/setup_cloud.sh | bash
set -euo pipefail

REPO_URL="https://github.com/taher-ahmed-092/groww-swing-trader.git"
APP_DIR="$HOME/groww-swing-trader"

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y git curl build-essential

echo "==> Installing uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Cloning / updating repo"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
echo "==> Syncing dependencies"
uv sync

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created .env from template — EDIT IT before going live."
fi

echo "==> Installing systemd service"
SERVICE_SRC="$APP_DIR/deploy/groww_trader.service"
sudo cp "$SERVICE_SRC" /etc/systemd/system/groww_trader.service
sudo sed -i "s|__APP_DIR__|$APP_DIR|g; s|__USER__|$USER|g; s|__HOME__|$HOME|g" \
  /etc/systemd/system/groww_trader.service
sudo systemctl daemon-reload

echo "==> Done. Next steps:"
echo "    1) edit $APP_DIR/.env"
echo "    2) sudo systemctl enable --now groww_trader"
echo "    3) sudo journalctl -u groww_trader -f"
