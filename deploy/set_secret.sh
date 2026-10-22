#!/usr/bin/env bash
# Save one secret on the server without it ever passing through chat, logs or `ps`.
#   sudo bash /opt/wallex-agent/deploy/set_secret.sh BALE_TOKEN
#   sudo bash /opt/wallex-agent/deploy/set_secret.sh wallex.key
# Typing is hidden. It cannot set WALLEX_LIVE_ARMED — arming is a separate, deliberate step.
set -euo pipefail
NAME=${1:-}
ENV=/etc/wallex-agent/env
case "$NAME" in
  BALE_TOKEN|BALE_CHAT_ID|WALLEX_END_AT|wallex.key) ;;
  *) echo "usage: set_secret.sh BALE_TOKEN|BALE_CHAT_ID|WALLEX_END_AT|wallex.key"; exit 1 ;;
esac
read -rsp "Paste the value for $NAME (hidden), then press Enter: " V; echo
V=$(printf '%s' "$V" | tr -d '\r\n')
[ -n "$V" ] || { echo "empty — nothing saved"; exit 1; }
umask 077
if [ "$NAME" = wallex.key ]; then
  printf '%s' "$V" > /etc/wallex-agent/wallex.key
  chown trader:trader /etc/wallex-agent/wallex.key && chmod 600 /etc/wallex-agent/wallex.key
else
  TMP=$(mktemp /etc/wallex-agent/.env.XXXXXX)
  grep -v "^$NAME=" "$ENV" > "$TMP" || true
  printf '%s=%s\n' "$NAME" "$V" >> "$TMP"
  chown trader:trader "$TMP" && chmod 600 "$TMP" && mv "$TMP" "$ENV"
fi
echo "saved $NAME (${#V} characters)"
