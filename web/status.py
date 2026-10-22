"""Read-only status page (Persian, RTL): one traffic light, equity, positions, playbook, last decisions.

    uv run uvicorn web.status:app --host 127.0.0.1 --port 8090      # STATUS_RUN_DIR=run/shadow
    # from the laptop: ssh -L 8090:127.0.0.1:8090 trader@SERVER, then open http://localhost:8090

It only reads run/<dir>/status.json and heartbeat — it can't change anything.
"""
from __future__ import annotations

import html
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

import config

RUN_DIR = Path(os.environ.get("STATUS_RUN_DIR", "run/shadow"))
app = FastAPI(title="wallex-agent status", docs_url=None, redoc_url=None)

LIGHT = {"green": ("#1f9d55", "همه‌چیز عادی است.", "کاری لازم نیست."),
         "yellow": ("#d69e2e", "ربات با احتیاط کار می‌کند.", "صبر کنید؛ اگر بیش از دو ساعت زرد ماند، لپ‌تاپ و VPN را بررسی کنید."),
         "red": ("#e53e3e", "ربات خرید نمی‌کند یا متوقف است.", "پیام بله را ببینید؛ اگر قلب ربات نمی‌تپد، سرویس را دوباره روشن کنید.")}


def load(run_dir: Path = RUN_DIR) -> tuple[dict | None, float | None]:
    try:
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status = None
    try:
        beat = float((run_dir / "heartbeat").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        beat = None
    return status, beat


def light(status: dict | None, beat: float | None, now: float) -> str:
    if status is None or beat is None or now - beat > config.LOOP.heartbeat_stale_s:
        return "red"
    if status.get("halted") or status.get("level") == 3 or status.get("gate_mode") == "hard_flatten":
        return "red"
    if status.get("level") == 2 or status.get("gate_mode") == "micro" or (status.get("daily_pnl") or 0) <= -0.01:
        return "yellow"
    return "green"


@app.get("/status.json")
def status_json():
    status, beat = load()
    return JSONResponse({"light": light(status, beat, time.time()), "heartbeat": beat, "status": status})


@app.get("/", response_class=HTMLResponse)
def page():
    now = time.time()
    status, beat = load()
    color, title, todo = LIGHT[light(status, beat, now)]
    e = html.escape
    if status is None:
        body = "<p>هنوز وضعیتی ثبت نشده است.</p>"
    else:
        pb = status.get("playbook") or {}
        rows = "".join(
            f"<tr><td>{e(time.strftime('%H:%M', time.localtime(d['ts'])))}</td><td>{e(d['market'])}</td>"
            f"<td>{e(d['kind'])}</td><td>{e(d['decided_by'])}</td><td>{e(d['verdict'])}</td></tr>"
            for d in status.get("recent_decisions", []))
        expo = status.get("exposure") or {}
        body = f"""
        <div class="cards">
          <div><b>دارایی</b><span>{status.get('equity') or 0:,} تومان</span></div>
          <div><b>بازده</b><span>{(status.get('gain') or 0):+.2%}</span></div>
          <div><b>امروز</b><span>{(status.get('daily_pnl') or 0):+.2%}</span></div>
          <div><b>بیشترین افت</b><span>{status.get('max_drawdown', 0):.2%}</span></div>
        </div>
        <p>سهم بازارها: {' · '.join(f"{e(m)} {v['core']:.0%} پایه، {v['tactical']:.0%} تاکتیکی" for m, v in expo.items())}</p>
        <h3>نقشه‌ی فعلی مدیر ({e(pb.get('source', '—'))}، حالت «{e(pb.get('regime', '—'))}»)</h3>
        <p>{e(pb.get('rationale_fa', 'هنوز نقشه‌ای نرسیده است.'))}</p>
        <h3>آخرین تصمیم‌ها</h3>
        <table><tr><th>ساعت</th><th>بازار</th><th>نوع</th><th>تصمیم‌گیرنده</th><th>کنترل ریسک</th></tr>{rows}</table>
        <p class="small">حالت {e(status.get('mode', ''))} · سطح احتیاط {status.get('level')} · مرحله {e(str(status.get('phase')))}
        · نسخه {e(status.get('git_sha', ''))} · پایان {e(status.get('end_at', ''))}</p>"""
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="15">
<title>ربات والکس</title><style>
body{{font-family:Vazirmatn,Tahoma,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;background:#fafafa;color:#1a202c}}
.light{{display:flex;gap:16px;align-items:center;padding:16px;border-radius:12px;background:#fff;box-shadow:0 1px 3px #0002}}
.dot{{width:48px;height:48px;border-radius:50%;background:{color};flex:none}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:16px 0}}
.cards div{{background:#fff;border-radius:10px;padding:12px;box-shadow:0 1px 2px #0001;display:flex;flex-direction:column}}
table{{width:100%;border-collapse:collapse;background:#fff}}td,th{{padding:6px;border-bottom:1px solid #eee;text-align:right}}
.small{{color:#718096;font-size:13px}}</style></head><body>
<div class="light"><div class="dot"></div><div><h2 style="margin:0">{title}</h2><p style="margin:4px 0 0">{todo}</p></div></div>
{body}</body></html>"""
