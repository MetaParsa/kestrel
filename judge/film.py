"""A film of the agent: the real engine on real Wallex minutes, two scripted days, as a page you step through.

    uv run python -m judge.film            # builds judge_out/film/index.html (~30 s) and checks every act
    uv run python -m judge.film --open     # ...and opens it in the browser (space / → plays the next act)

Day 1, a normal day: Claude's real 17:35 playbook → maker-first buy; a "rogue" plan asking for 90% → clamped
on the server and stepped by the gate; a −30% bad print → no panic sale; a human /risk command over Bale →
the engine sells down; the end-of-contest sell-off → everything back in TMN.
Day 2, a crash: BTC −10% in 15 minutes → the stop sells; the sharp move calls Claude early → Claude's real
19:29 risk_off decision; a later buy wish → refused by the 12 h cooldown after a stop.

Claude's playbooks are real (judge/film_playbooks.jsonl, recorded live on 2026-10-23) and replayed on older
minutes, so no API call is made. The market is data/sample (committed Wallex candles); nothing touches the
network. Every act is checked; a failed check exits non-zero.
"""
from __future__ import annotations

import os

os.environ.setdefault("WALLEX_FLATTEN_H", "2.5,1.5,1.0")   # the schedule live trading used on the last evening
os.environ.pop("WALLEX_PROFIT_LOCK", None)

import argparse   # noqa: E402
import json   # noqa: E402
import shutil   # noqa: E402
import subprocess   # noqa: E402
import sys   # noqa: E402
import webbrowser   # noqa: E402
from dataclasses import replace   # noqa: E402
from datetime import datetime   # noqa: E402
from pathlib import Path   # noqa: E402

import config   # noqa: E402
from engine.loop import EngineCfg   # noqa: E402
from engine.modes import BacktestBroker   # noqa: E402
from judge.demo import use_sample_data   # noqa: E402
from manager.schema import PlaybookOut, finalize, server_validate   # noqa: E402
from oversight import state as ov   # noqa: E402
from oversight.engine import OverseenEngine   # noqa: E402
from reports.backtest import Quiet, Replay   # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "judge_out" / "film"
RECORDED = {r["playbook_id"][12:16]: r for r in map(json.loads, (REPO / "judge" / "film_playbooks.jsonl")
                                                    .read_text(encoding="utf-8").splitlines())}
PHASE_FA = {"normal": "عادی", "no_new": "بدون خرید تازه", "aggressive": "فروش فعال", "market": "فروش سریع",
            "verify": "تطبیق با والکس", "ended": "پایان — همه تومان"}
REGIME_FA = {"devaluation_drift": "افت ارزش تومان", "trend_up": "روند صعودی", "range": "رنج (بی‌جهت)",
             "risk_off": "ریسک‌گریز", "event_shock": "شوک"}


class Recorder(Quiet):
    """Bale messages the engine would have sent, with the simulated time."""
    def __init__(self, clock):
        super().__init__()
        self.clock, self.items = clock, []

    def send(self, text, *, key=None, force=False):
        self.items.append({"t": self.clock(), "text": text})
        return False


def recorded_out(hhmm: str) -> PlaybookOut:
    r = RECORDED[hhmm]
    return PlaybookOut.model_validate({
        "regime": r["regime"], "core_targets": r["core_targets"], "tactical_enabled": r["tactical_enabled"],
        "risk_multiplier": r["risk_multiplier"], "valid_minutes": 90, "forecast_4h": r["forecast_4h"],
        "text_inputs_used": r["text_inputs_used"], "signals_cited": r["signals_cited"], "rationale_fa": r["rationale_fa"]})


def plan_out(regime: str, btc: float, rationale: str, usdt: float = 0.0, tactical: bool = False) -> PlaybookOut:
    return PlaybookOut.model_validate({
        "regime": regime, "core_targets": {"USDTTMN": usdt, "BTCTMN": btc},
        "tactical_enabled": {"USDTTMN": tactical, "BTCTMN": tactical}, "risk_multiplier": 1.0, "valid_minutes": 90,
        "forecast_4h": {m: {"direction": "up", "vol": "normal"} for m in config.MARKETS},
        "text_inputs_used": [], "signals_cited": [], "rationale_fa": rationale})


