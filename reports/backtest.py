"""Backtest: replay real Wallex 1-minute history through the real engine.

Everything that runs live runs here too — regime targets, the risk gate, the executor (passive
limits, re-pricing, crossing), exits, the activity floor and the end-of-contest sell-off to TMN —
on BacktestBroker (fills only when a real bar traded through our price, fees at Wallex's rates).
The manager is the free rule-based one (`manager/mock.py`): Claude can't be replayed honestly on
the past (it would read the answer in its own training data) and would cost ~$0.08 per hour.

Each run is a contest-shaped window: start in TMN, trade for `--days`, end fully in TMN.

    uv run python -m reports.backtest                # every 3-day window in the stored 30 days
    uv run python -m reports.backtest --days 3 --every-h 24 --out reports/out/backtest
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import time
from datetime import datetime
from pathlib import Path

import numpy as np

import config
from alerts.bale import Alerter
from data import bars as bars_mod
from data import fetch
from data.poll import MarketSnapshot
from engine.loop import Engine, EngineCfg
from engine.modes import BacktestBroker

HISTORY_MIN = 3 * 1440          # what the bundle and regime stats look back over


class Replay:
    """The market as it was at `now`: quotes from the minute's close ± typical half-spread."""

    def __init__(self, markets: list[str]):
        self.markets = markets
        self.g = {m: bars_mod.to_minute_grid(fetch.load(m)) for m in markets}
        self.ts = {m: g.index.as_unit("s").asi8 for m, g in self.g.items()}
        self.last_trade = {}
        for m, g in self.g.items():                      # time of the last minute that really traded
            t = np.where(g["traded"].to_numpy(), self.ts[m], np.nan)
            self.last_trade[m] = np.fmax.accumulate(np.nan_to_num(t, nan=-np.inf))
        self.now = 0.0

    def span(self) -> tuple[float, float]:
        return max(float(t[0]) for t in self.ts.values()), min(float(t[-1]) for t in self.ts.values())

    def idx(self, m: str) -> int:
        return int(np.searchsorted(self.ts[m], self.now, side="right")) - 1

    def bars(self, m: str):
        i = self.idx(m)
        return self.g[m].iloc[max(0, i - HISTORY_MIN):i + 1] if i >= 0 else None

    def row(self, m: str):
        return self.g[m].iloc[self.idx(m)]

    def snapshots(self) -> dict[str, MarketSnapshot]:
        out = {}
        for m in self.markets:
            i = self.idx(m)
            if i < 0:
                continue
            c = float(self.g[m]["c"].iloc[i])
            hs = config.SIM_HALF_SPREAD.get(m, 0.0005)
            lt = float(self.last_trade[m][i])
            out[m] = MarketSnapshot(m, c * (1 - hs), c * (1 + hs), self.now, 0.0, c,
                                    lt if math.isfinite(lt) else None)
        return out


class Quiet(Alerter):
    def __init__(self):
        super().__init__(None, None)
        self.texts: list[str] = []

    def send(self, text, *, key=None, force=False):
        self.texts.append(text)
        return False


