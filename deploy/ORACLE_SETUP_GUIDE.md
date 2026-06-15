# Oracle Cloud — 24/7 deployment guide

Run the trading system free, forever, on Oracle Cloud's Always-Free ARM tier
(Ampere A1). One-time ~30-minute setup; after that it runs unattended with a
permanent dashboard URL.

> Paper-first still applies. Deploy in PAPER/DEMO mode and only consider live
> trading after a consistent edge over 20+ trades (CLAUDE.md rule, getting-started step 4).

---

## 1. Create the VM (~10 min)

1. Sign in at https://cloud.oracle.com and open **Compute → Instances → Create instance**.
2. **Image & shape**: Canonical **Ubuntu 22.04**, shape **VM.Standard.A1.Flex**
   (Always Free eligible). 1–2 OCPU / 6–12 GB RAM is plenty.
3. **Networking**: create/keep a VCN with a public subnet; **assign a public IPv4**.
4. **SSH keys**: upload your public key (or let Oracle generate one and download it).
5. Create the instance and note its **public IP**.

The public IP is static for the life of the instance — this becomes your permanent
dashboard host (or use a Cloudflare tunnel, below, to avoid exposing ports).

## 2. Connect

```bash
ssh -i /path/to/your_key ubuntu@<PUBLIC_IP>
```

## 3. One-command setup

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/taher-ahmed-092/groww-swing-trader.git
cd groww-swing-trader
bash deploy/oracle_setup.sh
```

`oracle_setup.sh` installs `uv`, syncs dependencies, installs `cloudflared`,
creates a `systemd` service, and starts it. It does **not** create or overwrite
`.env` — you do that next.

## 4. Configure `.env`

```bash
cp .env.example .env
nano .env
```

Fill in at minimum:
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `DASHBOARD_SECRET_TOKEN`, `DASHBOARD_OWNER_NAME`
- `ANTHROPIC_API_KEY` (optional — without it the system runs in DEMO mode)
- `TRADING_MODE=balanced` (or `conserve` / `rogue`)

Leave `LIVE_TRADING_ENABLED=false`. Then restart the service:

```bash
sudo systemctl restart cosmic-punk
```

## 5. Dashboard access (pick one)

- **Cloudflare tunnel (recommended, no open ports):** the system starts
  `cloudflared` automatically and sends the dashboard URL to your Telegram on each
  start. Nothing else to configure.
- **Direct port:** if you prefer the static IP, open the dashboard port in BOTH
  the Oracle **security list** (ingress rule for the port in `.env`, default 8765)
  and the host firewall:
  ```bash
  sudo iptables -I INPUT -p tcp --dport 8765 -j ACCEPT
  sudo netfilter-persistent save   # if installed
  ```
  Then browse to `http://<PUBLIC_IP>:8765/dashboard/<DASHBOARD_SECRET_TOKEN>`.
  (The tunnel is safer — it avoids exposing the port to the internet.)

## 6. Operate

```bash
sudo systemctl status cosmic-punk      # is it running?
journalctl -u cosmic-punk -f           # live logs
sudo systemctl restart cosmic-punk     # after editing .env
sudo systemctl stop cosmic-punk        # halt
```

Emergency stop from anywhere: send `/kill` to the Telegram bot, or
`touch ~/groww-swing-trader/KILL_SWITCH`.

## 7. Updating

```bash
cd ~/groww-swing-trader
git pull
uv sync
sudo systemctl restart cosmic-punk
```

---

### Troubleshooting

- **Bot silent on start:** `uv run python scripts/verify_setup.py` — check token + chat id.
- **Dashboard unreachable:** confirm the tunnel URL in Telegram, or that the port is
  open in *both* the Oracle security list and the host firewall.
- **A1 capacity errors at create time:** Always-Free A1 capacity is regional and can be
  scarce — retry in another availability domain or later. (This is an Oracle capacity
  constraint, not a config issue.)
