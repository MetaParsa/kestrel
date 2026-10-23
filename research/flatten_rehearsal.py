"""Dress rehearsal of the end-of-contest sell-off with the live code and the live position.

Replays the last ~9.5 hours of real Wallex minutes through the engine (BacktestBroker), starting from
the real holding (BTCTMN 9.02285e-06 at the real cost), with END_AT set so that every phase happens:
no_new (passive sells) → aggressive → market → verify → ended.

    uv run python -m research.flatten_rehearsal
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import config
from engine.loop import Engine, EngineCfg
from engine.modes import BacktestBroker
from engine.portfolio import Portfolio
from research.prompt_stress import TWIN  # noqa: F401  (keeps the research package importable as one unit)
from reports.backtest import Quiet, Replay
from reports.crash_drill import playbook

QTY, COST = 9.02285e-06, 200_193.35


def main():
    rp = Replay(list(config.MARKETS))
    lo, hi = rp.span()
    start = (hi - 9.5 * 3600) // 60 * 60
    end_at = start + 8.6 * 3600                       # no_new starts ~0.6 h in
    run_dir = Path("run/rehearsal")
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    pf = Portfolio(config.START_EQUITY_TMN - COST, start)
    p = pf.pos("BTCTMN", "core")
    p.qty, p.cost_tmn, p.opened_at = QTY, COST, start
    broker = BacktestBroker()
    cfg = EngineCfg(run_dir=run_dir, mode="backtest", manager="remote", sha="rehearsal",
                    end_at=datetime.fromtimestamp(end_at, tz=config.TEHRAN))
    rp.now = start
    eng = Engine(cfg, broker=broker, market=rp.snapshots, bars=rp.bars, clock=lambda: rp.now, alerter=Quiet(), portfolio=pf)
    pb = playbook(start).model_copy(update={"regime": "range", "core_targets": {"USDTTMN": 0.0, "BTCTMN": 0.08},
                                           "risk_multiplier": 0.5})
    eng._activate(pb, start)
    phases, t = [], start
    while t <= min(hi, end_at + 600):
        rp.now = t
        for m in config.MARKETS:
            r = rp.row(m)
            broker.feed_bar(m, t, float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
        eng.tick()
        if not phases or phases[-1][1] != eng.last_phase:
            phases.append((datetime.fromtimestamp(t, tz=config.TEHRAN).strftime("%H:%M"), eng.last_phase))
        t += 60
    dec = eng.logs.tail("decisions", 1000)
    fills = eng.logs.tail("fills", 1000)
    tehran = lambda ts: datetime.fromtimestamp(ts, tz=config.TEHRAN).strftime("%H:%M")
    out = {
        "replayed": f"{tehran(start)} → {tehran(t)} (END_AT {tehran(end_at)})",
        "phases": phases,
        "decisions": [(tehran(d["ts"]), d["side"], d["kind"], d["urgency"], d["gate"]["verdict"]) for d in dec],
        "fills": [(tehran(f["ts"]), f["side"], f["qty"], round(f["price"])) for f in fills],
        "btc_left": eng.pf.qty("BTCTMN"),
        "btc_left_tmn": round(eng.pf.qty("BTCTMN") * eng.bids["BTCTMN"]),
        "final_equity": round(eng.pf.equity(eng.bids)),
    }
    Path("reports/out").mkdir(parents=True, exist_ok=True)
    Path("reports/out/flatten-rehearsal.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
