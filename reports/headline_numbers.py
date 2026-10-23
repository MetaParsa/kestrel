"""Rewrites the self-grading headline in README.md, JUDGES.md and STRATEGY.md from
reports/out/self-grading.json, so the docs never quote an older count than the evidence.

    uv run python -m reports.headline_numbers       # after reports.self_grading

Each doc marks the sentence with <!-- SG:<style> --> … <!-- /SG -->; nothing outside the markers changes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
DOCS = ("README.md", "JUDGES.md", "STRATEGY.md")     # the video script is rebuilt from the JSON by make_video


def pct(x: float) -> int:
    return round(x * 100)


def texts(s: dict) -> dict[str, str]:
    n, pbs, cite = s["forecasts_resolved"], s["claude_playbooks"], s["playbooks_citing_own_scorecard"]
    c, nv, fl = pct(s["direction_hit"]), pct(s["naive_hit"]), pct(s["always_flat_hit"])
    fa = lambda x: str(x).translate(FA)
    return {
        "en": (f"{n} forecasts: Claude {c} %, naive \"same as last 4 h\" {nv} %, \"always flat\" {fl} %. "
               f"{cite} of {pbs} playbooks cite Claude's own scorecard (a keyword match — read the lines yourself in "
               "SELF-GRADING.md); forecasts come two per playbook, so they aren't independent"),
        "fa_short": (f"{fa(n)} پیش‌بینی ۴ ساعته (دو در هر برنامه): Claude {fa(c)}٪، مبنای ساده {fa(nv)}٪، «همیشه خنثی» "
                     f"{fa(fl)}٪؛ در {fa(cite)} برنامه از {fa(pbs)} Claude به کارنامه‌ی خودش استناد کرد"),
        "fa_long": (f"در {fa(cite)} برنامه (از {fa(pbs)}) Claude به کارنامه‌ی خودش استناد کرد (شمارش با کلیدواژه؛ خود جمله‌ها در "
                    "[`SELF-GRADING.md`](delivery/SELF-GRADING.md) هست). هر برنامه دو پیش‌بینی دارد، پس پیش‌بینی‌ها مستقل نیستند. "
                    f"نتیجه‌ی صادقانه ({fa(n)} پیش‌بینی ارزیابی‌شده، [`reports/out/self-grading.json`](reports/out/self-grading.json)): "
                    f"جهت ۴ ساعته {fa(c)}٪ درست، در برابر {fa(nv)}٪ برای مبنای «مثل ۴ ساعت قبل» و {fa(fl)}٪ برای «همیشه خنثی»."),
    }


def main() -> None:
    s = json.loads(Path("reports/out/self-grading.json").read_text(encoding="utf-8"))["summary"]
    t = texts(s)
    for name in DOCS:
        p = Path(name)
        if not p.exists():
            continue
        doc = p.read_text(encoding="utf-8")
        new = re.sub(r"<!-- SG:(\w+) -->.*?<!-- /SG -->",
                     lambda m: f"<!-- SG:{m.group(1)} -->{t[m.group(1)]}<!-- /SG -->", doc, flags=re.S)
        if new != doc:
            p.write_text(new, encoding="utf-8")
            print("updated", name)


if __name__ == "__main__":
    main()
