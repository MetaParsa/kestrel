# Go-live checklist (only after the user says "go live" in chat)

Nothing below happens automatically. Until these steps run, the server cannot place an order:
the main service runs `--mode shadow`, and live mode (and the canary) refuse without
`WALLEX_LIVE_ARMED=yes-real-money`.

Helper used below — runs a command as `trader` with the env file, without putting secrets in `ps`:
```bash
asrun() { sudo systemd-run --quiet --wait --pipe --collect -p User=trader -p WorkingDirectory=/opt/wallex-agent \
  -p EnvironmentFile=/etc/wallex-agent/env /opt/wallex-agent/.venv/bin/python "$@"; }
```

## Before
1. Wallex support has confirmed which account the API key trades (main vs the prop sub-account) and that USDTTMN/BTCTMN are allowed.
2. The organizers have confirmed the end time → `WALLEX_END_AT` in `/etc/wallex-agent/env` (with `+03:30`).
3. The key is on the server: `/etc/wallex-agent/wallex.key` (owner trader, chmod 600), IP-whitelisted for the server, **no withdrawal scope**.
4. Read-only check: `asrun -m engine.reconcile` lists 5,000,000 TMN and no open orders. (Live start refuses if the fresh balance isn't ≈ 5,000,000.)
5. Bale alerts work: `asrun -m alerts.bale --test`.

## Canary (one tiny real order that can't fill — confirms Wallex's order format)
```bash
echo 'WALLEX_LIVE_ARMED=yes-real-money' | sudo tee -a /etc/wallex-agent/env >/dev/null
asrun -m engine.canary            # LIMIT BUY of ~55,000 TMN at bid −10%, read back, cancel, read back
sudo cat /opt/wallex-agent/run/canary.json
```
Must print `"ok": true`. Copy `read_back_raw` into `tests/fixtures/order_partial.json` and re-run the tests. If not ok: stop, disarm (remove the line), fix the field mapping.

## Switch the main engine to live
```bash
sudo sed -i 's/--mode shadow --manager remote/--mode live --manager remote/' /etc/systemd/system/wallex-agent.service
systemctl cat wallex-agent | grep -q -- '--mode live --manager remote' && echo OK-live-unit || echo "UNIT NOT CHANGED — stop"
sudo systemctl daemon-reload && sudo systemctl restart wallex-agent
```
At start, live mode: moves the shadow `state.json` aside (simulated positions are never treated as real), cancels any of our own `d-…` orders left on the exchange (and refuses to trade if one won't cancel), starts from the real balances, reconciles, and sends a Bale summary.

## After
- First real fill: check `orders.jsonl` / `fills.jsonl` and `fee_estimated` events (Wallex didn't report the fee). Reconcile runs every 30 min, at T−30 min and at the end; any mismatch is alerted.

## Stopping
- **Pause without selling** (positions stay, stops stop too — only for a short fix): `sudo systemctl stop wallex-agent`.
- **Exit everything to TMN early:** set `WALLEX_END_AT` to about now + 1 h 05 min in the env file and `sudo systemctl restart wallex-agent` — the flatten schedule sells everything (passive → aggressive → market) and the engine then stays read-only.
- Only after positions are all TMN: remove `WALLEX_LIVE_ARMED` from the env file and switch the unit back to `--mode shadow` (otherwise live refuses to start and the service restart-loops).
