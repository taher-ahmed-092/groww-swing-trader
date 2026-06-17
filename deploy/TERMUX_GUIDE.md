# Run Cosmic Punk on Android (Termux)

A spare Android phone is a great always-on, low-power host. This runs the full
system in PAPER/DEMO mode on the phone.

> Paper-first still applies — never set `LIVE_TRADING_ENABLED=true` here until you
> have a proven edge over 20+ trades.

---

## Why proot-distro (read this first)

The system depends on Rust/C packages — `pydantic-core`, `jiter`, `uuid-utils`,
`orjson`, `ormsgpack`, `greenlet`, `pynacl`, `httptools` — that are **mandatory**
(pydantic / langchain-core pull them; they can't be removed). On PyPI these ship
**glibc `manylinux_aarch64` wheels** but essentially **no Android wheels**.

- **Native Termux** uses **Bionic libc**, so pip rejects those wheels and tries to
  *compile from source* — which needs a Rust toolchain and still fails for
  `uuid-utils` (no `aarch64-linux-android` target). This path is painful and
  partially impossible.
- **proot-distro** runs a real **glibc Ubuntu** inside Termux. There, every wheel
  installs with **zero compilation** — no Rust, no build failures. This is the
  recommended path and the one below.

---

## 1. Install Termux + proot-distro

Install **Termux** (and optionally **Termux:Boot**) from **F-Droid** — the Play
Store builds are outdated. Open Termux once, then:

```bash
pkg update -y && pkg upgrade -y
pkg install -y proot-distro termux-api
proot-distro install ubuntu
```

## 2. Enter the glibc Ubuntu and set up

```bash
proot-distro login ubuntu
# --- now inside glibc Ubuntu (aarch64) ---
apt update && apt install -y python3 python3-pip git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

git clone https://github.com/taher-ahmed-092/groww-swing-trader.git
cd groww-swing-trader
uv sync          # all aarch64 wheels — no compilation
```

Prefer pip over uv? `pip install -r deploy/requirements-android.txt` (same set,
minus the optional ml/nlp extras).

## 3. Configure secrets

```bash
cp .env.example .env
nano .env   # TELEGRAM_*, DASHBOARD_*, optional ANTHROPIC_API_KEY; TRADING_MODE=balanced
```

Leave `LIVE_TRADING_ENABLED=false`.

## 4. Start it

```bash
uv run python scripts/start_everything.py
```

The dashboard URL is sent to your Telegram bot. Use `/learning` to watch the
24/7 replay engine grow the knowledge base.

## 5. Keep it alive + auto-start

Run the **wake-lock in the Termux (host) shell**, not inside proot, so Android
doesn't suspend the CPU:

```bash
# in the Termux host shell:
termux-wake-lock
```

Auto-start on boot with **Termux:Boot** — it logs into proot and launches the app:

```bash
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/cosmic_punk.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
proot-distro login ubuntu -- bash -lc \
  'cd ~/groww-swing-trader && ~/.local/bin/uv run python scripts/start_everything.py \
     >> ~/groww-swing-trader/logs/startup.log 2>&1'
EOF
chmod +x ~/.termux/boot/cosmic_punk.sh
```

Reboot once so Termux:Boot registers it.

- **Battery:** Android Settings → Apps → Termux → Battery → **Unrestricted**.
- **Updating:** inside proot — `cd ~/groww-swing-trader && git pull && uv sync`, then restart.

---

## Native Termux (advanced, not recommended)

If you insist on native Termux (no proot), you'll need a build toolchain and it may
still fail on `uuid-utils`:

```bash
pkg install -y python git rust binutils
bash deploy/termux_setup.sh   # attempts a native install
```

If `uuid-utils` (or another Rust package) fails to build, switch to the
proot-distro path above — it avoids all source builds.

### Troubleshooting
- **Bot silent:** inside proot, `uv run python scripts/verify_setup.py`.
- **Process dies when screen locks:** battery optimization still on, or no wake-lock.
- **Build errors mentioning Rust / cargo / `aarch64-linux-android`:** you're on
  native Termux — use proot-distro instead.