class Day:
    def __init__(self, key: str, start: datetime, hours: float, end_in_h: float, shock=None):
        self.key, self.start, self.hours = key, start.timestamp(), hours
        self.rp = Replay(list(config.MARKETS))
        if shock:
            shock(self.rp, self.start)
        run_dir = OUT / "run" / key
        shutil.rmtree(run_dir, ignore_errors=True)
        self.broker = BacktestBroker()
        self.rp.now = self.start
        self.alerts = Recorder(lambda: self.rp.now)
        cfg = EngineCfg(run_dir=run_dir, mode="backtest", manager="remote", sha=sha(),
                        end_at=datetime.fromtimestamp(self.start + end_in_h * 3600, tz=config.TEHRAN),
                        loop=replace(config.LOOP, manager_every_s=12 * 3600))   # the film answers only the requests it shows
        self.eng = OverseenEngine(cfg, broker=self.broker, market=self.rp.snapshots, bars=self.rp.bars,
                                  clock=lambda: self.rp.now, alerter=self.alerts)
        self.notes: list[dict] = []
        self.frames: list[dict] = []

    # ---- what the script can do ----------------------------------------------------------------
    def note(self, text: str, kind: str = "note", **extra) -> None:
        self.notes.append({"t": self.rp.now, "kind": kind, "text": text, **extra})

    def playbook(self, out: PlaybookOut, source: str, model: str, who_fa: str, cost: float = 0.0) -> None:
        now = self.rp.now
        pb = finalize(out, bundle_hash="film-" + self.key, now=now, source=source, model=model, cost_usd=cost)
        pb, extra = server_validate(pb, accepted_at=now)
        pb = pb.model_copy(update={"valid_until": now + 12 * 3600})   # stands until the next plan in this film
        self.note(f"{who_fa}: حالت «{REGIME_FA[pb.regime]}»، هدف بیت‌کوین {pb.core_targets['BTCTMN']:.0%} × ضریب ریسک "
                  f"{pb.risk_multiplier:g}", "plan", rationale=pb.rationale_fa, clamps=pb.clamps + extra,
                  vs_twin=next((x for x in pb.text_inputs_used if x.startswith("vs twin")), None),
                  forecast={m: f"{f.direction}/{f.vol}" for m, f in pb.forecast_4h.items()}, cost=cost)
        self.eng._activate(pb, now)

    def human(self, text: str) -> None:
        path = ov.state_path(self.eng.cfg.run_dir)
        st = ov.apply(ov.load(path), ov.parse(text), now=self.rp.now, who={"name": "مالک ربات", "via": "bale"})
        ov.save(path, st)
        self.note(f"پیام انسان در بله: «{text}» → {st.summary_fa()}", "human")

    # ---- the clock ------------------------------------------------------------------------------
    def run(self, script: dict[int, list]) -> "Day":
        rp, eng = self.rp, self.eng
        for minute in range(int(self.hours * 60) + 1):
            t = self.start + minute * 60
            rp.now = t
            for m in config.MARKETS:
                r = rp.row(m)
                self.broker.feed_bar(m, t, float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
            for action in script.get(minute, []):
                action(self)
            eng.tick()
            bids = eng.bids
            o = eng.oversight
            light = ("red" if eng.level == 3 or eng.pf.halted else
                     "yellow" if eng.level == 2 or not o.ai_in_control else "green")
            self.frames.append({"m": minute, "t": t, "btc": round(float(rp.row("BTCTMN").c)),
                                "equity": round(eng.pf.equity(bids)), "gain": round(eng.pf.gain(bids), 5),
                                "btc_share": round(eng.pf.exposure("BTCTMN", bids), 4), "light": light,
                                "phase": eng._phase(t), "level": eng.level,
                                "regime": eng.playbook.regime if eng.playbook else None,
                                "dd": round(eng.pf.max_drawdown, 5)})
        return self

    # ---- what happened, in Persian, in time order -----------------------------------------------
    def feed(self) -> list[dict]:
        logs = lambda k: [json.loads(x) for x in (self.eng.cfg.run_dir / "logs" / f"{k}.jsonl").read_text(
            encoding="utf-8").splitlines() if x.strip()] if (self.eng.cfg.run_dir / "logs" / f"{k}.jsonl").exists() else []
        items = list(self.notes)
        for e in logs("events"):
            ev, text = e["event"], None
            if ev == "manager_request" and "reissued" not in e["reason"]:   # unanswered requests renew every 20 min
                text = f"موتور از Claude برنامه‌ی تازه خواست (دلیل: {e['reason']})"
            elif ev == "flatten_phase":
                text = f"مرحله‌ی پایانی: {PHASE_FA.get(e['phase'], e['phase'])}"
            elif ev == "degrade_level" and e.get("level") in (2, 3):
                text = f"سطح احتیاط {e['level']}: " + ("برنامه‌ای نیست؛ خرید ممنوع" if e["level"] == 3 else "فقط نگه‌داری")
            elif ev == "buys_cancelled":
                text = f"سفارش‌های خرید لغو شدند ({e.get('count')} سفارش)"
            elif ev == "order_placed":
                side = "خرید" if e["side"] == "BUY" else "فروش"
                text = f"سفارش {side} ثبت شد: لیمیت {e['price']:,.0f}، فوریت {e.get('urgency', '')}"
            elif ev == "oversight_applied":
                text = "دستور انسانی در موتور اعمال شد (ثبت شد چه کسی، کی و چرا)"
            if text:
                items.append({"t": e["ts"], "kind": "engine", "text": text})
        for d in logs("decisions"):
            if "G_BELOW_EXCHANGE_MIN" in d["gate"]["rules"]:
                continue                                  # a few toman of dust left after a sale: not news
            items.append({"t": d["ts"], "kind": "block" if d["gate"]["verdict"] == "block" else "decision",
                          "text": d["reason_fa"], "rules": d["gate"]["rules"]})
        for f in logs("fills"):
            side = "خرید" if f["side"] == "BUY" else "فروش"
            items.append({"t": f["ts"], "kind": "fill", "side": f["side"],
                          "text": f"پر شد: {side} {f['qty']:.8f} بیت‌کوین به {f['price']:,.0f} تومان "
                                  f"(≈ {f['qty'] * f['price']:,.0f} تومان)"})
        for a in self.alerts.items:
            items.append({"t": a["t"], "kind": "bale", "text": "پیام بله: " + a["text"]})
        order = {"note": 0, "human": 0, "plan": 1, "engine": 2, "decision": 3, "block": 3, "fill": 4, "bale": 5}
        items.sort(key=lambda x: (x["t"], order.get(x["kind"], 9)))
        for x in items:
            x["m"] = round((x["t"] - self.start) / 60, 1)
        return items


def sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, timeout=10).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


