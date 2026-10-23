"""Builds the ~5-minute demo video from real logs: Persian scenes + Persian neural voice-over.

    .venv/Scripts/python.exe -m reports.video.make_video            # pull live data, render, encode
    .venv/Scripts/python.exe -m reports.video.make_video --no-pull  # use the last pulled data

Needs: ssh alias `wallex-iran` (read-only pull of run/main + run/baseline), Microsoft Edge (headless
screenshots), ffmpeg, and the edge-tts CLI in .kronos/.venv-tts (narration text is sent to Microsoft's
public TTS service; nothing else leaves the laptop). Output: run/video/wallex-demo.mp4
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import shutil
import subprocess
import time
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

OUT = ROOT / "run" / "video"
DATA = OUT / "data"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

def edge_shot(cmd, **kw):
    """Edge's launcher can return before the screenshot is written: delete the old file, then wait for the new one."""
    out = Path(next(a for a in cmd if a.startswith("--screenshot=")).split("=", 1)[1])
    out.unlink(missing_ok=True)
    kw.pop("check", None)
    subprocess.run(cmd, **kw)
    for _ in range(150):
        if out.exists() and out.stat().st_size > 0:
            time.sleep(0.4)
            return
        time.sleep(0.2)
    raise RuntimeError(f"no screenshot: {out}")

TTS = ROOT / ".kronos" / ".venv-tts" / "Scripts" / "edge-tts.exe"
FFMPEG = shutil.which("ffmpeg") or str(next(Path.home().glob(
    "AppData/Local/Microsoft/WinGet/Packages/*FFmpeg*/*/bin/ffmpeg.exe")))
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe") if FFMPEG.endswith(".exe") else "ffprobe"
VOICE = "fa-IR-FaridNeural"
SSH = ["ssh", "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
       "wallex-iran"]
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(x) -> str:
    return str(x).translate(FA)


def money(x: float) -> str:
    return fa(f"{x:,.0f}").replace(",", "٬")


def pct(x: float, nd: int = 2) -> str:
    s = f"{x:+.{nd}f}" if x else f"{x:.{nd}f}"
    return "\u2066" + fa(s.replace("-", "−")) + "٪\u2069"      # LTR isolate: the sign stays in front in RTL text


def tehran(ts: float) -> str:
    return fa(datetime.fromtimestamp(ts, tz=config.TEHRAN).strftime("%H:%M"))


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


# ---- data -------------------------------------------------------------------------------------
def pull() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    cmd = SSH + ["cd /opt/wallex-agent/run && sudo tar -cf - main/logs main/status.json main/playbook.json "
                 "baseline/logs baseline/status.json canary.json 2>/dev/null"]
    tar = subprocess.run(cmd, capture_output=True, timeout=120).stdout
    subprocess.run(["tar", "-xf", "-", "-C", str(DATA)], input=tar, check=True)


def load() -> dict:
    d: dict = {}
    d["status"] = json.loads((DATA / "main/status.json").read_text(encoding="utf-8"))
    d["base_status"] = json.loads((DATA / "baseline/status.json").read_text(encoding="utf-8"))
    d["canary"] = json.loads((DATA / "canary.json").read_text(encoding="utf-8")) if (DATA / "canary.json").exists() else {}
    logs = DATA / "main/logs"
    d["live_dec"] = [r for r in read_jsonl(logs / "decisions.jsonl") if r.get("mode") == "live"
                     and "G_BELOW_EXCHANGE_MIN" not in r["gate"]["rules"]]   # dust left after a sale: not news
    d["live_fills"] = [r for r in read_jsonl(logs / "fills.jsonl") if r.get("mode") == "live"]
    d["live_events"] = [r for r in read_jsonl(logs / "events.jsonl") if r.get("mode") == "live"]
    d["all_dec"] = read_jsonl(logs / "decisions.jsonl")
    pbs = [p for p in read_jsonl(logs / "playbooks.jsonl") if p.get("source") == "claude"]
    d["pbs"] = pbs
    d["pb"] = pbs[-1] if pbs else json.loads((DATA / "main/playbook.json").read_text(encoding="utf-8"))
    rep = ROOT / "reports/out"
    d["shadow"] = json.loads((rep / "main-report.json").read_text(encoding="utf-8"))
    d["base"] = json.loads((rep / "baseline-report.json").read_text(encoding="utf-8"))
    bt = rep / "backtest-summary.json"
    d["bt"] = json.loads(bt.read_text(encoding="utf-8")) if bt.exists() else None
    for key, name in (("sg", "self-grading.json"), ("ab", "ab-replay-BTCTMN.json"), ("drill", "crash-drill.json")):
        f = rep / name
        d[key] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    spend = read_jsonl(ROOT / "run/ai_box/spend.jsonl")
    d["claude_cost"] = sum(s.get("cost_usd", 0) for s in spend)
    d["claude_calls"] = len(spend)
    return d


