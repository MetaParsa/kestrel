"""1-minute candles → a complete minute grid → 5m / 1h bars, with gap and staleness info.

Wallex returns *flat zero-volume bars* for minutes without trades (47–88% of minutes),
so a missing minute is a real gap (outage or data hole), not just "no trade".
"""
from __future__ import annotations

import pandas as pd

TEHRAN = "Asia/Tehran"


def to_minute_grid(df: pd.DataFrame) -> pd.DataFrame:
    """Index by UTC minute; fill missing minutes with the previous close and v=0.

    Adds `traded` (v > 0) and `missing` (minute absent from the source) flags.
    """
    if df.empty:
        return pd.DataFrame(columns=["o", "h", "l", "c", "v", "traded", "missing"])
    d = df.copy()
    d.index = pd.to_datetime(d["t"].astype("int64"), unit="s", utc=True)
    d = d[["o", "h", "l", "c", "v"]].astype("float64")
    full = pd.date_range(d.index[0], d.index[-1], freq="1min", tz="UTC")
    g = d.reindex(full)
    g["missing"] = g["c"].isna()
    g["c"] = g["c"].ffill()
    for col in ("o", "h", "l"):
        g[col] = g[col].fillna(g["c"])
    g["v"] = g["v"].fillna(0.0)
    g["traded"] = g["v"] > 0
    return g


def resample(grid: pd.DataFrame, rule: str) -> pd.DataFrame:
    """rule: '5min', '15min', '1h'. Bars are labelled by their open time (left edge)."""
    r = grid.resample(rule, label="left", closed="left")
    out = pd.DataFrame({
        "o": r["o"].first(), "h": r["h"].max(), "l": r["l"].min(), "c": r["c"].last(),
        "v": r["v"].sum(), "traded_minutes": r["traded"].sum(), "missing_minutes": r["missing"].sum(),
    })
    return out.dropna(subset=["c"])


def gaps(grid: pd.DataFrame, min_minutes: int = 5) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """Runs of missing minutes at least `min_minutes` long: (start, end, length)."""
    if grid.empty:
        return []
    m = grid["missing"].astype(bool)
    runs, start = [], None
    for ts, miss in m.items():
        if miss and start is None:
            start = ts
        elif not miss and start is not None:
            length = int((ts - start) / pd.Timedelta(minutes=1))
            if length >= min_minutes:
                runs.append((start, ts - pd.Timedelta(minutes=1), length))
            start = None
    if start is not None:
        length = int((m.index[-1] - start) / pd.Timedelta(minutes=1)) + 1
        if length >= min_minutes:
            runs.append((start, m.index[-1], length))
    return runs


def age_seconds(last_update_unix: float, now_unix: float) -> float:
    return max(0.0, now_unix - last_update_unix)


def is_stale(last_update_unix: float, now_unix: float, limit_s: float = 30.0) -> bool:
    """Spec v2 §8.1: no new entries when market data is older than 30 s."""
    return age_seconds(last_update_unix, now_unix) > limit_s
