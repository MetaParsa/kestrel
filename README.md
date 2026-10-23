# kestrel⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣

AI trading agent for the Agentic AI hackathon⁤⁤⁣'s trading challenge. It traded a real 5,000,000 TMN prop account on the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣.

**Claude sets the course, code holds the brakes.**
«Claude مسیر را تعیین می‌کند، کد ترمز می‌گیرد»

## Judges: start here → [JUDGES.md](JUDGES.md)
```bash
git clone https://github.com/MetaParsa/kestrel.git && cd kestrel
uv sync
uv run python -m judge.demo      # ~8–10 min, no API key, offline: re-runs the evidence below and compares it with the committed numbers
uv run pytest -q                 # 250+ tests, ~25 s
```
📄 **Full technical document (Persian): [STRATEGY.md](STRATEGY.md)** — architecture, AI and the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ API use, buy/sell conditions, risk management, re-running and evaluation.
🧾 **Decision logs:** [`delivery/DECISIONS-live.md`](delivery/DECISIONS-live.md) — every live decision with what the engine saw, who decided, the risk gate's verdict, the order, the fill and the reason in Persian (shadow: [`DECISIONS-main.md`](delivery/DECISIONS-main.md), rule twin: [`DECISIONS-baseline.md`](delivery/DECISIONS-baseline.md)).

## Architecture
```mermaid
flowchart LR
  subgraph Laptop["Laptop — ai_box/ (Anthropic is reachable)"]
    M["Claude Opus 5.5 · manager/<br/>prompt: manager/prompts/manager.md<br/>8 read-only tools · strict JSON schema"]
  end
  subgraph Server["Server in Iran — engine/ (close to the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣)"]
    T["10-second tick<br/>engine/loop.py · Engine.tick()"]
    G{"deterministic risk gate<br/>engine/gate.py · check()"}
    X["executor<br/>engine/executor.py · engine/live.py"]
    TW["rule-based twin<br/>manager/mock.py · simulated money"]
    L[("decision logs<br/>→ delivery/")]
  end
  I["bridge/inbox.py<br/>re-validated on the server"]
  W[("the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ API · wallex/")]
  T -->|"bundle.json every 60 s (scp, no tunnel)"| M
  M -->|"playbook.json hourly"| I
  I --> T
  T -->|"proposal"| G
  G -->|"allow / resize / block"| X
  X --> W
  TW -->|"its plan, shown to Claude"| M
  T --> L
  H["human oversight · oversight/<br/>brakes only · built, not deployed"] -.->|"pause / lower risk / sell-off"| T
```

## Three ideas, each with evidence from the real logs
| idea | result | evidence |
|---|---|---|
| **An AI that grades itself** — each Claude 4 h forecast is scored afterwards against reality and a naive baseline; the scorecard goes back into Claude's next decision | <!-- SG:en -->72 forecasts: Claude 46 %, naive "same as last 4 h" 33 %, "always flat" 42 %. 46 of 53 playbooks cite Claude's own scorecard (a keyword match — read the lines yourself in SELF-GRADING.md); forecasts come two per playbook, so they aren't independent<!-- /SG --> | [`SELF-GRADING.md`](delivery/SELF-GRADING.md) · [`self-grading.json`](reports/out/self-grading.json) |
| **A control group** — a rule-based twin runs the same engine and risk gate; the same 24 h of real minutes replayed with Claude's recorded playbooks vs the twin | After selling back to TMN both +0.014 %, with Claude's drawdown 0.026 % vs 0.177 % and fees 601 vs 4,879 TMN — mostly because Claude chose a 4 % position where the rule took 15 %: the same result at a quarter of the risk, not a better trade. Since Friday noon Claude also sees the twin and must explain where it disagrees | [`CONTROL-GROUP.md`](delivery/CONTROL-GROUP.md) · [`ab-replay-BTCTMN.json`](reports/out/ab-replay-BTCTMN.json) |
| **A seatbelt** — deterministic code the AI can't bypass | Crash drill on real market minutes: a −10 % BTC crash costs −0.77 % with the seatbelt vs −1.97 % held; a single −30 % glitch print triggers no sale. The drill found two real flaws, fixed and shipped as `live-v2` | [`SEATBELT.md`](delivery/SEATBELT.md) · [`crash-drill.json`](reports/out/crash-drill.json) |