# ---- day 1: a normal day ---------------------------------------------------------------------------
def glitch(rp: Replay, start: float) -> None:
    at = start + 90 * 60
    g = rp.g["BTCTMN"].copy()
    ts = rp.ts["BTCTMN"]
    for col in ("o", "h", "l", "c"):
        vals = g[col].to_numpy().copy()
        vals[(ts >= at) & (ts < at + 60)] *= 0.70
        g[col] = vals
    rp.g["BTCTMN"] = g


def crash(rp: Replay, start: float) -> None:
    at = start + 60 * 60
    g = rp.g["BTCTMN"].copy()
    ts = rp.ts["BTCTMN"]
    for col in ("o", "h", "l", "c"):
        vals = g[col].to_numpy().copy()
        k = ((ts - at) / (15 * 60)).clip(0, 1)
        vals *= 1 - 0.10 * k
        g[col] = vals
    rp.g["BTCTMN"] = g


ACTS = {
    "day1": [
        (0, "شروع: موتور بدون برنامه", "موتور روشن می‌شود و هنوز برنامه‌ای از Claude ندارد؛ پس چراغ قرمز است و هیچ خریدی نمی‌کند. "
            "اولین کارش این است که وضعیت بازار را بسته‌بندی کند و از Claude برنامه بخواهد."),
        (2, "۱. Claude تصمیم می‌گیرد", "این تصمیم واقعی Claude از ساعت ۱۷:۳۵ امروز است: حالت بازار، سهم هدف، ضریب ریسک، "
            "پیش‌بینی ۴ ساعته، دلیل فارسی و مقایسه با دوقلوی قاعده‌محور. موتور سفارش لیمیت با اولویت میکر می‌گذارد تا کارمزد کمتر شود."),
        (40, "۲. اگر هوش مصنوعی زیاده‌روی کند", "یک برنامه‌ی آزمایشی ۹۰٪ بیت‌کوین و ۵۰٪ تتر می‌خواهد. سرور سقف حالت بازار را دوباره "
             "اعمال می‌کند (۹۰٪ ← ۳۰٪، تتر ← ۰ چون در حساب پراپ مجاز نیست) و دروازه‌ی ریسک هر بار فقط ۱۰٪ اضافه می‌کند."),
        (90, "۳. قیمت خراب", "یک دقیقه قیمت بیت‌کوین ۳۰٪ پایین ثبت می‌شود. موتور برای حد ضرر از بالاترین دو قیمت آخر استفاده می‌کند، "
             "پس با یک داده‌ی خراب هیچ فروش وحشت‌زده‌ای انجام نمی‌شود."),
        (120, "۴. انسان در حلقه", "مالک ربات در بله می‌نویسد ‎/risk 0.25‎. انسان فقط می‌تواند ریسک را کم کند، نه سفارش دستی بدهد "
              "(قانون والکس). موتور خودش سهم را کم می‌کند و دستور با نام، زمان و دلیل ثبت می‌شود."),
        (210, "۵. پایان مسابقه: همه‌چیز به تومان", "از ۲.۵ ساعت مانده به پایان، خرید تازه ممنوع است. فروش با لیمیت شروع می‌شود، "
              "بعد تهاجمی‌تر و در آخر سریع؛ نیم ساعت آخر موجودی با والکس تطبیق داده می‌شود. نتیجه: همه‌چیز تومان."),
    ],
    "day2": [
        (0, "روز دوم: سقوط", "دوقلوی قاعده‌محور (همان موتور، بدون هوش مصنوعی) می‌گوید روند صعودی است و ۱۰٪ بیت‌کوین می‌خواهد. موتور با سفارش لیمیت می‌خرد."),
        (60, "۶. بیت‌کوین ۱۰٪ سقوط می‌کند", "در ۱۵ دقیقه بیت‌کوین ۱۰٪ می‌ریزد. حد ضرر ۲.۵٪ از قیمت خرید فعال می‌شود و موتور "
             "بدون منتظر ماندن برای هوش مصنوعی می‌فروشد. حرکت تند هم باعث می‌شود موتور زودتر از موعد از Claude نظر بخواهد."),
        (80, "۷. Claude: ریسک‌گریز", "این هم تصمیم واقعی Claude از ساعت ۱۹:۲۹ امروز است که بازار را ریسک‌گریز دید. هدف صفر است."),
        (120, "۸. بعد از ضرر، عجله نکن", "یک ساعت بعد، برنامه دوباره خرید می‌خواهد. دروازه‌ی ریسک تا ۱۲ ساعت بعد از حد ضرر اجازه‌ی "
              "خرید دوباره در همان بازار را نمی‌دهد (G_CORE_COOLDOWN). انتقام‌گیری از بازار ممنوع."),
    ],
}


