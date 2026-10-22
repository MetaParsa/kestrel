"""Server side: build the context bundle for the manager and write it atomically.

The bundle is the ONLY thing the manager sees (its tools read from it). It is hashed
over canonical JSON, and the hash is echoed back in the playbook, so every playbook is
tied to the exact inputs that produced it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config
from data import bars
from data.poll import MarketSnapshot
from manager.schema import canonical_json, content_hash

WEEKDAYS_FA = {0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه", 5: "شنبه", 6: "یکشنبه"}


def _round_bar(t: pd.Timestamp, r: pd.Series) -> list:
    return [int(t.timestamp()), round(float(r.o), 2), round(float(r.h), 2), round(float(r.l), 2),
            round(float(r.c), 2), round(float(r.v), 4)]


def market_section(grid_1m: pd.DataFrame, snap: MarketSnapshot | None, now: float) -> dict:
    """grid_1m: output of data.bars.to_minute_grid (UTC minute index)."""
    h1 = bars.resample(grid_1m, "1h")
    m5 = bars.resample(grid_1m, "5min")
    c = grid_1m["c"]
    last = float(c.iloc[-1])

    def ret(minutes: int) -> float:
        if len(c) <= minutes:
            return 0.0
        return round(last / float(c.iloc[-1 - minutes]) - 1, 5)

    r1h = np.log(h1["c"]).diff().dropna()
    stats = {
        "last": last, "ret_1h": ret(60), "ret_4h": ret(240), "ret_24h": ret(1440), "ret_7d": ret(7 * 1440),
        "vol_24h": round(float(r1h.iloc[-24:].std() or 0.0), 5),
        "vol_1h_30d": round(float(r1h.std() or 0.0), 5),
        "range_30d_pct": round(float((last - c.min()) / (c.max() - c.min())) if c.max() > c.min() else 0.5, 3),
        "traded_minutes_last_hour": int(grid_1m["traded"].iloc[-60:].sum()),
        "missing_minutes_24h": int(grid_1m["missing"].iloc[-1440:].sum()),
    }
    snapshot = None
    if snap is not None:
        snapshot = {"bid": snap.bid, "ask": snap.ask, "spread_bps": round(snap.spread_bps, 2),
                    "imbalance": round(snap.imbalance, 3), "age_s": round(now - snap.fetched_at, 1),
                    "last_trade_age_s": round(now - snap.last_trade_at, 1) if snap.last_trade_at else None}
    return {"snapshot": snapshot, "stats": stats,
            "bars_1h": [_round_bar(t, r) for t, r in h1.iloc[-48:].iterrows()],
            "bars_5m": [_round_bar(t, r) for t, r in m5.iloc[-24:].iterrows()]}


def calendar_info(now: float, end_at: datetime = config.END_AT, cfg: config.FlattenCfg = config.FLATTEN) -> dict:
    t = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(config.TEHRAN)
    hours_to_end = (end_at.timestamp() - now) / 3600
    return {"tehran_time": t.strftime("%Y-%m-%d %H:%M"), "weekday_en": t.strftime("%A"),
            "weekday_fa": WEEKDAYS_FA[t.weekday()], "quiet_day": t.weekday() in (3, 4),   # Thu, Fri
            "hours_to_end": round(hours_to_end, 2), "end_at": end_at.isoformat(),
            "hours_to_flatten_start": round(hours_to_end - cfg.no_new_h, 2)}


def portfolio_summary(pf, bids: dict[str, float], mode: str) -> dict:
    """What the manager sees about our account. `core_exposure` is what event_shock holds."""
    return {
        "equity": round(pf.equity(bids)), "gain": round(pf.gain(bids), 5), "daily_pnl": round(pf.daily_pnl(bids), 5),
        "cash_tmn": round(pf.cash), "mode": mode, "halted": pf.halted,
        "core_exposure": {m: round(pf.exposure(m, bids, "core"), 4) for m in config.MARKETS},
        "tactical_exposure": {m: round(pf.exposure(m, bids, "tactical"), 4) for m in config.MARKETS},
        "positions": [{"market": m, "sleeve": s, "qty": p.qty, "avg_price": round(p.avg_price, 2),
                       "unrealized_pct": round(bids[m] / p.avg_price - 1, 5) if p.avg_price else 0.0}
                      for (m, s), p in sorted(pf.positions.items()) if p.qty > 0],
        "entries_today": pf.entries_today, "activity_today": pf.activity_today,
        "max_drawdown": round(pf.max_drawdown, 5), "realized_pnl": round(pf.realized_pnl), "fees_tmn": round(pf.fees_tmn),
    }


def build_bundle(*, now: float, markets: dict[str, dict], portfolio: dict, recent_decisions: list[dict],
                 previous_playbooks: list[dict], forecast_scorecard: dict, tactical: dict,
                 manager_due: bool, reason: str, manager_request_id: str | None = None) -> dict:
    """`manager_request_id` stays the same across the minute-by-minute rebuilds of one request,
    so the AI box decides (and pays) once per request, not once per bundle."""
    body = {"created_at": round(now, 3), "manager_due": manager_due, "reason": reason,
            "manager_request_id": manager_request_id if manager_due else None,
            "calendar": calendar_info(now), "markets": markets, "portfolio": portfolio,
            "recent_decisions": recent_decisions[:20], "previous_playbooks": previous_playbooks[:5],
            "forecast_scorecard": forecast_scorecard, "tactical": tactical}
    body["hash"] = content_hash(body)
    return body


def write_bundle(bundle: dict, outbox_dir: Path) -> Path:
    outbox_dir.mkdir(parents=True, exist_ok=True)
    path = outbox_dir / "bundle.json"
    tmp = outbox_dir / ".bundle.json.tmp"
    tmp.write_text(canonical_json(bundle), encoding="utf-8")
    os.replace(tmp, path)
    return path
