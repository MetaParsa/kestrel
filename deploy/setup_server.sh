#!/usr/bin/env bash
# One-time server setup (Ubuntu 26.04, Iran). Run as root FROM THE REPO ROOT:
#   sudo bash deploy/setup_server.sh
# Idempotent. Never prints secrets. Starts the engine in SHADOW mode only (no key needed
# for shadow: market data is public). Live trading is a separate, explicit step (C5).
#
# The server may be shared with other apps: this script changes nothing system-wide
# (no timezone change — our services set TZ themselves — no firewall, no apt upgrades).
set -euo pipefail
[ -f pyproject.toml ] && [ -f engine/loop.py ] || { echo "run this from the wallex-agent repo root"; exit 1; }
APP=/opt/wallex-agent
MIRROR=https://package-mirror.liara.ir/repository/pypi/simple
PUSH_USER=${SUDO_USER:-ubuntu}      # the SSH login the laptop uses to push playbooks

python3 -m venv --help >/dev/null 2>&1 || apt-get install -y python3-venv >/dev/null
command -v rsync >/dev/null || apt-get install -y rsync >/dev/null

id trader >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash trader
usermod -aG trader "$PUSH_USER"
mkdir -p "$APP" /etc/wallex-agent
# code only: never .git, venvs, run state, data, or anything that could hold a secret
rsync -a --delete \
  --exclude .git --exclude .venv --exclude run --exclude data/store --exclude logs \
  --exclude '*.key' --exclude 'api.txt' --exclude '.env*' --exclude secrets \
  ./ "$APP"/
mkdir -p "$APP/data/store"
[ -d data/store ] && rsync -a data/store/ "$APP/data/store/" || true
for d in main baseline; do mkdir -p "$APP/run/$d/inbox" "$APP/run/$d/outbox" "$APP/run/$d/logs"; done

[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
PIP="$APP/.venv/bin/pip"
$PIP install --quiet --index-url "$MIRROR" --upgrade pip
# the engine runs as `python -m ...` from $APP, so only the dependencies are installed
$PIP install --quiet --index-url "$MIRROR" httpx pydantic pandas pyarrow numpy fastapi uvicorn tzdata
$PIP install --quiet --index-url "$MIRROR" lightgbm scikit-learn \
  || echo "NOTE: lightgbm/scikit-learn not installable here (only needed for the tactical sleeve, step T1/T2)"

[ -f /etc/wallex-agent/env ] || cp "$APP/deploy/env.example" /etc/wallex-agent/env
chown -R trader:trader "$APP" /etc/wallex-agent
# the laptop (as $PUSH_USER, group trader) reads outbox/ and writes inbox/; setgid keeps files in group trader
chmod 750 "$APP/run" "$APP/run/main" "$APP/run/baseline"
chmod 2770 "$APP/run/main/inbox" "$APP/run/baseline/inbox"
chmod 2750 "$APP/run/main/outbox" "$APP/run/baseline/outbox"
chmod 700 /etc/wallex-agent
chmod 600 /etc/wallex-agent/env
if [ -f /etc/wallex-agent/wallex.key ]; then chmod 600 /etc/wallex-agent/wallex.key
else echo "NOTE: no /etc/wallex-agent/wallex.key yet — fine for shadow mode; needed before go-live"; fi

cp "$APP/deploy/"wallex-*.service "$APP/deploy/"wallex-*.timer /etc/systemd/system/
cp "$APP/deploy/logrotate.conf" /etc/logrotate.d/wallex-agent
systemctl daemon-reload
systemctl enable wallex-agent.service wallex-shadow.service wallex-status.service wallex-watchdog.timer
systemctl restart wallex-agent.service wallex-shadow.service wallex-status.service
systemctl start wallex-watchdog.timer
sleep 15
systemctl --no-pager --lines=5 status wallex-agent.service wallex-shadow.service || true
echo "Setup done: engine + baseline running in SHADOW mode. Status: ssh -L 8090:127.0.0.1:8090 $PUSH_USER@<server>"