def run_window(rp: Replay, start: float, days: float, run_dir: Path) -> dict:
    shutil.rmtree(run_dir, ignore_errors=True)
    end = start + days * 86400
    broker = BacktestBroker()
    rp.now = start
    cfg = EngineCfg(run_dir=run_dir, mode="backtest", manager="rules", sha="backtest",
                    end_at=datetime.fromtimestamp(end, tz=config.TEHRAN))
    eng = Engine(cfg, broker=broker, market=rp.snapshots, bars=rp.bars, clock=lambda: rp.now, alerter=Quiet())
    peak, max_dd, curve = cfg.start_cash, 0.0, []
    t = start
    while t <= end + 60:
        rp.now = t
        for m in config.MARKETS:
            r = rp.row(m)
            broker.feed_bar(m, t, float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
        eng.tick()
        if eng.bids:
            eq = eng.pf.equity(eng.bids)
            peak = max(peak, eq)
            max_dd = max(max_dd, 1 - eq / peak)
            if int(t) % 3600 == 0:
                curve.append((int(t), round(eq)))
        t += 60
    fills = eng.logs.tail("fills", 100_000)
    decisions = eng.logs.tail("decisions", 100_000)
    final = eng.pf.equity(eng.bids)
    left = {m: eng.pf.qty(m) for m in config.MARKETS                  # dust below the exchange minimum can't be sold
            if eng.pf.qty(m) * eng.bids.get(m, 0.0) >= config.LIMITS.exchange_min_notional_tmn}
    first = {m: float(rp.g[m]["c"].iloc[int(np.searchsorted(rp.ts[m], start)) ]) for m in config.MARKETS}
    last = {m: float(rp.row(m).c) for m in config.MARKETS}
    return {
        "start": datetime.fromtimestamp(start, tz=config.TEHRAN).isoformat(timespec="minutes"),
        "end": datetime.fromtimestamp(end, tz=config.TEHRAN).isoformat(timespec="minutes"),
        "return_pct": round((final / cfg.start_cash - 1) * 100, 3),
        "max_drawdown_pct": round(max_dd * 100, 3),
        "fills": len(fills), "decisions": len(decisions),
        "fees_tmn": round(sum(f["fee"] * (f["price"] if f.get("fee_asset") not in ("TMN", None) else 1) for f in fills)),
        "ended_in_tmn": not left, "left": left,
        "regimes": sorted({d.get("regime") for d in decisions if d.get("regime")}),
        "hold_btc_pct": round((last["BTCTMN"] / first["BTCTMN"] - 1) * 100, 3),
        "hold_usdt_pct": round((last["USDTTMN"] / first["USDTTMN"] - 1) * 100, 3),
        "equity_hourly": curve,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=3.0)
    ap.add_argument("--every-h", type=float, default=24.0, help="hours between window starts")
    ap.add_argument("--limit", type=int, default=0, help="only the first N windows")
    ap.add_argument("--warmup-h", type=float, default=72.0, help="history before the first window")
    ap.add_argument("--out", type=Path, default=Path("reports/out/backtest"))
    ap.add_argument("--shard", default="0/1", help="k/n: run every n-th window starting at k (parallel runs)")
    ap.add_argument("--merge", action="store_true", help="only merge shard results into summary.json")
    a = ap.parse_args(argv)
    k, n = (int(x) for x in a.shard.split("/"))
    rp = Replay(list(config.MARKETS))
    lo, hi = rp.span()
    start = (lo + a.warmup_h * 3600) // 3600 * 3600
    starts = []
    while start + a.days * 86400 <= hi:
        starts.append(start)
        start += a.every_h * 3600
    if a.limit:
        starts = starts[:a.limit]
    a.out.mkdir(parents=True, exist_ok=True)
    results = []
    if a.merge:
        for f in sorted(a.out.glob("shard-*.jsonl")):
            results += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        results.sort(key=lambda r: r["start"])
    for i, s in enumerate(starts):
        if a.merge or i % n != k:
            continue
        t0 = time.time()
        r = run_window(rp, s, a.days, a.out / "runs" / f"w{i:02d}")
        r["seconds"] = round(time.time() - t0, 1)
        results.append(r)
        with (a.out / f"shard-{k}of{n}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(json.dumps({kk: v for kk, v in r.items() if kk != "equity_hourly"}, ensure_ascii=False), flush=True)
    if n > 1 and not a.merge:
        return
    rets = [r["return_pct"] for r in results]
    summary = {
        "tradable": list(config.TRADABLE), "manager": "rules (manager/mock.py)", "days": a.days,
        "windows": len(results),
        "positive_share": round(sum(x > 0 for x in rets) / len(rets), 3) if rets else None,
        "mean_return_pct": round(float(np.mean(rets)), 3) if rets else None,
        "median_return_pct": round(float(np.median(rets)), 3) if rets else None,
        "worst_return_pct": min(rets) if rets else None, "best_return_pct": max(rets) if rets else None,
        "worst_drawdown_pct": max(r["max_drawdown_pct"] for r in results) if results else None,
        "all_ended_in_tmn": all(r["ended_in_tmn"] for r in results),
        "mean_fills": round(float(np.mean([r["fills"] for r in results])), 1) if results else None,
        "results": results,
    }
    (a.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
