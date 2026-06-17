#!/data/data/com.termux/files/usr/bin/bash
# Cosmic Punk on Android — NATIVE Termux install (advanced, not recommended).
#
# The mandatory Rust deps (pydantic-core, uuid-utils, jiter, ...) have no Android
# wheels, so this path compiles from source and needs `pkg install rust`; uuid-utils
# can still fail on the aarch64-android target. The RELIABLE path is proot-distro
# (glibc Ubuntu), where every wheel installs without compiling — see TERMUX_GUIDE.md.
set -e

echo "=== Cosmic Punk — Termux setup ==="

# 1. Base packages.
pkg update -y && pkg upgrade -y
pkg install -y python git termux-api

# 2. uv (Astral). If no Android wheel is available, fall back to pip for deps.
python -m pip install --upgrade pip
python -m pip install uv || echo "uv pip install failed — will fall back to pip."

# 3. Keep the CPU awake so Android doesn't suspend the process.
termux-wake-lock || true

# 4. Clone (or update) the repo.
REPO_DIR="$HOME/groww-swing-trader"
if [ ! -d "$REPO_DIR/.git" ]; then
    git clone https://github.com/taher-ahmed-092/groww-swing-trader.git "$REPO_DIR"
else
    git -C "$REPO_DIR" pull --ff-only || true
fi
cd "$REPO_DIR"

# 5. Dependencies.
if command -v uv >/dev/null 2>&1; then
    uv sync
else
    python -m pip install -r <(python - <<'PY'
import tomllib, sys
data = tomllib.load(open("pyproject.toml","rb"))
for dep in data.get("project", {}).get("dependencies", []):
    print(dep)
PY
)
fi

echo ""
echo "=== Setup complete ==="
echo "1. Configure secrets:   cp .env.example .env && nano .env"
echo "2. Start the system:    uv run python scripts/start_everything.py"
echo "3. Auto-start on boot:   install the Termux:Boot add-on (see TERMUX_GUIDE.md)"
echo "Tip: keep 'termux-wake-lock' active so the process isn't suspended."
