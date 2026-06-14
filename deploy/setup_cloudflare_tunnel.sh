#!/usr/bin/env bash
# Cloudflare Tunnel — free, permanent HTTPS URL for your dashboard.
# Better than ngrok: permanent URL option, free forever, no port forwarding.
set -e
PORT="${1:-8765}"

echo "=== Cloudflare Tunnel Setup for Cosmic Punk Dashboard ==="
echo ""

if ! command -v cloudflared &> /dev/null; then
    echo "Installing cloudflared..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
        sudo dpkg -i /tmp/cloudflared.deb
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install cloudflare/cloudflare/cloudflared
    else
        echo "Unsupported OS — install cloudflared manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        exit 1
    fi
fi

echo ""
echo "Starting a temporary tunnel to the dashboard (port ${PORT})..."
echo "You'll get a random *.trycloudflare.com HTTPS URL — append /dashboard/<token>."
echo ""
echo "For a PERMANENT URL (recommended):"
echo "  1. Free account at cloudflare.com"
echo "  2. cloudflared tunnel login"
echo "  3. cloudflared tunnel create cosmic-punk-dashboard"
echo "  4. Docs: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/"
echo ""
cloudflared tunnel --url "http://localhost:${PORT}"
