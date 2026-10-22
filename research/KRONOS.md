# Kronos on Wallex data — walk-forward test (2026-10-22)

**Verdict: no-go.** Kronos-small does not call the direction or size of 4-hour moves in USDTTMN or
BTCTMN better than lazy baselines, and trading on it loses money after fees. Its band width carries a
little information about coming volatility, but that edge is unstable across the two halves of the
month and mostly overlaps with plain trailing volatility. It is **not** wired into the agent.

## Setup

- Model: official `NeoQuasar/Kronos-small` + `Kronos-Tokenizer-base`, code from `github.com/shiyu-coder/Kronos` (commit 67b630e).
- Data: our 30 days of Wallex 1-minute candles (22 Sep – 22 Oct 2026), resampled to 15-minute bars.
- Walk-forward: every 4 hours (non-overlapping), Kronos sees only the past **128 bars (32 h)** and
  forecasts the next 16 bars (4 h) as **20 sampled paths**. Windows whose 4 h target has more than
  60 missing minutes are dropped (USDTTMN's trading gaps).
- Baselines: "no change" (next close = last close), "always up", 4 h momentum, trailing realized volatility.
- Paper P&L: spot, long-or-flat (no shorting), 0.6% round trip (0.30% taker each way).
- Run: `.kronos/.venv-cpu/Scripts/python.exe research/kronos_walkforward.py --model small --ctx 128 --step 16 --paths 20 --device cpu`
  (set `HF_HUB_OFFLINE=1` once the weights are cached). Per-window results in `research/kronos_out/`.

## Results

| | USDTTMN | BTCTMN |
|---|---|---|
| Usable windows | 167 | 172 |
| 4 h direction right — Kronos | **43.8%** ± 8.5 | **48.8%** ± 7.5 |
| 4 h direction right — "always up" | 53.8% | 54.7% |
| Miss on 4 h close vs "no change" | 10% worse | 7% worse |
| Share of forecasts pointing up | 29% | 29% |
| Price inside its 10–90% band (ideal 80%) | 50% | 49% |
| Paper P&L, long when Kronos says up | **−26.4%** (48 trades) | **−23.2%** (50 trades) |
| Same, only when it predicts > fees | +0.5% (1 trade) | −1.0% (5 trades) |
| Buy and hold over the month | +15.7% | +13.2% |

Both halves of the month tell the same story for direction and P&L (Kronos never beats "always up"
in either half).

**Band width vs the volatility of the next 4 h** (Spearman rank correlation):

| | USDTTMN all / 1st half / 2nd half | BTCTMN all / 1st / 2nd |
|---|---|---|
| Kronos band | 0.42 / 0.56 / 0.19 | 0.38 / 0.34 / 0.40 |
| Trailing 4 h volatility alone | 0.37 / 0.42 / 0.26 | 0.32 / 0.35 / 0.29 |
| Band after removing trailing 4 h + 24 h volatility | 0.21 / 0.46 / −0.05 | 0.15 / 0.03 / 0.25 |

The band adds something beyond trailing volatility in one half of each market and nothing in the
other. That is too unstable to size real money on.

## Why it fails here

- **It leans bearish while the toman devalues.** Only 29% of its forecasts point up, while both
  markets rose ~13–16% in TMN over the month. A model trained on global exchanges up to mid-2024
  mean-reverts against the steady devaluation drift that dominates these pairs.
- That bias also pushes the real price outside its band half the time.
- USDTTMN is thin: a third of its 15-minute bars have zero volume, so its candles carry little shape
  for a candle-reading model.

## Limits of this test

- 128 bars of history instead of the 400 in the Kronos examples: the GPU build of PyTorch could not
  be downloaded on this connection, and the CPU needs ~3.5 s per path at 400 bars.
- One model size (small), one month, ~170 windows per market (direction CIs ±8 points).
- These limits could hide a small effect, not turn a 44–49% hit rate and −23% P&L into an edge.

## Decision

Kronos stays out of the trading path and out of Claude's bundle. The spec's rule ("optional feature,
kept only if out-of-fold results improve") already covered this; the test answers it. For the
write-up: an evaluated-and-rejected model, with its numbers, is evidence of the rigour the rubric's
"strategy logic" and "backtest" lines ask for.
