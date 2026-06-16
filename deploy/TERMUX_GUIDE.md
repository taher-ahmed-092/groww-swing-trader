# Run Cosmic Punk on Android (Termux)

A spare Android phone is a great always-on, low-power host. This runs the full
system in PAPER/DEMO mode on the phone itself.

> Paper-first still applies — never set `LIVE_TRADING_ENABLED=true` here until you
> have a proven edge over 20+ trades.

---

## 1. Install Termux (the right way)

Install **Termux** and **Termux:Boot** from **F-Droid** (the Play Store builds are
outdated and broken). https://f-droid.org → search "Termux" and "Termux:Boot".

Open Termux once so it finishes first-run setup.

## 2. One-command setup

```bash
pkg install -y git
git clone https://github.com/taher-ahmed-092/groww-swing-trader.git
cd groww-swing-trader
bash deploy/termux_setup.sh
```

This installs Python + git, installs `uv` (or falls back to pip), grabs a wake-lock,
clones the repo, and syncs dependencies.

## 3. Configure secrets

```bash
cp .env.example .env
nano .env   # fill TELEGRAM_*, DASHBOARD_*, optional ANTHROPIC_API_KEY; TRADING_MODE=balanced
```

Leave `LIVE_TRADING_ENABLED=false`.

## 4. Start it

```bash
termux-wake-lock                                  # keep the CPU awake
uv run python scripts/start_everything.py
```

The dashboard URL is sent to your Telegram bot. Use `/learning` to watch the
24/7 replay engine grow the knowledge base.

## 5. Auto-start on phone boot (Termux:Boot)

Create the boot script Termux:Boot runs at startup:

```bash
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/cosmic_punk.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
cd ~/groww-swing-trader
uv run python scripts/start_everything.py >> ~/groww-swing-trader/logs/startup.log 2>&1
EOF
chmod +x ~/.termux/boot/cosmic_punk.sh
```

Reboot once so Termux:Boot registers it. After that the trader starts on every boot.

## 6. Keep it alive

- **Battery:** Android Settings → Apps → Termux → Battery → **Unrestricted**.
- **Wake-lock:** the boot script calls `termux-wake-lock`; you can also tap the
  persistent Termux notification's "Acquire wakelock".
- **Updating:** `cd ~/groww-swing-trader && git pull && uv sync`, then restart.

### Troubleshooting
- **Process dies when screen locks:** battery optimization is still on for Termux,
  or the wake-lock wasn't acquired.
- **`uv` not found:** the script fell back to pip — use `python -m ...` or re-run
  `python -m pip install uv`.
- **Bot silent:** `uv run python scripts/verify_setup.py`.
