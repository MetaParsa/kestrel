"""Rewrites the live-trading section of STRATEGY.md from reports/out/live-report.json.

    uv run python -m reports.live_section
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import config

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
START, END = "<!-- LIVE_RESULTS -->", "<!-- /LIVE_RESULTS -->"


def fa(x) -> str:
    return str(x).translate(FA)


def main() -> None:
    r = json.loads(Path("reports/out/live-report.json").read_text(encoding="utf-8"))
    p, d, pb = r["pnl_from_fills"], r["decisions"], r["playbooks"]
    fc = r.get("claude_forecasts_4h", {})
    now = datetime.now(tz=config.TEHRAN).strftime("%H:%M")
    regimes = "، ".join(f"{k} {fa(v)}" for k, v in pb["regimes"].items())
    rows = [
        f"به‌روزشده: جمعه ۱ آبان ساعت {fa(now)} تهران (این بخش با `run/refresh_delivery.sh` از لاگ سرور ساخته می‌شود).", "",
        "| | |", "|---|---|",
        f"| بازه | {fa(r['from'])} تا {fa(r['to'])} (کد `{', '.join(c[:7] for c in r['code_versions'])}`، تگ `live-v1`) |",
        f"| برنامه‌های Claude | {fa(pb['total'])} برنامه، {fa(f'{pb['claude_cost_usd']:.2f}')} دلار؛ رژیم‌ها: {regimes} |",
        f"| تصمیم‌ها | {fa(d['total'])} (رأی دروازه: {', '.join(f'{k} {fa(v)}' for k, v in d['gate'].items())}) |",
        f"| معاملات پرشده | {fa(r['fills'])}، حجم {fa(f'{p['volume_tmn']:,}')} تومان، کارمزد {fa(f'{p['fees_tmn']:,}')} تومان |",
        f"| ارزش حساب (از روی پر شدن‌ها، به قیمت آخر) | {fa(f'{p['equity_tmn']:,}')} تومان ({fa(f'{p['return_pct']:+.3f}')}٪) |",
        f"| پیش‌بینی ۴ ساعته‌ی Claude در دوره‌ی زنده | " + (f"{fa(fc['n'])} مورد، جهت {fa(round(fc['direction_hit_rate'] * 100))}٪ درست "
                                                       f"(مبنا {fa(round(fc['baseline_hit_rate'] * 100))}٪)" if fc.get("n") else "هنوز ۴ ساعت نگذشته")
        + " |",
        f"| خطا / سطح احتیاط | {fa(sum(r['errors'].values()))} خطا؛ سطح‌ها: {', '.join(f'{k}: {fa(v)}' for k, v in r['degrade_levels'].items())} |",
        "",
        "**اولین معامله‌ی واقعی** (۰۳:۲۳): برنامه‌ی Claude «بی‌جهت» با هدف ۸٪ × ضریب ۰٫۵ = ۴٪؛ خرید لیمیت میکر، دو بار قیمت‌گذاری مجدد، "
        "بعد عبور از اسپرد؛ پر شد ۰٫۰۰۰۰۰۹۰۵ BTC به قیمت ۲۲٬۱۲۰٬۸۱۱٬۸۶۶ تومان (≈ ۲۰۰ هزار تومان). موجودی واقعی زیرحساب پراپ با دفتر موتور "
        "دقیقاً یکی بود: ۴٬۷۹۹٬۸۰۷ تومان و ۰٫۰۰۰۰۰۹۰۲ BTC (کارمزد ۰٫۳٪ از بیت‌کوین دریافتی کسر شد).",
        "",
        "**دو اصلاح شب آخر (همه در گیت):** (۱) حساب پراپ سفارش USDTTMN را نپذیرفت → فقط BTCTMN (کامیت `301364b`)؛ "
        "(۲) Claude یک بار ۵٪ × ۰٫۵ = ۲٫۵٪ را انتخاب کرد که زیر حداقل سفارش ۱۵۰ هزار تومان بود و هیچ سفارشی نرفت؛ به‌جای قانون تازه در کد، "
        "این واقعیت و معیار «فعال بودن» داوری به پرامپت Claude اضافه شد (کامیت‌های `75c2700` و `612c972`) و تصمیم با خود او ماند.",
        "",
        "لاگ کامل هر تصمیم: `delivery/DECISIONS-live.md`؛ خام: `delivery/server-logs/main/logs/`.",
    ]
    doc = Path("STRATEGY.md").read_text(encoding="utf-8")
    block = START + "\n" + "\n".join(rows) + "\n" + END
    if END in doc:
        doc = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda m: block, doc, flags=re.S)
    else:
        doc = doc.replace(START, block)
    Path("STRATEGY.md").write_text(doc, encoding="utf-8")
    print("live section updated")


if __name__ == "__main__":
    main()
