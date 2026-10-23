# Go-live checklist (only after the user says "go live" in chat)

Nothing below happens automatically. Until these steps run, the server cannot place an order:
`WALLEX_MODE=shadow` in `/etc/wallex-agent/env`, and live mode (and the canary) refuse without
`WALLEX_LIVE_ARMED=yes-real-money`. Both settings live in the env file, so a redeploy never changes them.

Helper — runs a command as `trader` with the env file, without putting secrets in `ps`:
```bash
asrun() { sudo systemd-run --quiet --wait --pipe --collect -p User=trader -p WorkingDirectory=/opt/wallex-agent \
  -p EnvironmentFile=/etc/wallex-agent/env ${ARM:+-p Environment=WALLEX_LIVE_ARMED=yes-real-money} \
  /opt/wallex-agent/.venv/bin/python "$@"; }
# ARM=1 asrun …   arms only that one command
```

## Before
1. `WALLEX_END_AT` in the env file is the confirmed (or agreed) end, with `+03:30`.
2. The key is on the server (`/etc/wallex-agent/wallex.key`, trader 600), no withdrawal scope, server IP whitelisted.
3. Read-only check: `asrun -m engine.reconcile` → 5,000,000 TMN, no open orders. (A fresh live start refuses otherwise.)
4. Bale works: `asrun -m alerts.bale --test`.

## Canary — one tiny real order that can't fill (armed for this one command only)
```bash
ARM=1 asrun -m engine.canary
sudo cat /opt/wallex-agent/run/canary.json
```
Must print `"ok": true`. While it rests, check read-only which account shows ~55,000 TMN locked (main vs prop sub-account) — stop if it isn't the prop account. Copy `read_back_raw` into `tests/fixtures/order_partial.json`.

## Switch the main engine to live
```bash
sudo bash /opt/wallex-agent/deploy/arm_live.sh      # type yes-real-money
```
At start, live mode moves the shadow `state.json` aside, cancels any of our own `d-…` orders left on the exchange (refuses to trade if one won't cancel), starts from the real balances, reconciles, and sends a Bale summary. From then on a shadow engine refuses to start on that run dir (it holds live state).

## After
- First real fill: check `orders.jsonl` / `fills.jsonl` and `fee_estimated` events. Reconcile runs every 30 min, at T−30 min and at the end; at T−1 h and T−30 min the books are re-synced to the real balances so the final sell-off sells what the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ really holds.

## Stopping
- **Pause without selling** (positions stay; stops pause too — only for a short fix): `sudo systemctl stop wallex-agent`.
- **Exit everything to TMN early:** set `WALLEX_END_AT` to about now + 1 h 05 min (`sudo bash deploy/set_secret.sh WALLEX_END_AT`) and `sudo systemctl restart wallex-agent` — the sell-off schedule sells everything and the engine stays read-only.
- Only after everything is TMN: set `WALLEX_MODE=shadow`, clear `WALLEX_LIVE_ARMED`, move `run/main/state.json` aside, restart.
