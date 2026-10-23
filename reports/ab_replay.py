"""Control group on equal footing: the same engine, the same minutes of real Wallex data, two managers.

A) Claude's recorded playbooks, activated at the minute they were written (as the live engine did);
B) the rule-based manager (manager/mock.py), the twin;
plus two references with no manager: stay in TMN, and hold BTC from the start.
Execution, risk gate, exits and fees are identical (BacktestBroker), so the difference is the manager.

    uv run python -m reports.ab_replay --logs delivery/server-logs/main/logs            # BTCTMN only (live rule)
    WALLEX_TRADABLE=USDTTMN,BTCTMN uv run python -m reports.ab_replay --logs ...        # what the plans were written for
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

import config
from engine.loop import Engine, EngineCfg
from engine.modes import BacktestBroker
from manager.schema import Playbook
from reports.backtest import Quiet, Replay


def load_claude_playbooks(logs: Path) -> list[Playbook]:
    out, seen = [], set()
    for line in (logs / "playbooks.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("source") != "claude" or rec["playbook_id"] in seen:
            continue
        seen.add(rec["playbook_id"])
        out.append(Playbook.model_validate({k: v for k, v in rec.items() if k not in ("git_sha", "mode")}))
    return sorted(out, key=lambda p: p.created_at)


def run(rp: Replay, start: float, end: float, manager: str, playbooks: list[Playbook], run_dir: Path) -> dict:
    shutil.rmtree(run_dir, ignore_errors=True)
    broker = BacktestBroker()
    rp.now = start
    cfg = EngineCfg(run_dir=run_dir, mode="backtest", manager=manager, sha="ab-replay", end_at=config.END_AT)
    eng = Engine(cfg, broker=broker, market=rp.snapshots, bars=rp.bars, clock=lambda: rp.now, alerter=Quiet())
    queue = list(playbooks)
    peak, max_dd, regimes, curve = cfg.start_cash, 0.0, [], []
    t = start
    while t <= end:
        rp.now = t
        while queue and queue[0].created_at <= t:
            pb = queue.pop(0)
            eng._activate(pb, t)                      # what the inbox does once the server accepts it
            regimes.append(pb.regime)
        for m in config.MARKETS:
            r = rp.row(m)
            broker.feed_bar(m, t, float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
        eng.tick()
        if eng.bids:
            eq = eng.pf.equity(eng.bids)
            peak = max(peak, eq)
            max_dd = max(max_dd, 1 - eq / peak)
            if int(t) % 1800 == 0:
                curve.append((int(t), round(eq)))
        t += 60
    if manager == "rules":
        regimes = [p["regime"] for p in eng.playbook_history]
    fills = eng.logs.tail("fills", 100_000)
    final = eng.pf.equity(eng.bids)
    switches = sum(1 for a, b in zip(regimes, regimes[1:]) if a != b)
    exposure = {m: eng.pf.exposure(m, eng.bids) for m in config.MARKETS}
    exit_cost = sum(x * config.FEES[m][1] for m, x in exposure.items())      # selling back to TMN at taker
    return {"return_pct": round((final / cfg.start_cash - 1) * 100, 3),
            "after_selling_to_tmn_pct": round(((final / cfg.start_cash - 1) - exit_cost) * 100, 3),
            "max_drawdown_pct": round(max_dd * 100, 3),
            "fills": len(fills), "fees_tmn": round(sum(f["fee"] * (f["price"] if f.get("fee_asset") not in ("TMN", None)
                                                                     else 1) for f in fills)),
            "playbooks": len(regimes), "regime_switches": switches,
            "exposure_end": {m: round(eng.pf.exposure(m, eng.bids), 4) for m in config.MARKETS},
            "equity_halfhourly": curve}


def hold_btc(rp: Replay, start: float, end: float, share: float) -> dict:
    rp.now = start
    p0 = float(rp.row("BTCTMN").c)
    rp.now = end
    p1 = float(rp.row("BTCTMN").c)
    fee = config.FEES["BTCTMN"][1]
    ret = share * ((p1 / p0) * (1 - fee) ** 2 - 1)
    return {"return_pct": round(ret * 100, 3), "note": f"{share:.0%} in BTCTMN at the start, taker fee both ways"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", type=Path, required=True, help="the live engine's logs (playbooks.jsonl)")
    ap.add_argument("--out", type=Path, default=Path("reports/out"))
    ap.add_argument("--end", type=float, default=0.0, help="unix end time (default: last stored minute)")
    a = ap.parse_args(argv)
    pbs = load_claude_playbooks(a.logs)
    rp = Replay(list(config.MARKETS))
    lo, hi = rp.span()
    start = (pbs[0].created_at // 60) * 60
    end = min(a.end or hi, hi)
    pbs = [p for p in pbs if p.created_at <= end]       # only the playbooks that existed inside the window
    tag = "+".join(config.TRADABLE)
    res = {"tradable": list(config.TRADABLE),
           "from": datetime.fromtimestamp(start, tz=config.TEHRAN).isoformat(timespec="minutes"),
           "to": datetime.fromtimestamp(end, tz=config.TEHRAN).isoformat(timespec="minutes"),
           "claude_playbooks": len(pbs)}
    for name, manager, plays in (("claude", "remote", pbs), ("rules_twin", "rules", [])):
        t0 = time.time()
        res[name] = run(rp, start, end, manager, plays, Path("run/ab") / f"{tag}-{name}")
        res[name]["seconds"] = round(time.time() - t0)
        print(name, {k: v for k, v in res[name].items() if k != "equity_halfhourly"}, flush=True)
    res["stay_in_tmn"] = {"return_pct": 0.0}
    res["hold_btc_10pct"] = hold_btc(rp, start, end, 0.10)
    res["hold_btc_100pct"] = hold_btc(rp, start, end, 1.0)
    a.out.mkdir(parents=True, exist_ok=True)
    out = a.out / f"ab-replay-{tag}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("claude", "rules_twin")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