# ---- scenes ------------------------------------------------------------------------------------
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;600;800&display=swap');
:root{--bg:#0b1220;--card:#131c2e;--line:#22304a;--ink:#e8eefc;--mute:#8fa3c4;--acc:#38bdf8;--ok:#34d399;--warn:#fbbf24;--bad:#f87171}
*{box-sizing:border-box} body{margin:0;width:1920px;height:1080px;background:var(--bg);color:var(--ink);
font-family:Vazirmatn,Tahoma,sans-serif;direction:rtl;overflow:hidden}
.wrap{padding:70px 90px;height:100%;display:flex;flex-direction:column;gap:28px}
h1{font-size:64px;margin:0;font-weight:800} h2{font-size:48px;margin:0;font-weight:800}
.sub{font-size:30px;color:var(--mute)} .tag{display:inline-block;background:#0c4a6e;color:#bae6fd;border-radius:12px;padding:6px 18px;font-size:24px}
.grid{display:grid;gap:24px} .g2{grid-template-columns:1fr 1fr} .g3{grid-template-columns:1fr 1fr 1fr} .g4{grid-template-columns:repeat(4,1fr)}
.card{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:28px 32px}
.k{font-size:24px;color:var(--mute)} .v{font-size:44px;font-weight:800;margin-top:6px}
.ok{color:var(--ok)} .warn{color:var(--warn)} .bad{color:var(--bad)} .acc{color:var(--acc)}
table{width:100%;border-collapse:collapse;font-size:23px} td,th{padding:12px 14px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}
th{color:var(--mute);font-weight:600} .ltr{direction:ltr;unicode-bidi:isolate;font-family:Consolas,monospace}
.quote{font-size:30px;line-height:1.9;border-right:6px solid var(--acc);padding-right:26px}
.small{font-size:22px;color:var(--mute);line-height:1.7} ul{margin:0;padding-right:30px;font-size:27px;line-height:1.8}
.foot{margin-top:auto;font-size:22px;color:var(--mute);display:flex;justify-content:space-between}
.box{background:var(--card);border:2px solid var(--line);border-radius:20px;padding:22px;text-align:center;font-size:27px}
.arrow{font-size:46px;color:var(--mute);text-align:center;align-self:center}
"""


def page(body: str, n: int, total: int) -> str:
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'><style>{CSS}</style></head>"
            f"<body><div class='wrap'>{body}<div class='foot'><span>موتور معاملاتی هوشمند والکس · هکاتون استارکوچ</span>"
            f"<span>{fa(n)} / {fa(total)}</span></div></div></body></html>")


def scenes(d: dict) -> list[tuple[str, str]]:
    st, pb, sh, base = d["status"], d["pb"], d["shadow"], d["base"]
    eq = float(st.get("equity") or 5_000_000)
    ret = (eq / config.START_EQUITY_TMN - 1) * 100
    exp = st.get("exposure", {}).get("BTCTMN", {})
    btc_exp = (exp.get("core") or 0) * 100
    live_fills, live_dec = d["live_fills"], d["live_dec"]
    out: list[tuple[str, str]] = []

    # 1 — title
    out.append(("""<div style='margin:auto 0'><span class='tag'>چالش والکس · هکاتون Agentic AI استارکوچ</span>
<h1 style='font-size:92px;margin-top:30px'>موتور معاملاتی هوشمند والکس</h1>
<div class='sub' style='font-size:40px;margin-top:20px'>Claude مسیر را تعیین می‌کند، کد ترمز می‌گیرد.</div>
<div class='small' style='margin-top:40px'>تیم یک‌نفره: پارسا، با Claude Code به‌عنوان ایجنت کدنویس · github.com/MetaParsa/kestrel</div></div>""",
                "سلام. این موتور معاملاتی هوشمند ما برای چالش والکس است. سؤال ما این بود: از کجا بفهمیم هوش مصنوعی "
                "واقعاً چیزی اضافه می‌کند، و مطمئن باشیم نمی‌تواند پول را به باد بدهد؟ جواب سه ایده است: هوش مصنوعی‌ای که به خودش "
                "نمره می‌دهد، یک گروه کنترل بدون هوش مصنوعی، و یک کمربند ایمنی که مدل نمی‌تواند دورش بزند. همه را روی حساب واقعی والکس نشان می‌دهیم."))

    # 2 — architecture
    out.append(("""<h2>معماری: دو مغز، یک ترمز</h2>
<div class='grid' style='grid-template-columns:1fr 90px 1fr 90px 1fr;align-items:stretch;margin-top:20px'>
<div class='box'><div class='acc' style='font-size:34px;font-weight:800'>Claude Opus 5.5</div><div class='small'>روی لپ‌تاپ · هر ساعت (عصر آخر هر ۱۵ دقیقه)<br>رژیم بازار، سهم هدف، ضریب ریسک<br>پیش‌بینی ۴ ساعته + دلیل فارسی<br>۸ ابزار فقط‌خواندنی</div></div>
<div class='arrow'>←</div>
<div class='box'><div class='warn' style='font-size:34px;font-weight:800'>دروازه‌ی ریسک</div><div class='small'>کد قطعی روی سرور ایران<br>سقف حجم، حد ضرر، توقف روزانه<br>فروش اضطراری در افت ۳٪<br>مدل نمی‌تواند دورش بزند</div></div>
<div class='arrow'>←</div>
<div class='box'><div class='ok' style='font-size:34px;font-weight:800'>اجرا در والکس</div><div class='small'>لیمیت میکر-اول<br>قیمت‌گذاری مجدد، بدون مارکت خام<br>پیگیری با client_id<br>فروش پایانی به تومان</div></div></div>
<div class='grid g3' style='margin-top:10px'>
<div class='card'><div class='k'>اتصال</div><div class='small'>سرور ایران به Anthropic دسترسی ندارد؛ لپ‌تاپ هر ۶۰ ثانیه وضعیت را می‌کشد و برنامه را می‌فرستد. بدون تونل.</div></div>
<div class='card'><div class='k'>نظارت</div><div class='small'>هشدار پیام‌رسان بله، واچ‌داگ دقیقه‌ای، صفحه‌ی وضعیت فارسی.</div></div>
<div class='card'><div class='k'>شفافیت</div><div class='small'>هر تصمیم با «چه دید، چه کسی تصمیم گرفت، دروازه چه گفت، چرا» و شناسه‌ی نسخه‌ی کد ثبت می‌شود.</div></div></div>""",
                "معماری سه لایه دارد. لایه‌ی اول Claude Opus است که هر ساعت، و در عصر آخر هر پانزده دقیقه، روی لپ‌تاپ اجرا می‌شود، بازار را با "
                "ابزارهای فقط‌خواندنی بررسی می‌کند و یک برنامه می‌نویسد: رژیم بازار، سهم هدف، ضریب ریسک، "
                "پیش‌بینی چهار ساعته و دلیلش به فارسی. لایه‌ی دوم دروازه‌ی ریسک است؛ کد قطعی روی سرور که هیچ "
                "برنامه‌ای نمی‌تواند از آن عبور کند. لایه‌ی سوم اجراست: سفارش لیمیت با اولویت میکر تا کارمزد کمتری بدهیم. "
                "سرور در ایران به Anthropic دسترسی ندارد، پس لپ‌تاپ هر شصت ثانیه وضعیت را می‌گیرد و برنامه را می‌فرستد."))

    # 3 — Claude's real decision
    sig = "".join(f"<li class='ltr' style='direction:ltr;text-align:left;font-size:22px'>{html.escape(s)}</li>"
                  for s in (pb.get("signals_cited") or [])[:4])
    fc = pb.get("forecast_4h", {})
    fct = " · ".join(f"{m}: {html.escape(v.get('direction',''))}/{html.escape(v.get('vol',''))}" for m, v in fc.items())
    tgt = pb.get("core_targets", {}).get("BTCTMN", 0) * 100
    out.append((f"""<h2>یک تصمیم واقعی Claude <span class='tag ltr'>{html.escape(pb.get('playbook_id',''))}</span></h2>
<div class='grid g4'>
<div class='card'><div class='k'>رژیم</div><div class='v acc ltr'>{html.escape(pb.get('regime',''))}</div></div>
<div class='card'><div class='k'>سهم هدف بیت‌کوین</div><div class='v'>{fa(f'{tgt:.0f}')}٪</div></div>
<div class='card'><div class='k'>ضریب ریسک</div><div class='v'>{fa(pb.get('risk_multiplier'))}</div></div>
<div class='card'><div class='k'>هزینه‌ی این تصمیم</div><div class='v'>{fa(f"{pb.get('cost_usd') or 0:.3f}")}$</div></div></div>
<div class='card quote'>{html.escape(pb.get('rationale_fa',''))}</div>
<div class='grid g2'><div class='card'><div class='k'>اعدادی که به آن‌ها استناد کرد</div><ul>{sig}</ul></div>
<div class='card'><div class='k'>پیش‌بینی ۴ ساعته (بعداً نمره می‌گیرد)</div><div class='ltr' style='font-size:26px;margin-top:12px'>{fct}</div></div></div>""",
                "این یک تصمیم واقعی Claude از لاگ‌هاست، نه نمونه‌ی ساختگی. Claude رژیم بازار را تشخیص داده، "
                "سهم هدف و ضریب ریسک را انتخاب کرده، و دلیلش را به فارسی ساده نوشته است؛ با استناد به اعداد مشخص "
                "مثل بازده و نوسان. همچنین یک پیش‌بینی چهار ساعته می‌دهد که بعداً با واقعیت مقایسه می‌شود "
                "و کارنامه‌اش در چرخه‌ی بعد به خود او برمی‌گردد. هر تصمیم حدود پنج تا یازده سنت هزینه دارد."))

    # 4 — idea 1: the AI that grades itself
    sg = d.get("sg") or {}
    s1 = sg.get("summary", {})
    r = lambda x: fa(round((x or 0) * 100))
    quotes = [("01:19", "دو بار آخر گفتم تتر بالا می‌رود و هر دو بار خنثی ماند؛ پس این بار خنثی می‌گویم."),
              ("10:18", "سه بار آخر برای بیت‌کوین گفتم خنثی و هر سه بار بالا رفت… پیش‌بینی نوسان تتر را هم به «عادی» تغییر دادم."),
              ("12:23", "پیش‌بینی‌های خنثیِ اخیر بیت‌کوین حرکت‌های صعودی را از دست دادند؛ پس این بار بالا می‌گویم.")]
    qhtml = "".join(f"<div class='quote' style='font-size:27px;margin-bottom:14px'>«{q}» <span class='small'>({fa(t)})</span></div>"
                    for t, q in quotes)
    out.append((f"""<h2>ایده‌ی ۱: هوش مصنوعی‌ای که به خودش نمره می‌دهد</h2>
<div class='grid g4'>
<div class='card'><div class='k'>پیش‌بینی ۴ ساعته‌ی ارزیابی‌شده</div><div class='v'>{fa(s1.get('forecasts_resolved', 0))}</div></div>
<div class='card'><div class='k'>جهت درست — Claude</div><div class='v acc'>{r(s1.get('direction_hit'))}٪</div></div>
<div class='card'><div class='k'>مبنای «مثل ۴ ساعت قبل»</div><div class='v'>{r(s1.get('naive_hit'))}٪</div></div>
<div class='card'><div class='k'>برنامه‌هایی که به کارنامه استناد کردند</div><div class='v ok'>{fa(s1.get('playbooks_citing_own_scorecard', 0))}</div></div></div>
<div class='card'><div class='k' style='margin-bottom:14px'>از لاگ خود Claude (ترجمه):</div>{qhtml}</div>
<div class='small'>چهار ساعت بعد از هر پیش‌بینی، کد بدون نگاه به آینده نمره می‌دهد و کارنامه به چرخه‌ی بعد Claude برمی‌گردد. صادقانه: «همیشه خنثی» {r(s1.get('always_flat_hit'))}٪ می‌گیرد چون بیشتر ساعت‌ها بازار آرام بود.</div>""",
                f"ایده‌ی اول: هوش مصنوعی‌ای که به خودش نمره می‌دهد. Claude در هر برنامه جهت چهار ساعت بعد را پیش‌بینی می‌کند. "
                f"چهار ساعت بعد، کد پیش‌بینی را با بازار واقعی و با یک مبنای ساده مقایسه می‌کند و کارنامه را به خود Claude برمی‌گرداند. "
                f"Claude از آن یاد می‌گیرد: در {fa(s1.get('playbooks_citing_own_scorecard', 0))} برنامه از {fa(s1.get('claude_playbooks', 0))} به کارنامه‌ی خودش استناد کرده؛ "
                f"مثلاً نوشته دو بار گفتم تتر بالا می‌رود و نرفت، پس این بار خنثی می‌گویم. تا اینجا جهت را {r(s1.get('direction_hit'))} درصد "
                f"درست گفته، در برابر {r(s1.get('naive_hit'))} درصد برای مبنای ساده. و صادقانه می‌گوییم که چون بازار بیشتر آرام بود، "
                f"حدس همیشه خنثی هم {r(s1.get('always_flat_hit'))} درصد می‌گیرد."))

    # 5 — idea 2: the control group
    ab = d.get("ab") or {}
    if ab:
        c, t = ab["claude"], ab["rules_twin"]
        vs = next((x for p_ in reversed(d["pbs"]) for x in p_.get("text_inputs_used", []) if x.lower().startswith("vs twin")), "")

        def row(name, x, cls=""):
            return (f"<tr><td class='{cls}'>{name}</td><td>{pct(x['return_pct'], 3)}</td>"
                    f"<td><b>{pct(x['after_selling_to_tmn_pct'], 3)}</b></td><td>{fa(x['max_drawdown_pct'])}٪</td>"
                    f"<td>{fa(x['fills'])}</td><td>{money(x['fees_tmn'])}</td></tr>")
        out.append((f"""<h2>ایده‌ی ۲: گروه کنترل — همان موتور، بدون Claude</h2>
<div class='card'><div class='k'>بازپخش منصفانه: همان ۲۴ ساعت واقعی والکس، همان شبیه‌ساز و همان کمربند؛ فقط مدیر فرق می‌کند (فقط BTCTMN)</div>
<table style='margin-top:10px'><tr><th>مدیر</th><th>بازده</th><th>بعد از فروش به تومان</th><th>بیشترین افت</th><th>معاملات</th><th>کارمزد (تومان)</th></tr>
{row('Claude', c, 'acc')}{row('دوقلوی قاعده‌محور', t)}<tr><td>ماندن در تومان</td><td>۰٪</td><td><b>۰٪</b></td><td>۰٪</td><td>۰</td><td>۰</td></tr></table></div>
<div class='grid g2'><div class='card'><div class='k'>نتیجه</div><div style='font-size:30px;line-height:1.7;margin-top:8px'>نتیجه‌ی نهایی مساوی، با <b class='ok'>حدود یک‌هفتم افت حساب</b> و <b class='ok'>یک‌هشتم کارمزد</b> — بیشتر چون Claude سهم کوچک‌تری انتخاب کرد.</div></div>
<div class='card'><div class='k'>Claude دوقلو را می‌بیند و باید توضیح دهد (از لاگ):</div><div style='font-size:20px;line-height:1.6;margin-top:8px;direction:ltr;text-align:left'>{html.escape(vs[:330])}</div></div></div>""",
                    "ایده‌ی دوم: گروه کنترل. کنار موتور اصلی، یک دوقلو با همان موتور و همان کمربند ایمنی کار می‌کند، فقط به‌جای Claude یک "
                    "قاعده‌ی ثابت تصمیم می‌گیرد. همان بیست و چهار ساعت واقعی والکس را دو بار از همان شبیه‌ساز گذراندیم: یک بار با برنامه‌های "
                    "ثبت‌شده‌ی Claude و یک بار با قاعده. بعد از فروش همه‌چیز به تومان، که مسابقه همین را می‌سنجد، نتیجه تقریباً مساوی است؛ ولی "
                    "افت حساب Claude حدود یک‌هفتم و کارمزدش یک‌هشتم دوقلوست؛ بیشتر به این دلیل که Claude سهم کوچک‌تری انتخاب کرد، یعنی همان نتیجه با ریسک کمتر. "
                    "از ظهر جمعه هم Claude برنامه‌ی دوقلو را می‌بیند و باید بنویسد "
                    "کجا با آن مخالف است و چرا؛ مثلاً اینکه یک جهش یک‌ساعته در جمعه‌ی کم‌حجم دلیل خوبی برای چهار برابر کردن موقعیت نیست."))

    # 6 — idea 3: the seatbelt
    dr = d.get("drill") or {}
    blocks = {}
    for r_ in d["all_dec"]:
        if r_["gate"]["verdict"] == "block":
            for rule in r_["gate"]["rules"]:
                blocks[rule] = blocks.get(rule, 0) + 1
    crash = dr.get("crash", {})
    bl = " · ".join(f"<span class='ltr'>{html.escape(k)}</span> {fa(v)}" for k, v in blocks.items()) or "—"
    out.append((f"""<h2>ایده‌ی ۳: کمربند ایمنی — کدی که هوش مصنوعی نمی‌تواند دورش بزند</h2>
<div class='grid g2'>
<div class='card'><div class='k'>آزمون سقوط روی دقیقه‌های واقعی والکس (۲۰٪ سهم بیت‌کوین)</div>
<table style='margin-top:10px'><tr><th>شوک</th><th>با کمربند</th><th>بدون کمربند</th></tr>
<tr><td>سقوط ۱۰٪ در ۱۵ دقیقه</td><td class='ok'><b>{pct(crash.get('with_seatbelt_return_pct', 0), 2)}</b></td><td class='bad'>{pct(crash.get('held_through_return_pct', 0), 2)}</td></tr>
<tr><td>یک قیمت خراب (−۳۰٪، یک دقیقه)</td><td class='ok'><b>بدون فروش</b></td><td>—</td></tr></table>
<div class='small' style='margin-top:12px'>این آزمون دو ایراد واقعی پیدا کرد؛ همان روز درست و در نسخه‌ی زنده‌ی <span class='ltr'>live-v2</span> منتشر شد.</div></div>
<div class='card'><table><tr><th>قانون</th><th>مقدار</th></tr>
<tr><td>سقف هر بازار / کل</td><td>۴۰٪ / ۶۰٪</td></tr><tr><td>حد ضرر هر موقعیت</td><td>−۲٫۵٪</td></tr>
<tr><td>زیان روزانه</td><td>−۱٪ → توقف خرید</td></tr><tr><td>افت کل −۳٪</td><td>فروش همه و قفل</td></tr>
<tr><td>رد واقعی در زنده/سایه</td><td>{bl}</td></tr></table></div></div>""",
                f"ایده‌ی سوم: کمربند ایمنی. Claude فقط پیشنهاد می‌دهد؛ هر سفارش از دروازه‌ی ریسک قطعی می‌گذرد: سقف حجم، حد ضرر، توقف روزانه "
                f"و فروش اضطراری. برای اینکه فقط ادعا نباشد، روی دقیقه‌های واقعی والکس یک سقوط ده درصدی بیت‌کوین تزریق کردیم: با کمربند، "
                f"حساب حدود {fa(abs(round(crash.get('with_seatbelt_return_pct', 0), 2)))} درصد افت کرد و بدون آن حدود "
                f"{fa(abs(round(crash.get('held_through_return_pct', 0), 2)))} درصد. یک قیمت خراب منفی سی درصدی هم هیچ فروش وحشت‌زده‌ای ایجاد نکرد. "
                f"این آزمون دو ایراد واقعی هم پیدا کرد که همان روز درست شد و در نسخه‌ی زنده منتشر شد."))

    # 5 — live account
    rows = ""
    for r in live_dec[-6:]:
        fills = [f for f in live_fills if f.get("decision_id") == r["decision_id"]]
        fl = "; ".join(f"{fa(f'{f['qty']:.6f}')} @ {money(f['price'])}" for f in fills) or "—"
        rows += (f"<tr><td>{tehran(r['ts'])}</td><td>{'خرید' if r['side'] == 'BUY' else 'فروش'}</td>"
                 f"<td class='ltr'>{html.escape(r['decided_by'])}</td><td class='ltr'>{html.escape(r['gate']['verdict'])}</td>"
                 f"<td>{fl}</td></tr>")
    can = d["canary"]
    can_txt = (f"سفارش آزمایشی {fa(f"{float(can.get('qty', 0)):.8f}")} BTC در {money(can.get('price', 0))} — هنگام باز بودن، "
               f"{money(float(can.get('tmn_while_resting', {}).get('locked', 0) or 0))} تومان در زیرحساب پراپ فریز شد؛ سپس لغو.") if can else ""
    out.append((f"""<h2>حساب واقعی والکس <span class='tag'>زنده از جمعه ۱ آبان، ۰۱:۲۰</span></h2>
<div class='grid g4'>
<div class='card'><div class='k'>ارزش حساب</div><div class='v'>{money(eq)}</div><div class='k'>تومان</div></div>
<div class='card'><div class='k'>بازده تا این لحظه</div><div class='v {'ok' if ret >= 0 else 'bad'}'>{pct(ret, 3)}</div></div>
<div class='card'><div class='k'>سهم بیت‌کوین</div><div class='v'>{fa(f'{btc_exp:.1f}')}٪</div></div>
<div class='card'><div class='k'>معاملات پرشده</div><div class='v'>{fa(len(live_fills))}</div></div></div>
<div class='card'><div class='k'>آخرین تصمیم‌های زنده</div><table><tr><th>ساعت</th><th>سمت</th><th>تصمیم‌گیرنده</th><th>دروازه</th><th>پر شد (مقدار @ قیمت)</th></tr>
{rows or "<tr><td colspan=5>هنوز تصمیمی ثبت نشده</td></tr>"}</table></div>
<div class='small'>{can_txt}</div>""",
                f"این حساب واقعی والکس است. معامله‌ی زنده از بامداد جمعه شروع شد. قبل از شروع، یک سفارش آزمایشی "
                f"که پر نمی‌شد ثبت کردیم تا مطمئن شویم کلید دقیقاً روی زیرحساب پراپ معامله می‌کند؛ مبلغ همان سفارش "
                f"در زیرحساب فریز شد و بعد لغو کردیم. حساب پراپ فقط بازار بیت‌کوین به تومان را می‌پذیرد، پس تتر را "
                f"فقط برای تحلیل نگه داشتیم. در لحظه‌ی ساخت این ویدئو ارزش حساب {money(eq)} تومان است و {fa(len(live_fills))} "
                f"معامله پر شده. هر سطر این جدول در لاگ تصمیم‌ها، دلیل فارسی کامل خودش را دارد."))

    # 7 — backtest
    bt = d["bt"]
    if bt:
        rows = "".join(f"<tr><td class='ltr'>{html.escape(r['start'][5:16].replace('T', ' '))}</td><td>{pct(r['return_pct'], 2)}</td>"
                       f"<td>{pct(-r['max_drawdown_pct'], 2)}</td><td>{fa(r['fills'])}</td><td>{pct(r['hold_btc_pct'], 1)}</td></tr>"
                       for r in bt["results"][::3][:9])
        body = (f"<div class='grid g4'><div class='card'><div class='k'>پنجره‌ی ۳ روزه</div><div class='v'>{fa(bt['windows'])}</div></div>"
                f"<div class='card'><div class='k'>میانه‌ی بازده</div><div class='v'>{pct(bt['median_return_pct'], 2)}</div></div>"
                f"<div class='card'><div class='k'>بدترین افت</div><div class='v'>{pct(-bt['worst_drawdown_pct'], 2)}</div></div>"
                f"<div class='card'><div class='k'>پایان تمام‌تومان</div><div class='v ok'>{'بله' if bt['all_ended_in_tmn'] else 'نه'}</div></div></div>"
                f"<div class='card'><table><tr><th>شروع</th><th>بازده</th><th>بیشترین افت</th><th>معاملات</th><th>نگه‌داری بیت‌کوین</th></tr>{rows}</table></div>")
        say = (f"بک‌تست با همان موتور واقعی انجام شده: همان دروازه‌ی ریسک، همان اجرا و همان فروش پایانی، روی داده‌ی یک دقیقه‌ای واقعی "
               f"والکس. {fa(bt['windows'])} پنجره‌ی سه روزه، درست به شکل مسابقه، با مدیر قاعده‌محور. میانه‌ی بازده "
               f"{pct(bt['median_return_pct'], 2)} و بدترین افت {pct(-bt['worst_drawdown_pct'], 2)} بود و همه‌ی پنجره‌ها "
               f"تمام‌تومان تمام شدند. هدف ما سود کوچک با افت کم است، نه قمار.")
    else:
        body, say = "<div class='card'>بک‌تست در حال اجراست.</div>", "بک‌تست با همان موتور روی داده‌ی واقعی والکس اجرا می‌شود."
    out.append(("<h2>بک‌تست: همان موتور، روی ۳۰ روز داده‌ی واقعی والکس</h2>" + body, say))

    # 9 — the end
    out.append(("""<h2>پایان مسابقه و ایمنی</h2>
<div class='grid g4'>
<div class='card'><div class='k'>جمعه ۲۱:۲۰</div><div class='v' style='font-size:34px'>خرید متوقف · فروش با لیمیت</div></div>
<div class='card'><div class='k'>۲۲:۲۰</div><div class='v' style='font-size:34px'>فروش تهاجمی‌تر</div></div>
<div class='card'><div class='k'>۲۲:۵۰</div><div class='v' style='font-size:34px'>فروش محافظت‌شده</div></div>
<div class='card'><div class='k'>۲۳:۲۰ تا ۲۳:۵۰</div><div class='v' style='font-size:34px'>تطبیق و تأیید تمام‌تومان</div></div></div>
<div class='grid g3'><div class='card small'>هشدار پیام‌رسان بله برای هر رویداد مهم؛ واچ‌داگ مستقل هر دقیقه ضربان موتور و تازگی برنامه‌ی Claude را چک می‌کند.</div>
<div class='card small'>قفل سود: اگر بعد از هزینه‌ی فروش در سود باشیم، همه‌چیز فروخته و خرید بسته می‌شود. نظارت انسانی فقط ترمز است، نه معامله.</div>
<div class='card small'>بازسازی همه‌ی شواهد بدون کلید: <span class='ltr'>uv run python -m judge.demo</span> · <span class='ltr'>github.com/MetaParsa/kestrel</span></div></div>""",
                "و در پایان مسابقه، سیستم خودش همه‌چیز را به تومان برمی‌گرداند: در روز آخر خرید تا ساعت نه و بیست دقیقه‌ی شب باز بود، "
                "بعد فروش با سفارش‌های لیمیت شروع شد، سپس تهاجمی‌تر و در ساعت آخر سریع، و از یازده و بیست دقیقه موجودی با والکس تطبیق داده می‌شود. "
                "یک قفل سود هم داریم: اگر بعد از هزینه‌ی فروش در سود باشیم، همه‌چیز فروخته و خرید بسته می‌شود. مدل‌هایی مثل Kronos را هم آزمودیم "
                "و چون از مبنای ساده بدتر بودند کنار گذاشتیم. هشدارهای بله و یک واچ‌داگ مستقل مراقب‌اند، و هر داوری می‌تواند با یک دستور و بدون کلید "
                "همه‌ی این عددها را دوباره بسازد. کد، لاگ تصمیم‌ها، بک‌تست و تست سایه در گیت‌هاب است. ممنون."))
    return out


# ---- render -------------------------------------------------------------------------------------
def run_retry(cmd: list[str], tries: int = 3, **kw) -> subprocess.CompletedProcess:
    """Windows sometimes holds a just-written file (antivirus); retry a failed step a couple of times."""
    for i in range(tries):
        try:
            return subprocess.run(cmd, check=True, **kw)
        except subprocess.CalledProcessError:
            if i == tries - 1:
                raise
            time.sleep(2)


def render(items: list[tuple[str, str]]) -> Path:
    work = OUT / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, (body, say) in enumerate(items, 1):
        h, png, mp3, mp4 = (work / f"s{i:02d}.{x}" for x in ("html", "png", "mp3", "mp4"))
        h.write_text(page(body, i, len(items)), encoding="utf-8")
        edge_shot([EDGE, "--headless=new", f"--user-data-dir={Path.home() / 'AppData' / 'Local' / 'Temp' / 'edge-headless-render'}", "--disable-gpu", "--hide-scrollbars", "--window-size=1920,1080",
                        "--virtual-time-budget=4000", f"--screenshot={png}", h.resolve().as_uri()],
                       capture_output=True, timeout=120, check=True)
        subprocess.run([str(TTS), "--voice", VOICE, "--rate=+4%", "--text", say, "--write-media", str(mp3)],
                       capture_output=True, timeout=180, check=True)
        dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp3)],
                                   capture_output=True, text=True, check=True).stdout.strip()) + 0.8
        run_retry([FFMPEG, "-y", "-loop", "1", "-i", str(png), "-i", str(mp3), "-t", f"{dur:.2f}",
                        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "25",
                        "-c:a", "aac", "-b:a", "128k", "-af", "apad", str(mp4)], capture_output=True)
        parts.append(mp4)
        print(f"scene {i}: {dur:.1f}s", flush=True)
    script = ["# متن گوینده‌ی ویدئوی دمو (هر صحنه یک بند)", "",
              "ویدئوی آماده: `run/video/wallex-demo.mp4` (صدای ماشینی فارسی). اگر وقت دارید، تصاویر `run/video/work/s*.png` "
              "را به ترتیب نشان دهید و همین متن را با صدای خودتان بخوانید؛ صدای انسان طبیعی‌تر است.", ""]
    script += [f"## صحنه‌ی {fa(i)}\n\n{say}\n" for i, (_, say) in enumerate(items, 1)]
    (ROOT / "delivery").mkdir(exist_ok=True)
    (ROOT / "delivery" / "VIDEO-SCRIPT-fa.md").write_text("\n".join(script), encoding="utf-8")
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    final = OUT / "wallex-demo.mp4"
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(final)],
                   capture_output=True, check=True)
    total = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
                                 capture_output=True, text=True, check=True).stdout.strip())
    print(f"video: {final} ({total / 60:.1f} min)")
    return final


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-pull", action="store_true")
    a = ap.parse_args(argv)
    if not a.no_pull:
        pull()
    render(scenes(load()))


if __name__ == "__main__":
    main()