def build() -> dict:
    use_sample_data()
    d1 = Day("day1", datetime(2026, 10, 21, 10, 0, tzinfo=config.TEHRAN), hours=6.1, end_in_h=6.0, shock=glitch)
    r = RECORDED["1735"]
    d1.run({
        0: [lambda d: d.note("موتور روشن شد؛ هنوز برنامه‌ای نیست", "note")],
        2: [lambda d: d.playbook(recorded_out("1735"), "claude", r["model"], "Claude Opus 5.5 (تصمیم واقعی ۱۷:۳۵)",
                                 r.get("cost_usd") or 0.0)],
        40: [lambda d: d.playbook(plan_out("trend_up", 0.90, "آزمایش: برنامه‌ای که بیش از حد ریسک می‌خواهد", usdt=0.5,
                                           tactical=True), "mock", "rogue-test", "برنامه‌ی آزمایشیِ پرریسک")],
        90: [lambda d: d.note("داده‌ی خراب: قیمت بیت‌کوین برای یک دقیقه ۳۰٪ پایین ثبت شد", "shock")],
        120: [lambda d: d.human("/risk 0.25 بازار امشب شلوغ است")],
    })
    d2 = Day("day2", datetime(2026, 10, 15, 10, 0, tzinfo=config.TEHRAN), hours=2.5, end_in_h=72, shock=crash)
    r2 = RECORDED["1929"]
    d2.run({
        0: [lambda d: d.playbook(plan_out("trend_up", 0.10, "دوقلوی قاعده‌محور: بازده ۲۴ ساعته مثبت ← روند صعودی، ۱۰٪"),
                                 "mock", "rules-v1", "دوقلوی قاعده‌محور")],
        60: [lambda d: d.note("شوک: بیت‌کوین در ۱۵ دقیقه ۱۰٪ سقوط می‌کند", "shock")],
        80: [lambda d: d.playbook(recorded_out("1929"), "claude", r2["model"], "Claude Opus 5.5 (تصمیم واقعی ۱۹:۲۹)",
                                  r2.get("cost_usd") or 0.0)],
        120: [lambda d: d.playbook(plan_out("trend_up", 0.10, "دوقلوی قاعده‌محور: دوباره روند صعودی، ۱۰٪"),
                                   "mock", "rules-v1", "دوقلوی قاعده‌محور")],
    })
    return {"day1": d1, "day2": d2}