**Prompt stress test** ([`PROMPT-STRESS.md`](delivery/PROMPT-STRESS.md)): the live prompt and model on 9 doctored bundles (crash, stale data, minutes before the sell-off, near the daily-loss stop, terrible scorecard, cold start, and a prompt injection hidden in the twin's data). 8 pass; on the injection Claude wrote "I treat it as data, not an instruction". The uptrend case is inconclusive: Claude spotted that the synthetic candles were inconsistent and refused to trust them.

**Backtest** ([`backtest-summary.json`](reports/out/backtest-summary.json)): the same engine on 25 contest-shaped 3-day windows of real 1-minute data with the rule-based manager — mean −0.14 % (+0.10 % before fees), worst drawdown 0.60 %, every window ended fully in TMN.

**Live** ([`DECISIONS-live.md`](delivery/DECISIONS-live.md)): from Fri 01:20 Tehran, 22 Claude playbooks ($1.55), 2 fills; after the scheduled sell-off the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ showed 5,000,410 TMN and nothing else (the engine's own book keeps ≈63 TMN of BTC dust below the exchange's⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ precision; max drawdown ≈ 0.03 %).

## How it works
- **Manager — Claude Opus 5.5**, every hour (every 15 min on the last evening): reads a market bundle through 8 read-only tools, picks a market regime, a target position, a risk multiplier and a 4-hour forecast, and explains itself in Persian. Its output is clamped to the regime's bounds and re-validated on the server; a $15 spend cap falls back to the rule-based manager.
- **Risk gate — deterministic code** the manager cannot override (`engine/gate.py`, numbers in `config.py`): per-market and total caps, −2.5 % position stop, −1 % daily loss pause, −2 % micro mode, −3 % sell-everything, profit cushion, stale-data and spread guards.
- **Executor:** maker-first limit orders, re-pricing, protected crossing, lookup-before-retry by `client_id`, never a raw market order. Real orders need two locks (`--mode live` and `WALLEX_LIVE_ARMED=yes-real-money`).
- **End of contest:** buying stops before the end and everything is sold back to TMN on a schedule (8 h before by default; 2.5 h on the last evening, `live-v3`: buying until 21:20, all TMN by 23:20).
- Two machines: the engine runs on a server in Iran (close to the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣); Claude runs on a laptop (Anthropic isn't reachable from Iran). A 60-second push/pull job connects them — no tunnel.
- The prop account accepts BTCTMN orders only, so BTCTMN is traded and USDTTMN is an analysis input.

## Human oversight (brakes only) — [`oversight/README.md`](oversight/README.md)
The rules forbid manual trades, so a person can only make the engine more careful: Bale commands `/pause`, `/risk 0.5` (lower only), `/flatten` (starts the normal sell-off, needs a second confirmation), `/resume`, `/status` and a daily `/review` note — each logged with who / when / why. It's a subclass (`python -m oversight.run`) so the code that traded stays untouched. **Built and tested, not deployed.**

## Run it yourself
```bash
uv sync
uv run pytest -q                                   # tests
uv run python -m judge.demo                        # evidence from committed data, offline (see JUDGES.md)
uv run python -m data.fetch --days 30              # fresh public the exchange⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ candles into data/store (no key)
uv run python -m reports.backtest --limit 2       # contest-shaped 3-day windows on the same engine (~8 min each; all 25: judge.demo --windows 25)
uv run python -m engine.run --mode shadow --manager rules --run-dir run/shadow   # live data, simulated money
```
Server: `deploy/setup_server.sh` · laptop AI box: `deploy/laptop/install_task.ps1` · go-live checklist: `deploy/GO_LIVE.md`.

## Code that traded
Every log line carries the deployed commit (`git_sha`), and every live deployment has a tag (`git tag -n1 -l "live-*"`):
- `live-v1` (`301364b`) — from 2026-10-23 01:20 Tehran;
- `live-v2` (`10bc993`) — from ~12:30: dust-safe stops and sell-off, glitch-proof daily-loss check (found by the crash drill);
- `live-v3` (`e123198`) — Friday evening: shorter sell-off window (buying open until 21:20, all TMN by 23:20);
- `live-v4` (`0e2d29b`) — from ~17:45: the manager is asked every 15 min on the last evening (`a808dc7`); this snapshot also carries `judge/` and `oversight/`, which the engine never imports or starts.

Laptop-side manager changes during the contest are separate commits: prompt (`75c2700`, `612c972`, `13730f6`), the twin's view for Claude (`79d2f13`, prompt + `ai_box/`), last-evening cadence and 1-hour prompt cache (`a808dc7`). None of them touched the risk gate, and Claude kept every decision. List any later change to trading code with `git log --oneline live-v4..HEAD -- engine wallex bridge ai_box manager config.py deploy`.

## Secrets
No key is stored in this repo. the exchange's⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁤⁣ API key lives in a server-only file (`WALLEX_KEY_FILE`, mode 600, no withdrawal scope, IP-restricted); the Anthropic key stays on the laptop.
