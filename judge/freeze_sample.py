"""Copy the candle store into the committed sample, so `judge.demo` reproduces the latest reports.

    uv run python -m judge.freeze_sample        # after run/refresh_delivery.sh has rebuilt reports/out

Run it whenever reports/out/self-grading.json is regenerated from newer candles (the other reports
use fixed windows and don't change). Writes data/sample/*.parquet and data/sample/MANIFEST.json.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config

REPO = Path(__file__).resolve().parent.parent
STORE, SAMPLE = REPO / "data" / "store", REPO / "data" / "sample"
SYMBOLS = ("USDTTMN", "BTCTMN", "BTCUSDT")


def utc(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="minutes")


def main() -> None:
    files = {}
    for s in SYMBOLS:
        src, dst = STORE / f"{s}_1m.parquet", SAMPLE / f"{s}_1m.parquet"
        shutil.copyfile(src, dst)
        d = pd.read_parquet(dst)
        files[dst.name] = {"sha256": hashlib.sha256(dst.read_bytes()).hexdigest(), "bars": len(d),
                           "first_utc": utc(int(d.t.min())), "last_utc": utc(int(d.t.max()))}
    last = min(int(pd.read_parquet(SAMPLE / name).t.max()) for name in files)
    manifest = {"source": "Wallex public UDF candles (/v1/udf/history, resolution 1), downloaded with `python -m data.fetch`",
                "frozen_at": datetime.fromtimestamp(last, config.TEHRAN).strftime("%Y-%m-%d %H:%M Tehran")
                             + " (copy of data/store used by the committed reports)",
                "files": files}
    (SAMPLE / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
