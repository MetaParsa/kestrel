#!/usr/bin/env bash
# One-time server setup (Ubuntu 26.04, Iran). Run as root FROM THE REPO ROOT:
#   sudo bash deploy/setup_server.sh
# Idempotent. Never prints secrets. Starts the engine in SHADOW mode only (no key needed
# for shadow: market data is public). Live trading is a separate, explicit step (C5).
set -euo pipefail
[ -f pyproject.toml ] && [ -f engine/loop.py ] || { echo "run this from the wallex-agent repo root"; exit 1; }
APP=/opt/wallex-agent
MIRROR=https://package-mirror.liara.ir/repository/pypi/simple

timedatectl set-timezone Asia/Tehran
timedatectl set-ntp true || true

id trader >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash trader
mkdir -p "$APP" /etc/wallex-agent
# code only: never .git, venvs, run state, data, or anything that could hold a secret
rsync -a --delete \
  --exclude .git --exclude .venv --exclude run --exclude data/store --exclude logs \
  --exclude '*.key' --exclude 'api.txt' --exclude '.env*' --exclude secrets \
  ./ "$APP"/
mkdir -p "$APP/run/main" "$APP/run/baseline" "$APP/data/store"
[ -d data/store ] && rsync -a data/store/ "$APP/data/store/" || true

apt-get install -y python3 python3-venv rsync logrotate >/dev/null
python3 -m venv "$APP/.venv"
PIP="$APP/.venv/bin/pip"
$PIP install --quiet --index-url "$MIRROR" --upgrade pip
# the engine runs as `python -m ...` from $APP, so only the dependencies are installed
$PIP install --quiet --index-url "$MIRROR" httpx pydantic pandas pyarrow numpy fastapi uvicorn tzdata
$PIP install --quiet --index-url "$MIRROR" lightgbm scikit-learn \
  || echo "NOTE: lightgbm/scikit-learn not installable here (only needed for the tactical sleeve, step T1/T2)"

[ -f /etc/wallex-agent/env ] || cp "$APP/deploy/env.example" /etc/wallex-agent/env
chown -R trader:trader "$APP" /etc/wallex-agent
chmod 700 /etc/wallex-agent
chmod 600 /etc/wallex-agent/env
if [ -f /etc/wallex-agent/wallex.key ]; then chmod 600 /etc/wallex-agent/wallex.key
else echo "NOTE: no /etc/wallex-agent/wallex.key yet — fine for shadow mode; needed before go-live"; fi

cp "$APP/deploy/"wallex-*.service "$APP/deploy/"wallex-*.timer /etc/systemd/system/
cp "$APP/deploy/logrotate.conf" /etc/logrotate.d/wallex-agent
systemctl daemon-reload
systemctl enable --now wallex-agent.service wallex-shadow.service wallex-status.service wallex-watchdog.timer
systemctl --no-pager --lines=0 status wallex-agent.service wallex-shadow.service || true
echo "Setup done: engine + baseline running in SHADOW mode. Status: ssh -L 8090:127.0.0.1:8090 <user>@<server>"
