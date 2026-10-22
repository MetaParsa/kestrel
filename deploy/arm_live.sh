#!/usr/bin/env bash
# Switch the main engine to REAL trading (both locks) — only after the user says "go live".
#   sudo bash /opt/wallex-agent/deploy/arm_live.sh          # asks you to type the phrase
# Sets WALLEX_LIVE_ARMED=yes-real-money and WALLEX_MODE=live in /etc/wallex-agent/env and restarts.
# A later redeploy keeps both (they live in the env file, not the unit file).
set -euo pipefail
ENV=/etc/wallex-agent/env
[ -f /etc/wallex-agent/wallex.key ] || { echo "no Wallex key on the server"; exit 1; }
read -rp "Type yes-real-money to trade with real money: " ANS
[ "$ANS" = "yes-real-money" ] || { echo "not armed"; exit 1; }
TMP=$(mktemp /etc/wallex-agent/.env.XXXXXX)
grep -v -e '^WALLEX_LIVE_ARMED=' -e '^WALLEX_MODE=' "$ENV" > "$TMP" || true
printf 'WALLEX_LIVE_ARMED=yes-real-money\nWALLEX_MODE=live\n' >> "$TMP"
chown trader:trader "$TMP" && chmod 600 "$TMP" && mv "$TMP" "$ENV"
systemctl restart wallex-agent
sleep 10
systemctl --no-pager --lines=15 status wallex-agent || true
echo "armed: the engine restarted in LIVE mode — check the Bale start-up summary"
