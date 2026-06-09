# Cloud Deployment Options

The scheduler (`runner.py`) is a long-running process. To run it 24/7 you need an
always-on host. Two India-friendly options:

## Option A — Oracle Cloud Free Tier (recommended, ₹0)
- **Always Free** Ampere A1 (ARM) VM: up to 4 OCPU / 24 GB RAM, or a small AMD VM.
- Gives you a **static public IP** — which Groww requires for live order placement.
- Sign up: https://www.oracle.com/cloud/free/ → create an Ubuntu 24.04 instance.
- Reserve the public IP (Networking → Reserved Public IPs) so it never changes.

## Option B — GigaNodes / Indian VPS (~₹500/month, UPI)
- Small VPS (1 vCPU / 1 GB) is plenty for this workload.
- Pay via UPI; pick a plan that offers a **dedicated/static IP**.
- Whitelist that IP in the Groww developer portal.

## Setup (either option)
```bash
# on the fresh Ubuntu 24.04 box, as a sudo user
curl -fsSL https://raw.githubusercontent.com/taher-ahmed-092/groww-swing-trader/main/deploy/setup_cloud.sh | bash
# then edit the .env it created, and:
sudo systemctl enable --now groww_trader
sudo journalctl -u groww_trader -f   # watch logs
```

## Notes
- Keep `LIVE_TRADING_ENABLED=false` until your paper edge is proven.
- The static IP is the gating requirement for live trading — verify it's whitelisted
  with Groww before flipping the live switch.
- The `KILL_SWITCH` file halts everything; `systemctl stop groww_trader` stops the service.
