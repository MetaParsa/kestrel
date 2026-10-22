# wallex-agent

AI trading agent for the StarCoach Agentic AI hackathon — Wallex challenge.

**Claude sets the course, code holds the brakes.**
«Claude مسیر را تعیین می‌کند، کد ترمز می‌گیرد»

📄 **Full technical document (Persian): [STRATEGY.md](STRATEGY.md)** — architecture, how AI and the Wallex API are used, buy/sell conditions, risk management, how to re-run and evaluate.
🧾 **Decision logs:** [`delivery/`](delivery/) — every decision with what the engine saw, who decided, the risk gate's verdict, the order, the fill and the reason in Persian.

## How it works
- **Manager — Claude Opus 5.5**, every hour: reads a market bundle through 8 read-only tools, picks a market regime, a target position, a risk multiplier and a 4-hour forecast (scored afterwards against a naive baseline), and explains itself in Persian.
- **Risk gate — deterministic code** the manager cannot override: per-market and total caps, −2.5% position stop, −1% daily loss pause, −3% sell-everything, profit cushion, stale-data and spread guards.
- **Executor:** maker-first limit orders, re-pricing, protected crossing, lookup-before-retry by `client_id`, never a raw market order.
- **End of contest:** buying stops 8 h before the end and everything is sold back to TMN on a schedule.
- Two machines: the engine runs on a server in Iran (close to Wallex); Claude runs on a laptop (Anthropic isn't reachable from Iran). A 60-second push/pull job connects them — no tunnel.
- The prop account accepts BTCTMN orders only, so BTCTMN is traded and USDTTMN is an analysis input.

## Run
```bash
uv sync
uv run pytest -q                                   # tests
uv run python -m data.fetch --days 30              # public Wallex candles, no key
uv run python -m reports.backtest                  # contest-shaped 3-day backtests on the same engine
uv run python -m engine.run --mode shadow --manager rules --run-dir run/shadow   # shadow, no money
```
Server: `deploy/setup_server.sh` · laptop AI box: `deploy/laptop/install_task.ps1` · go-live checklist: `deploy/GO_LIVE.md`.

## Code that traded
Every log line carries the deployed commit (`git_sha`). Live trading started on 2026-10-23 01:20 Tehran with commit `301364b`.

## Secrets
No key is stored in this repo. The Wallex key lives in a server-only file (`WALLEX_KEY_FILE`, mode 600, no withdrawal scope, IP-restricted); the Anthropic key stays on the laptop.
