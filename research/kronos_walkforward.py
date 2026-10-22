"""Kronos walk-forward test on Wallex data (research only — nothing here trades).

Question 1: does Kronos call the 4 h direction / size of USDTTMN and BTCTMN moves better than
lazy baselines, after our fees?
Question 2: does the width of its 30-path band predict how much the price will actually move,
beyond what trailing volatility already tells us?

Runs with the Kronos venv (see research/KRONOS.md):
    .kronos/.venv/Scripts/python.exe research/kronos_walkforward.py --model small
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
KRONOS_DIR = ROOT / ".kronos"
os.environ.setdefault("HF_HOME", str(KRONOS_DIR / "hf"))
sys.path.insert(0, str(KRONOS_DIR / "Kronos"))

MODELS = {   # official NeoQuasar weights only
    "mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048),
    "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
    "base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512),
}
BAR = "15min"
CTX = 400            # 100 h of 15-minute bars
H = 16               # 4 h ahead (same horizon as Claude's scored forecasts)
H1 = 4               # 1 h ahead, read off the same paths
STEP = 4             # a new forecast every hour
PATHS = 30
ROUND_TRIP = 0.006   # 0.30% taker each way on TMN pairs
MAX_TARGET_GAP_MIN = 60
TEHRAN = "Asia/Tehran"
DEVICE: str | None = None


def load_bars(market: str) -> tuple[pd.DataFrame, pd.Series]:
    d = pd.read_parquet(ROOT / "data" / "store" / f"{market}_1m.parquet")
    d.index = pd.to_datetime(d["t"], unit="s", utc=True)
    grid = pd.date_range(d.index[0], d.index[-1], freq="1min")
    missing = pd.Series(~grid.isin(d.index), index=grid)          # minutes the exchange gave us nothing for
    d = d.reindex(grid)
    d["c"] = d["c"].ffill()
    for col in ("o", "h", "l"):
        d[col] = d[col].fillna(d["c"])
    d["v"] = d["v"].fillna(0.0)
    bars = pd.DataFrame({
        "open": d["o"].resample(BAR).first(), "high": d["h"].resample(BAR).max(),
        "low": d["l"].resample(BAR).min(), "close": d["c"].resample(BAR).last(),
        "volume": d["v"].resample(BAR).sum(),
    }).dropna()
    gap_min = missing.resample(BAR).sum().reindex(bars.index).fillna(0)
    return bars, gap_min


def windows(bars: pd.DataFrame) -> list[int]:
    return list(range(CTX, len(bars) - H + 1, STEP))


def run_model(bars: pd.DataFrame, starts: list[int], model_key: str, batch_windows: int) -> np.ndarray:
    """Returns closes of every path: shape (n_windows, PATHS, H)."""
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer
    name, tok_name, max_ctx = MODELS[model_key]
    torch.manual_seed(0)
    tok = KronosTokenizer.from_pretrained(tok_name)
    mdl = Kronos.from_pretrained(name)
    pred = KronosPredictor(mdl, tok, device=DEVICE, max_context=max_ctx)
    ts = pd.Series(bars.index.tz_convert(TEHRAN).tz_localize(None))
    out = np.zeros((len(starts), PATHS, H))
    t0 = time.time()
    for b in range(0, len(starts), batch_windows):
        chunk = starts[b:b + batch_windows]
        dfs, xts, yts = [], [], []
        for i in chunk:
            x = bars.iloc[i - CTX:i][["open", "high", "low", "close", "volume"]].reset_index(drop=True)
            xt = ts.iloc[i - CTX:i].reset_index(drop=True)
            yt = ts.iloc[i:i + H].reset_index(drop=True)
            for _ in range(PATHS):                    # one row per path: sample_count=1 keeps paths separate
                dfs.append(x), xts.append(xt), yts.append(yt)
        res = pred.predict_batch(dfs, xts, yts, pred_len=H, T=1.0, top_p=0.9, sample_count=1, verbose=False)
        for k, i in enumerate(chunk):
            for p in range(PATHS):
                out[b + k, p] = res[k * PATHS + p]["close"].to_numpy()
        done = b + len(chunk)
        print(f"  {done}/{len(starts)} windows, {time.time() - t0:.0f}s", flush=True)
    return out


def spearman(a, b) -> float:
    a, b = pd.Series(a).rank(), pd.Series(b).rank()
    return float(a.corr(b))


def partial_spearman(y, x, z) -> float:
    """Rank correlation of y and x after removing the rank-linear effect of z."""
    ry, rx, rz = (pd.Series(v).rank().to_numpy() for v in (y, x, z))
    def resid(v):
        beta = np.polyfit(rz, v, 1)
        return v - np.polyval(beta, rz)
    return float(np.corrcoef(resid(ry), resid(rx))[0, 1])


def hit_rate(pred_sign, true_sign) -> tuple[float, int]:
    m = (true_sign != 0) & (pred_sign != 0)
    n = int(m.sum())
    return (float((pred_sign[m] == true_sign[m]).mean()) if n else float("nan")), n


def ci95(p: float, n: int) -> float:
    return 1.96 * math.sqrt(p * (1 - p) / n) if n else float("nan")


def score(bars: pd.DataFrame, gap_min: pd.Series, starts: list[int], paths: np.ndarray) -> tuple[dict, pd.DataFrame]:
    close = bars["close"].to_numpy()
    rows = []
    for k, i in enumerate(starts):
        p0 = close[i - 1]
        fin = paths[k, :, H - 1]
        fin1 = paths[k, :, H1 - 1]
        lr = np.diff(np.log(close[i - 1:i + H]))
        trail4 = np.diff(np.log(close[i - 1 - H:i]))
        trail24 = np.diff(np.log(close[i - 1 - 96:i])) if i - 1 - 96 >= 0 else trail4
        rows.append({
            "t": bars.index[i - 1], "p0": p0,
            "r4": close[i + H - 1] / p0 - 1, "r1": close[i + H1 - 1] / p0 - 1,
            "k_r4": fin.mean() / p0 - 1, "k_med4": np.median(fin) / p0 - 1, "k_r1": fin1.mean() / p0 - 1,
            "k_up_share": float((fin > p0).mean()),
            "band_q": (np.quantile(fin, 0.9) - np.quantile(fin, 0.1)) / p0,
            "band_mm": (fin.max() - fin.min()) / p0,
            "in_band": bool(np.quantile(fin, 0.1) <= close[i + H - 1] <= np.quantile(fin, 0.9)),
            "rv_next": float(lr.std() * math.sqrt(H)), "abs_r4": abs(close[i + H - 1] / p0 - 1),
            "rv_trail4": float(trail4.std() * math.sqrt(H)), "rv_trail24": float(trail24.std() * math.sqrt(H)),
            "mom4": close[i - 1] / close[i - 1 - H] - 1,
            "target_gap_min": float(gap_min.iloc[i:i + H].sum()),
        })
    df = pd.DataFrame(rows)
    df = df[df["target_gap_min"] <= MAX_TARGET_GAP_MIN].reset_index(drop=True)
    return summarize(df), df


def summarize(df: pd.DataFrame) -> dict:
    s4 = np.sign(df["r4"].to_numpy())
    out: dict = {"windows": len(df), "from": str(df["t"].iloc[0]), "to": str(df["t"].iloc[-1])}
    # --- direction (4 h, plus 1 h)
    for name, pred in {"kronos": df["k_r4"], "momentum_4h": df["mom4"],
                       "always_up": pd.Series(1.0, index=df.index)}.items():
        hr, n = hit_rate(np.sign(pred.to_numpy()), s4)
        out[f"dir4_{name}"] = {"hit": round(hr, 3), "n": n, "ci95": round(ci95(hr, n * min(STEP, H) // H), 3)}
    hr, n = hit_rate(np.sign(df["k_r1"].to_numpy()), np.sign(df["r1"].to_numpy()))
    out["dir1_kronos"] = {"hit": round(hr, 3), "n": n}
    # --- size of the miss on the 4 h close
    out["mae4_kronos_pct"] = round(float((df["k_r4"] - df["r4"]).abs().mean() * 100), 3)
    out["mae4_naive_pct"] = round(float(df["r4"].abs().mean() * 100), 3)
    out["mae4_skill"] = round(1 - out["mae4_kronos_pct"] / out["mae4_naive_pct"], 3)
    # --- band vs volatility
    out["band_coverage_10_90"] = round(float(df["in_band"].mean()), 3)        # ideal ≈ 0.80
    out["median_band_q_pct"] = round(float(df["band_q"].median() * 100), 3)
    out["median_abs_r4_pct"] = round(float(df["abs_r4"].median() * 100), 3)
    out["spearman_band_vs_rv_next"] = round(spearman(df["band_q"], df["rv_next"]), 3)
    out["spearman_trail4_vs_rv_next"] = round(spearman(df["rv_trail4"], df["rv_next"]), 3)
    out["spearman_trail24_vs_rv_next"] = round(spearman(df["rv_trail24"], df["rv_next"]), 3)
    out["partial_band_given_trail24"] = round(partial_spearman(df["rv_next"], df["band_q"], df["rv_trail24"]), 3)
    out["spearman_band_vs_abs_r4"] = round(spearman(df["band_q"], df["abs_r4"]), 3)
    # --- long-or-flat paper P&L on non-overlapping 4 h windows (spot: no shorting)
    nov = df.iloc[::max(1, H // STEP)]
    for thr in (0.0, ROUND_TRIP):
        take = nov["k_r4"] > thr
        pnl = (nov["r4"][take] - ROUND_TRIP).sum()
        out[f"pnl_kronos_long_if_gt_{thr:.3f}_pct"] = {"sum": round(float(pnl * 100), 2), "trades": int(take.sum()),
                                                       "win": round(float((nov['r4'][take] > ROUND_TRIP).mean()), 3)
                                                       if take.any() else None}
    out["pnl_always_long_each_window_pct"] = round(float((nov["r4"] - ROUND_TRIP).sum() * 100), 2)
    out["pnl_buy_and_hold_pct"] = round(float((nov["p0"].iloc[-1] / nov["p0"].iloc[0] - 1 - ROUND_TRIP) * 100), 2)
    out["pnl_windows"] = len(nov)
    return out


def main():
    global CTX, STEP, PATHS, DEVICE
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small", choices=list(MODELS))
    ap.add_argument("--markets", default="USDTTMN,BTCTMN")
    ap.add_argument("--batch-windows", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="only the last N windows (smoke test)")
    ap.add_argument("--ctx", type=int, default=CTX, help="history bars per forecast")
    ap.add_argument("--step", type=int, default=STEP, help="bars between forecasts (16 = non-overlapping 4 h)")
    ap.add_argument("--paths", type=int, default=PATHS)
    ap.add_argument("--device", default=None, help="cpu / cuda:0 (default: auto)")
    a = ap.parse_args()
    CTX, STEP, PATHS, DEVICE = a.ctx, a.step, a.paths, a.device
    outdir = ROOT / "research" / "kronos_out"
    outdir.mkdir(parents=True, exist_ok=True)
    report = {"model": a.model, "device": DEVICE or "auto", "bar": BAR, "ctx": CTX, "horizon_bars": H, "step_bars": STEP, "paths": PATHS,
              "round_trip": ROUND_TRIP, "markets": {}}
    for m in a.markets.split(","):
        bars, gap_min = load_bars(m)
        starts = windows(bars)
        if a.limit:
            starts = starts[-a.limit:]
        print(f"{m}: {len(bars)} bars, {len(starts)} windows", flush=True)
        t0 = time.time()
        paths = run_model(bars, starts, a.model, a.batch_windows)
        np.save(outdir / f"{m}_{a.model}_ctx{CTX}_paths.npy", paths)
        summ, df = score(bars, gap_min, starts, paths)
        half = len(df) // 2
        summ["first_half"] = summarize(df.iloc[:half].reset_index(drop=True))
        summ["second_half"] = summarize(df.iloc[half:].reset_index(drop=True))
        summ["seconds"] = round(time.time() - t0)
        df.to_csv(outdir / f"{m}_{a.model}_ctx{CTX}_windows.csv", index=False)
        report["markets"][m] = summ
        print(json.dumps(summ, indent=1, default=str), flush=True)
    tag = f"{a.model}_ctx{CTX}_step{STEP}_p{PATHS}" + ("_smoke" if a.limit else "")
    (outdir / f"summary_{tag}.json").write_text(
        json.dumps(report, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