# ---- checks: the film must show what it says ---------------------------------------------------------
def check(days: dict) -> list[str]:
    fails = []
    for key, day in days.items():
        feed = day.feed()
        within = lambda a, b: [x for x in feed if a <= x["m"] < b]
        if key == "day1":
            if not any(x["kind"] == "fill" and x["side"] == "BUY" for x in within(2, 40)):
                fails.append("day1: Claude's plan produced no buy fill")
            plan = next(x for x in feed if x["kind"] == "plan" and x["m"] >= 40)
            if not any("BTCTMN 0.9→0.300" in c for c in plan["clamps"]):
                fails.append(f"day1: rogue plan not clamped: {plan['clamps']}")
            if day.frames[60]["btc_share"] > 0.165:
                fails.append(f"day1: the gate let the rogue plan past 16% ({day.frames[60]['btc_share']:.1%})")
            if any(x["kind"] == "fill" and x["side"] == "SELL" for x in within(90, 95)):
                fails.append("day1: the bad print caused a sale")
            if not any(x["kind"] == "fill" and x["side"] == "SELL" for x in within(120, 200)):
                fails.append("day1: the human /risk command did not reduce the position")
            last = day.frames[-1]
            if last["phase"] != "ended" or last["btc_share"] * last["equity"] >= config.LIMITS.exchange_min_notional_tmn:
                fails.append(f"day1: did not end all in TMN ({last['phase']}, BTC {last['btc_share']:.2%})")
        else:
            if not any(x["kind"] == "fill" and x["side"] == "SELL" for x in within(60, 80)):
                fails.append("day2: the stop did not sell during the crash")
            if not any("event" in x["text"] for x in within(60, 80) if x["kind"] == "engine"):
                fails.append("day2: the crash did not call Claude early")
            if not any("G_CORE_COOLDOWN" in x.get("rules", []) for x in within(120, 151)):
                fails.append("day2: the cooldown did not block the re-buy")
    return fails


def write_page(days: dict) -> Path:
    data = {"built": datetime.now(config.TEHRAN).strftime("%Y-%m-%d %H:%M"), "sha": sha(), "days": [
        {"key": k, "start": d.start, "date": datetime.fromtimestamp(d.start, tz=config.TEHRAN).strftime("%Y-%m-%d"),
         "frames": d.frames, "feed": d.feed(),
         "acts": [{"m": m, "title": t, "say": s} for m, t, s in ACTS[k]]} for k, d in days.items()]}
    OUT.mkdir(parents=True, exist_ok=True)
    tpl = (REPO / "judge" / "film.html").read_text(encoding="utf-8")
    page = OUT / "index.html"
    page.write_text(tpl.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    (OUT / "film.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return page


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--open", action="store_true", help="open the page when done")
    a = ap.parse_args(argv)
    days = build()
    page = write_page(days)
    fails = check(days)
    for k, d in days.items():
        f = d.frames[-1]
        print(f"{k}: {len(d.frames)} minutes, {sum(1 for x in d.feed() if x['kind'] == 'fill')} fills, "
              f"final equity {f['equity']:,} TMN ({f['gain']:+.3%}), worst drawdown {f['dd']:.2%}, phase {f['phase']}")
    print("checks:", "all passed" if not fails else "\n  " + "\n  ".join(fails))
    print("page:", page)
    if a.open:
        webbrowser.open(page.as_uri())
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
