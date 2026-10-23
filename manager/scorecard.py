"""Scores each playbook's 4 h forecast against what happened.

direction: sign of the 4 h close-to-close return with a ±0.2% flat band.
vol: realized |1 h returns| std over the 4 h vs the trailing 30 d 1 h std
     (ratio > 1.3 high, < 0.7 low, else normal).
Baseline: persistence — "the next 4 h move like the previous 4 h".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FLAT_BAND = 0.002
H1 = pd.Timedelta(hours=1)
H4 = pd.Timedelta(hours=4)
# Bars are labelled by their OPEN time, so a bar labelled L is only known at L + 1 h.
# Every lookup below uses closes known at the time in question — no lookahead.


def direction_of(ret: float) -> str:
    return "up" if ret > FLAT_BAND else "down" if ret < -FLAT_BAND else "flat"


def vol_bucket(window_std: float, ref_std: float) -> str:
    r = window_std / ref_std if ref_std > 0 else 1.0
    return "high" if r > 1.3 else "low" if r < 0.7 else "normal"


def _close_at(bars: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    """Close of the last bar that had finished by `ts`."""
    s = bars["c"].loc[: ts - H1]
    return float(s.iloc[-1]) if len(s) else None


def score(playbooks: list[dict], bars_1h: dict[str, pd.DataFrame], now: float) -> dict:
    """playbooks: dicts with created_at and forecast_4h {market: {direction, vol}}; bars_1h indexed by UTC time."""
    rows = []
    now_ts = pd.Timestamp(now, unit="s", tz="UTC")
    for pb in playbooks:
        t0 = pd.Timestamp(pb["created_at"], unit="s", tz="UTC")
        if t0 + H4 + H1 > now_ts:
            continue                                   # the bar closing the window isn't finished yet
        for m, fc in pb["forecast_4h"].items():
            b = bars_1h.get(m)
            if b is None or b.empty:
                continue
            c0, c1, cprev = _close_at(b, t0), _close_at(b, t0 + H4), _close_at(b, t0 - H4)
            if c0 is None or c1 is None:
                continue
            rets = np.log(b["c"]).diff()                 # labelled by the bar whose close ends it
            ref = float(rets.loc[t0 - pd.Timedelta(days=30): t0 - H1].std() or 0.0)
            win = float(rets.loc[t0 - H1 + pd.Timedelta(seconds=1): t0 + H4 - H1].std() or 0.0)
            actual = direction_of(c1 / c0 - 1)
            base = direction_of(c0 / cprev - 1) if cprev else "flat"
            rows.append({"playbook_id": pb.get("playbook_id", ""), "market": m,
                         "said": fc["direction"], "actual": actual, "baseline": base,
                         "dir_hit": fc["direction"] == actual, "base_hit": base == actual,
                         "vol_said": fc["vol"], "vol_actual": vol_bucket(win, ref)})
    if not rows:
        return {"n": 0, "note": "no forecast resolved yet (needs 4 h)"}
    n = len(rows)
    return {
        "n": n,
        "direction_hit_rate": round(sum(r["dir_hit"] for r in rows) / n, 3),
        "baseline_hit_rate": round(sum(r["base_hit"] for r in rows) / n, 3),
        "vol_hit_rate": round(sum(r["vol_said"] == r["vol_actual"] for r in rows) / n, 3),
        "recent": rows[-6:],
    }
