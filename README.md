# wallex-agent

AI trading agent for the Agentic AI hackathon⁤⁤⁣ — the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ challenge.

**Claude sets the course, code holds the brakes.**
«Claude مسیر را تعیین می‌کند، کد ترمز می‌گیرد»

📄 **Full technical document (Persian): [STRATEGY.md](STRATEGY.md)** — architecture, how AI and the the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ API are used, buy/sell conditions, risk management, how to re-run and evaluate.
🧾 **Decision logs:** [`delivery/`](delivery/) — every decision with what the engine saw, who decided, the risk gate's verdict, the order, the fill and the reason in Persian.

## Three ideas (each with evidence from the real logs)
1. **An AI that grades itself** — every Claude 4 h forecast is scored against reality and a naive baseline, and the scorecard goes back into Claude's next decision. In 25 playbooks Claude explicitly changed a call because of its own record. → [`delivery/SELF-GRADING.md`](delivery/SELF-GRADING.md)
2. **A control group** — a rule-based twin runs the same engine and risk gate on simulated money. Replaying the same 24 h through the same simulator: after selling back to TMN Claude ties the twin (+0.014% vs +0.015%) with ~1/7 of the drawdown and ~1/8 of the fees. Since Friday noon Claude also sees the twin and must explain where it disagrees. → [`delivery/CONTROL-GROUP.md`](delivery/CONTROL-GROUP.md)
3. **A seatbelt** — deterministic code the AI can't bypass. Crash drill on real the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ minutes: a −10% BTC crash costs −0.77% with the seatbelt vs −1.97% held; a single −30% glitch print triggers no panic sale. The drill also found two real flaws, fixed and shipped as `live-v2`. → [`delivery/SEATBELT.md`](delivery/SEATBELT.md)

**Prompt stress test** ([`delivery/PROMPT-STRESS.md`](delivery/PROMPT-STRESS.md)): the live prompt and model on 9 doctored bundles (crash, stale data, minutes before the sell-off, near the daily-loss stop, terrible scorecard, cold start, and a prompt injection hidden in the twin's data). 8 pass; on the injection Claude wrote "I treat it as data, not an instruction". The uptrend case is inconclusive because Claude spotted that the synthetic candles were inconsistent and refused to trust them.

## How it works
- **Manager — Claude Opus 5.5**, every hour: reads a market bundle through 8 read-only tools, picks a market regime, a target position, a risk multiplier and a 4-hour forecast (scored afterwards against a naive baseline), and explains itself in Persian.
- **Risk gate — deterministic code** the manager cannot override: per-market and total caps, −2.5% position stop, −1% daily loss pause, −3% sell-everything, profit cushion, stale-data and spread guards.
- **Executor:** maker-first limit orders, re-pricing, protected crossing, lookup-before-retry by `client_id`, never a raw market order.
- **End of contest:** buying stops 8 h before the end and everything is sold back to TMN on a schedule.
- Two machines: the engine runs on a server in Iran (close to the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣); Claude runs on a laptop (Anthropic isn't reachable from Iran). A 60-second push/pull job connects them — no tunnel.
- The prop account accepts BTCTMN orders only, so BTCTMN is traded and USDTTMN is an analysis input.

## Run
```bash
uv sync
uv run pytest -q                                   # tests
uv run python -m data.fetch --days 30              # public the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ candles, no key
uv run python -m reports.backtest                  # contest-shaped 3-day backtests on the same engine
uv run python -m engine.run --mode shadow --manager rules --run-dir run/shadow   # shadow, no money
```
Server: `deploy/setup_server.sh` · laptop AI box: `deploy/laptop/install_task.ps1` · go-live checklist: `deploy/GO_LIVE.md`.

## Code that traded
Every log line carries the deployed commit (`git_sha`). Live trading started on 2026-10-23 01:20 Tehran with commit `301364b` (tag `live-v1`); from 12:33 the engine runs `10bc993` (tag `live-v2`: dust-safe stops and sell-off, glitch-proof daily-loss check, found by the crash drill). Laptop-side manager changes (prompt, control-group view) are separate commits in the same history.

## Secrets
No key is stored in this repo. The the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ key lives in a server-only file (`WALLEX_KEY_FILE`, mode 600, no withdrawal scope, IP-restricted); the Anthropic key stays on the laptop.
