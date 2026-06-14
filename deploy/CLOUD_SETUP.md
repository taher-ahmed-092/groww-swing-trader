# Cloud & Remote Access Setup

See `CLOUD_OPTIONS.md` for running the 24/7 scheduler on a cloud VM (Oracle free tier
or a small Indian VPS) — that's what you need for live Groww trading (static IP).

This file covers **remote access to the read-only dashboard** from your phone.

## Dashboard (read-only)

Start locally:
```bash
# 1) generate a secret token (once)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# 2) add to .env:  DASHBOARD_SECRET_TOKEN=<paste>   DASHBOARD_OWNER_NAME=Syed
# 3) run
uv run python scripts/run_dashboard.py
```
Open: `http://localhost:8765/dashboard/<your_token>` — bookmark it.

The dashboard is **read-only**: it never exposes API keys, never places orders, and
is gated by the secret token in the URL. Treat that full URL like a password.

## Phone access — free permanent HTTPS (Cloudflare Tunnel)

```bash
bash deploy/setup_cloudflare_tunnel.sh        # prints an https://xxxx.trycloudflare.com URL
```
Then open `https://xxxx.trycloudflare.com/dashboard/<your_token>` on your phone.
For a URL that never changes, follow the "PERMANENT URL" steps the script prints
(free Cloudflare account → `cloudflared tunnel login/create`).

Why Cloudflare Tunnel over ngrok: free forever, permanent-URL option, no port
forwarding, and the tunnel is outbound-only (your laptop's ports stay closed).

## Telegram charts (optional)
With `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` set, the bot can push PNG charts
(`send_performance_chart`, `send_win_chart`) and the dashboard link
(`send_dashboard_link`) — no public URL needed.
