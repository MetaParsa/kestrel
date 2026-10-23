"""The AI that grades itself: every Claude 4 h forecast vs what happened, vs naive baselines, and the
places where Claude used its own scorecard to change its call.

    uv run python -m reports.self_grading --logs delivery/server-logs/main/logs
Writes reports/out/self-grading.json and delivery/SELF-GRADING.md.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import config
from data import bars as bars_mod
from data import fetch
from manager.scorecard import H1, H4, _close_at, direction_of, vol_bucket

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
DIR_FA = {"up": "بالا", "down": "پایین", "flat": "خنثی"}
TOPIC = ("scorecard", "forecast", "calls", "hit rate")
REACTION = ("came out", "turned out", "wrong", "were right", "missed", "so i", "now saying", "i am now", "instead",
            "adjust", "lower", "raise", "hit rate", "was right")
NOT_YET = ("empty", "no resolved", "n=0", "no track record", "not yet")


def reacts(line: str) -> bool:
    x = line.lower()
    return any(w in x for w in TOPIC) and any(w in x for w in REACTION) and not any(w in x for w in NOT_YET)


def fa(x) -> str:
    return str(x).translate(FA)


def score_rows(pbs: list[dict], h1: dict[str, pd.DataFrame], now: float) -> list[dict]:
    rows = []
    now_ts = pd.Timestamp(now, unit="s", tz="UTC")
    for pb in pbs:
        t0 = pd.Timestamp(pb["created_at"], unit="s", tz="UTC")
        if t0 + H4 + H1 > now_ts:
            continue
        for m, fc in pb["forecast_4h"].items():
            b = h1[m]
            c0, c1, cprev = _close_at(b, t0), _close_at(b, t0 + H4), _close_at(b, t0 - H4)
            if c0 is None or c1 is None:
                continue
            rets = np.log(b["c"]).diff()
            ref = float(rets.loc[t0 - pd.Timedelta(days=30): t0 - H1].std() or 0.0)
            win = float(rets.loc[t0 - H1 + pd.Timedelta(seconds=1): t0 + H4 - H1].std() or 0.0)
            actual = direction_of(c1 / c0 - 1)
            naive = direction_of(c0 / cprev - 1) if cprev else "flat"
            rows.append({"playbook_id": pb["playbook_id"], "t": pb["created_at"], "market": m,
                         "said": fc["direction"], "actual": actual, "naive": naive, "move_pct": round((c1 / c0 - 1) * 100, 3),
                         "hit": fc["direction"] == actual, "naive_hit": naive == actual, "flat_hit": actual == "flat",
                         "vol_said": fc["vol"], "vol_actual": vol_bucket(win, ref)})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", type=Path, required=True)
    a = ap.parse_args(argv)
    pbs, seen = [], set()
    for line in (a.logs / "playbooks.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("source") == "claude" and r["playbook_id"] not in seen:
            seen.add(r["playbook_id"])
            pbs.append(r)
    pbs.sort(key=lambda p: p["created_at"])
    grids = {m: bars_mod.to_minute_grid(fetch.load(m)) for m in config.MARKETS}
    h1 = {m: bars_mod.resample(g, "1h") for m, g in grids.items()}
    now = min(float(g.index[-1].timestamp()) for g in grids.values()) + 60
    rows = score_rows(pbs, h1, now)
    n = len(rows)

    def rate(key, sub=None):
        rr = [r for r in rows if sub is None or r["market"] == sub]
        return round(sum(r[key] for r in rr) / len(rr), 3) if rr else None
    learning = []
    for pb in pbs:
        cites = [x for x in pb.get("text_inputs_used", []) if reacts(x)]
        vs = [x for x in pb.get("text_inputs_used", []) if x.lower().startswith("vs twin")]
        if cites or vs:
            learning.append({"playbook_id": pb["playbook_id"], "t": pb["created_at"], "regime": pb["regime"],
                             "forecast": {m: f["direction"] + "/" + f["vol"] for m, f in pb["forecast_4h"].items()},
                             "self_grading": cites, "vs_twin": vs})
    summary = {
        "claude_playbooks": len(pbs), "forecasts_resolved": n,
        "direction_hit": rate("hit"), "naive_hit": rate("naive_hit"), "always_flat_hit": rate("flat_hit"),
        "by_market": {m: {"n": sum(1 for r in rows if r["market"] == m), "claude": rate("hit", m),
                          "naive": rate("naive_hit", m), "always_flat": rate("flat_hit", m)} for m in config.MARKETS},
        "vol_hit": round(sum(r["vol_said"] == r["vol_actual"] for r in rows) / n, 3) if n else None,
        "said": dict(Counter(r["said"] for r in rows)), "actual": dict(Counter(r["actual"] for r in rows)),
        "playbooks_citing_own_scorecard": sum(1 for x in learning if x["self_grading"]),
        "playbooks_with_vs_twin": sum(1 for x in learning if x["vs_twin"]),
    }
    Path("reports/out").mkdir(parents=True, exist_ok=True)
    Path("reports/out/self-grading.json").write_text(
        json.dumps({"summary": summary, "rows": rows, "learning": learning}, ensure_ascii=False, indent=1), encoding="utf-8")

    def when(t):
        return fa(datetime.fromtimestamp(t, tz=config.TEHRAN).strftime("%m-%d %H:%M"))
    pct = lambda x: "—" if x is None else fa(round(x * 100)) + "٪"
    md = ["# هوش مصنوعی‌ای که به خودش نمره می‌دهد", "",
          "Claude در هر برنامه، جهت و نوسان ۴ ساعت بعدِ هر بازار را پیش‌بینی می‌کند. چهار ساعت بعد، کد پیش‌بینی را با بازار واقعی "
          "مقایسه می‌کند (جهت با باند خنثیِ ±۰٫۲٪؛ نوسان در برابر نوسان ۳۰ روزه)، کنارش دو مبنای ساده را هم نمره می‌دهد، "
          "و کارنامه را در چرخه‌ی بعد به خود Claude برمی‌گرداند. همه‌چیز بدون نگاه به آینده: فقط کندل‌هایی که بسته شده بودند.", "",
          f"**{fa(len(pbs))} برنامه، {fa(n)} پیش‌بینی ارزیابی‌شده.**", "",
          "| | Claude | مبنای ساده («مثل ۴ ساعت قبل») | «همیشه خنثی» |", "|---|---|---|---|",
          f"| همه | {pct(summary['direction_hit'])} | {pct(summary['naive_hit'])} | {pct(summary['always_flat_hit'])} |"]
    for m, v in summary["by_market"].items():
        md.append(f"| {m} ({fa(v['n'])}) | {pct(v['claude'])} | {pct(v['naive'])} | {pct(v['always_flat'])} |")
    md += ["", f"پیش‌بینی نوسان: {pct(summary['vol_hit'])} درست.", "",
           "**صادقانه:** نمونه کوچک است و بیشتر ساعت‌ها بازار خنثی بوده؛ ارزش این بخش در خود حلقه‌ی بازخورد است، نه در یک عدد.", "",
           "## جاهایی که Claude از کارنامه‌ی خودش استفاده کرد", ""]
    for x in learning:
        if x["self_grading"]:
            md.append(f"- **{when(x['t'])}** (`{x['playbook_id']}`، {x['regime']}، پیش‌بینی: "
                      f"{', '.join(f'{m} {f}' for m, f in x['forecast'].items())}): " + " · ".join(x["self_grading"]))
    if any(x["vs_twin"] for x in learning):
        md += ["", "## مقایسه با دوقلوی قاعده‌محور (به قلم خود Claude)", ""]
        for x in learning:
            if x["vs_twin"]:
                md.append(f"- **{when(x['t'])}** ({x['regime']}): " + " · ".join(x["vs_twin"]))
    md += ["", "## همه‌ی پیش‌بینی‌ها", "", "| زمان | بازار | Claude گفت | واقعیت | حرکت ۴ ساعته | مبنا گفت | درست؟ |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {when(r['t'])} | {r['market']} | {DIR_FA[r['said']]} | {DIR_FA[r['actual']]} | {fa(r['move_pct'])}٪ | "
                  f"{DIR_FA[r['naive']]} | {'✓' if r['hit'] else '✗'} |")
    Path("delivery/SELF-GRADING.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
