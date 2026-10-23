"""Shadow / live report from an engine's logs, plus a human-readable decision log.

    uv run python -m reports.shadow_report --logs delivery/server-logs/main/logs --name main
    uv run python -m reports.shadow_report --logs delivery/server-logs/baseline/logs --name baseline

Writes reports/out/<name>-report.json and delivery/DECISIONS-<name>.md (every decision: what the
engine saw, who decided, the gate's verdict, the order and its fill, and the Persian reason).
Claude's 4 h forecasts are scored with manager/scorecard.py against the stored 1-minute candles.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import config
from data import bars as bars_mod
from data import fetch
from manager import scorecard

MARKET_FA = {"USDTTMN": "تتر/تومان", "BTCTMN": "بیت‌کوین/تومان"}


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tehran(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=config.TEHRAN).strftime("%m-%d %H:%M")


def replay_pnl(fills: list[dict], prices: dict[str, float], start: float = config.START_EQUITY_TMN) -> dict:
    """Cash and holdings from the fills alone (fees in the received asset, as Wallex charges them)."""
    cash, held, fees_tmn, volume = start, Counter(), 0.0, 0.0
    for f in sorted(fills, key=lambda x: x["ts"]):
        m, qty, px, fee = f["market"], float(f["qty"]), float(f["price"]), float(f.get("fee") or 0.0)
        volume += qty * px
        if f["side"] == "BUY":
            cash -= qty * px
            held[m] += qty - fee                      # fee taken from the coin received
            fees_tmn += fee * px
        else:
            cash += qty * px - fee                    # fee taken from the TMN received
            held[m] -= qty
            fees_tmn += fee
    value = cash + sum(q * prices.get(m, 0.0) for m, q in held.items())
    return {"equity_tmn": round(value), "return_pct": round((value / start - 1) * 100, 3),
            "volume_tmn": round(volume), "fees_tmn": round(fees_tmn),
            "holdings": {m: q for m, q in held.items() if abs(q) > 1e-12}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--mode", default=None, help="only records of this mode (shadow/live)")
    ap.add_argument("--to", type=float, default=None, help="only records up to this unix time (same-period comparisons)")
    a = ap.parse_args(argv)
    keep = lambda r: (not a.mode or r.get("mode") == a.mode) and (a.to is None or r.get("ts", r.get("created_at", 0)) <= a.to)
    dec = [r for r in read(a.logs / "decisions.jsonl") if keep(r)]
    fills = [r for r in read(a.logs / "fills.jsonl") if keep(r)]
    orders = [r for r in read(a.logs / "orders.jsonl") if keep(r)]
    pbs = [r for r in read(a.logs / "playbooks.jsonl") if keep(r)]
    events = [r for r in read(a.logs / "events.jsonl") if keep(r)]
    human = read(a.logs / "oversight.jsonl")          # oversight/bot.py: who / when / why of every human command

    grids = {m: bars_mod.to_minute_grid(fetch.load(m)) for m in config.MARKETS}
    last_px = {m: float(g["c"].iloc[-1]) for m, g in grids.items()}
    now = min(float(g.index[-1].timestamp()) for g in grids.values()) + 60
    bars_1h = {m: bars_mod.resample(g, "1h") for m, g in grids.items()}
    claude_pbs = [p for p in pbs if p.get("source") == "claude"]

    span = [r["ts"] for r in dec + fills + events if "ts" in r]
    report = {
        "name": a.name, "mode_filter": a.mode,
        "from": tehran(min(span)) if span else None, "to": tehran(max(span)) if span else None,
        "code_versions": sorted({r["git_sha"] for r in dec + events + pbs if r.get("git_sha")}),
        "playbooks": {"total": len(pbs), "by_source": dict(Counter(p.get("source") for p in pbs)),
                      "regimes": dict(Counter(p.get("regime") for p in pbs)),
                      "claude_cost_usd": round(sum(p.get("cost_usd") or 0.0 for p in claude_pbs), 3),
                      "clamped": sum(1 for p in pbs if p.get("clamps"))},
        "claude_forecasts_4h": scorecard.score(claude_pbs, bars_1h, now),
        "decisions": {"total": len(dec), "by_kind": dict(Counter(d["kind"] for d in dec)),
                      "by_decider": dict(Counter(d["decided_by"] for d in dec)),
                      "gate": dict(Counter(d["gate"]["verdict"] for d in dec)),
                      "block_reasons": dict(Counter(r for d in dec if d["gate"]["verdict"] == "block"
                                                    for r in d["gate"]["rules"]))},
        "orders": dict(Counter(o["event"] for o in orders)),
        "fills": len(fills),
        "pnl_from_fills": replay_pnl(fills, last_px),
        "degrade_levels": dict(Counter(e.get("level") for e in events if e["event"] == "degrade_level")),
        "errors": dict(Counter(e["event"] for e in events if "error" in e["event"] or "fail" in e["event"])),
        "human_oversight": dict(Counter(h["event"] for h in human)),
    }
    out = Path("reports/out")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.name}-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    fills_by = {}
    for f in fills:
        fills_by.setdefault(f["decision_id"], []).append(f)
    lines = [f"# لاگ تصمیم‌ها — {a.name}", "",
             "هر سطر یک تصمیم موتور است: چه دید، چه کسی تصمیم گرفت (`manager` = Claude، `gate` = کنترل ریسک، "
             "`flatten` = فروش پایانی، `floor` = حداقل فعالیت)، کنترل ریسک چه گفت، چه سفارشی رفت و چه پر شد. "
             "منبع: `decisions.jsonl` و `fills.jsonl` (هر سطر با شناسه‌ی کد `git_sha`).", "",
             "| زمان (تهران) | حالت | بازار | سمت | نوع | تصمیم‌گیرنده | رژیم | کنترل ریسک | سفارش | پر شد | دلیل |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in dec:
        fs = fills_by.get(d["decision_id"], [])
        filled = "; ".join(f"{f['qty']:g} @ {f['price']:,.0f}" for f in fs) or "—"
        gate = d["gate"]["verdict"] + (f" ({', '.join(d['gate']['rules'])})" if d["gate"]["rules"] else "")
        reason = d.get("reason_fa", "").replace("|", "/")
        lines.append(f"| {tehran(d['ts'])} | {d['mode']} | {MARKET_FA.get(d['market'], d['market'])} | {d['side']} | "
                     f"{d['kind']} | {d['decided_by']} | {d.get('regime') or '—'} | {gate} | "
                     f"`{d.get('order_client_id') or '—'}` | {filled} | {reason} |")
    shown = [h for h in human if h["event"] in ("applied", "review", "needs_confirm", "rejected", "stale")]
    if shown:
        lines += ["", "## نظارت انسانی", "",
                  "دستورهای انسانی (`oversight/`): فقط توقف خرید، کم کردن ریسک، شروع فروش پایانی یا برگرداندن کنترل؛ "
                  "هیچ سفارشی دستی نیست. منبع: `oversight.jsonl`.", "",
                  "| زمان (تهران) | چه کسی | رویداد | دستور | دلیل / یادداشت | وضعیت بعد |", "|---|---|---|---|---|---|"]
        for h in shown:
            who = h.get("who") or {}
            why = (h.get("reason") or h.get("note") or h.get("error") or "—").replace("|", "/")
            lines.append(f"| {tehran(h['ts'])} | {who.get('name') or who.get('id') or '—'} ({who.get('via', '—')}) | "
                         f"{h['event']} | {h.get('command') or '—'}{'' if h.get('value') is None else ' ' + str(h['value'])} | "
                         f"{why} | {h.get('after') or '—'} |")
    Path("delivery").mkdir(exist_ok=True)
    (Path("delivery") / f"DECISIONS-{a.name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
