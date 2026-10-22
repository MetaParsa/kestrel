"""Download 1-minute candles from Wallex's UDF endpoint into parquet, resumably.

    uv run python -m data.fetch --days 30                 # USDTTMN, BTCTMN, BTCUSDT
    uv run python -m data.fetch --days 90 --symbols BTCTMN

Pages are one day each (> 1440 one-minute bars returns "no_data"). Days already
fully stored are skipped, so the command can be re-run after an interruption.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from wallex.client import WallexClient

STORE = Path(__file__).parent / "store"
DEFAULT_SYMBOLS = ["USDTTMN", "BTCTMN", "BTCUSDT"]
PAGE = 86400 - 60          # 1439 bars per request
COLUMNS = ["t", "o", "h", "l", "c", "v"]


def path_for(symbol: str, store: Path = STORE) -> Path:
    return store / f"{symbol}_1m.parquet"


def load(symbol: str, store: Path = STORE) -> pd.DataFrame:
    p = path_for(symbol, store)
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=COLUMNS)


def _merge(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    df = pd.concat([old, new], ignore_index=True) if len(old) else new
    df = df.drop_duplicates("t", keep="last").sort_values("t").reset_index(drop=True)
    df["t"] = df["t"].astype("int64")
    for c in "ohlcv":
        df[c] = df[c].astype("float64")
    return df


def fetch_history(client: WallexClient, symbol: str, days: int, *, end: int | None = None,
                  store: Path = STORE, log=print) -> pd.DataFrame:
    store.mkdir(parents=True, exist_ok=True)
    end = end or int(time.time()) // 60 * 60
    start = end - days * 86400 + 60      # exactly days*1440 bars, so N days = N requests
    have = load(symbol, store)
    have_t = set(have["t"].astype("int64")) if len(have) else set()
    frames, fetched, skipped = [], 0, 0
    t_end = end
    while t_end > start:
        t_start = max(t_end - PAGE, start)
        expected = (t_end - t_start) // 60 + 1
        stored = sum(1 for t in range(t_start, t_end + 1, 60) if t in have_t)
        if stored >= expected - 1:          # page already complete (allow the open minute)
            skipped += 1
        else:
            cols = client.candles(symbol, "1", t_start, t_end)
            fetched += 1
            if cols["t"]:
                frames.append(pd.DataFrame(cols)[COLUMNS])
        t_end = t_start - 60
    df = _merge(have, pd.concat(frames, ignore_index=True)) if frames else have
    if len(df):
        df.to_parquet(path_for(symbol, store), index=False)
    log(f"{symbol}: {len(df)} bars stored, {fetched} pages fetched, {skipped} skipped")
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    args = ap.parse_args(argv)
    client = WallexClient()          # public endpoints only, no key needed
    try:
        for s in args.symbols:
            fetch_history(client, s, args.days)
    finally:
        print(f"requests made: {client.requests_made}")
        client.close()


if __name__ == "__main__":
    main()
