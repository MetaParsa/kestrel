"""The seatbelt, tested: the live engine on real Wallex minutes with injected shocks.

1) crash  — a 20% BTCTMN position, then BTC falls 10% over 15 minutes and stays down.
            With the seatbelt vs the same position held through it.
2) glitch — one absurd print (−30% for a single minute). The engine must NOT sell.

    uv run python -m reports.crash_drill
Writes reports/out/crash-drill.json and delivery/SEATBELT.md.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import config
from engine.loop import Engine, EngineCfg
from engine.modes import BacktestBroker
from manager.schema import Playbook
from reports.backtest import Quiet, Replay

START = datetime(2026, 10, 15, 10, 0, tzinfo=config.TEHRAN).timestamp()
SHOCK_AT = START + 2 * 3600
HOURS = 6
KEY_EVENTS = ("stop", "flatten", "hard", "halt", "gate_mode", "daily", "degrade", "micro", "cooldown")


def playbook(now: float) -> Playbook:
    return Playbook.model_validate({
        "playbook_id": "pb-drill", "created_at": now, "valid_until": now + 48 * 3600, "bundle_hash": "drill",
        "source": "mock", "model": "drill", "regime": "trend_up", "core_targets": {"USDTTMN": 0.0, "BTCTMN": 0.20},
        "tactical_enabled": {"USDTTMN": False, "BTCTMN": False}, "risk_multiplier": 1.0,
        "forecast_4h": {m: {"direction": "up", "vol": "normal"} for m in config.MARKETS},
        "text_inputs_used": [], "signals_cited": [], "rationale_fa": "سناریوی آزمایشی", "clamps": [], "cost_usd": 0.0})


def shock(rp: Replay, kind: str) -> None:
    g = rp.g["BTCTMN"].copy()
    ts = rp.ts["BTCTMN"]
    for col in ("o", "h", "l", "c"):
        vals = g[col].to_numpy().copy()
        for i, t in enumerate(ts):
            if kind == "crash" and t >= SHOCK_AT:
                k = min(1.0, (t - SHOCK_AT) / (15 * 60))
                vals[i] *= 1 - 0.10 * k
            if kind == "glitch" and SHOCK_AT <= t < SHOCK_AT + 60:
                vals[i] *= 0.70
        g[col] = vals
    rp.g["BTCTMN"] = g


def run(kind: str) -> dict:
    rp = Replay(list(config.MARKETS))
    shock(rp, kind)
    run_dir = Path("run/drill") / kind
    shutil.rmtree(run_dir, ignore_errors=True)
    broker = BacktestBroker()
    end = START + HOURS * 3600
    cfg = EngineCfg(run_dir=run_dir, mode="backtest", manager="remote", sha="drill",
                    end_at=datetime.fromtimestamp(START + 72 * 3600, tz=config.TEHRAN))
    rp.now = START
    eng = Engine(cfg, broker=broker, market=rp.snapshots, bars=rp.bars, clock=lambda: rp.now, alerter=Quiet())
    eng._activate(playbook(START), START)
    worst, held_entry, t = cfg.start_cash, None, START
    while t <= end:
        rp.now = t
        for m in config.MARKETS:
            r = rp.row(m)
            broker.feed_bar(m, t, float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
        eng.tick()
        eq = eng.pf.equity(eng.bids)
        worst = min(worst, eq)
        if held_entry is None and eng.pf.qty("BTCTMN") > 0 and t >= SHOCK_AT - 60:
            held_entry = (eng.pf.qty("BTCTMN"), eq)
        t += 60
    fills = eng.logs.tail("fills", 1000)
    events = [e for e in eng.logs.tail("events", 5000) if any(k in e["event"] for k in KEY_EVENTS)]
    decisions = eng.logs.tail("decisions", 1000)
    sells = [d for d in decisions if d["side"] == "SELL"]
    final = eng.pf.equity(eng.bids)
    rp.now = end
    p_end = float(rp.row("BTCTMN").c)
    rp.now = SHOCK_AT - 60
    p_pre = float(rp.row("BTCTMN").c)
    exposure_pre = held_entry[0] * p_pre / held_entry[1] if held_entry else 0.0
    no_belt = exposure_pre * (p_end / p_pre - 1) * 100
    tehran = lambda ts: datetime.fromtimestamp(ts, tz=config.TEHRAN).strftime("%H:%M")
    return {
        "kind": kind, "exposure_before_shock_pct": round(exposure_pre * 100, 2),
        "with_seatbelt_return_pct": round((final / cfg.start_cash - 1) * 100, 3),
        "worst_point_pct": round((worst / cfg.start_cash - 1) * 100, 3),
        "held_through_return_pct": round(no_belt, 3),
        "btc_move_pct": round((p_end / p_pre - 1) * 100, 2),
        "sells": [{"t": tehran(d["ts"]), "kind": d["kind"], "decided_by": d["decided_by"], "urgency": d["urgency"],
                   "reason_fa": d["reason_fa"]} for d in sells],
        "fills": [{"t": tehran(f["ts"]), "side": f["side"], "qty": f["qty"], "price": round(f["price"])} for f in fills],
        "events": [{"t": tehran(e["ts"]), "event": e["event"],
                    **{k: v for k, v in e.items() if k in ("level", "phase", "mode", "reason", "market")}} for e in events][:15],
        "ended_btc_qty": eng.pf.qty("BTCTMN"),
    }


def main():
    out = {"start": datetime.fromtimestamp(START, tz=config.TEHRAN).isoformat(timespec="minutes"),
           "shock_at": datetime.fromtimestamp(SHOCK_AT, tz=config.TEHRAN).isoformat(timespec="minutes"),
           "crash": run("crash"), "glitch": run("glitch")}
    Path("reports/out").mkdir(parents=True, exist_ok=True)
    Path("reports/out/crash-drill.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:5000])


if __name__ == "__main__":
    main()
