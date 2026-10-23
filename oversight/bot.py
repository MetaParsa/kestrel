"""The human command channel: a Bale bot that writes the oversight state file (see oversight/state.py).

    uv run python -m oversight.bot --run-dir run/main                  # long-polls Bale (BALE_TOKEN, BALE_CHAT_ID)
    uv run python -m oversight.bot --run-dir run/main --local "/pause spread is odd"    # same, from a terminal
    uv run python -m oversight.bot --run-dir run/main --local "/flatten news" --yes

Who may command: only the chat in BALE_CHAT_ID, and if BALE_ADMIN_IDS is set only those user ids.
Anything else is ignored (and logged without its text). Messages older than 10 minutes are not acted on,
the first start skips whatever was waiting, and /flatten and /resume need a second message
(/confirm <code>, valid 2 minutes). Every message that reaches a command is logged with who / when / why
in <run_dir>/logs/oversight.jsonl. Once a day the bot sends a digest and asks for a /review note.
The token is only ever part of the request URL; it is never logged.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx

import config
from oversight import state as ov

API = "https://tapi.bale.ai/bot{token}/{method}"
MAX_AGE_S = 600                 # older commands are not acted on (e.g. queued while the bot was down)
CONFIRM_S = 120
REVIEW_HOUR = 21                # Tehran time of the daily digest

HELP_FA = ("دستورهای نظارت انسانی (فقط کم کردن ریسک؛ هیچ سفارشی دستی ثبت نمی‌شود):\n"
           "/status — وضعیت\n"
           "/pause دلیل — توقف خرید جدید (فروش‌ها و حد ضررها کار می‌کنند)\n"
           "/risk 0.5 دلیل — سقف ضریب ریسک Claude (بین ۰ و ۱)\n"
           "/flatten دلیل — شروع فروش پایانی همین حالا (نیاز به تأیید)\n"
           "/resume دلیل — برگرداندن کنترل به Claude و دروازه‌ی ریسک (نیاز به تأیید)\n"
           "/review یادداشت — یادداشت بازبینی روزانه")


def _fa_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=config.TEHRAN).strftime("%m-%d %H:%M")


class Controller:
    """Turns one message from one person into a state change, a log line and a reply. No network."""

    def __init__(self, run_dir: Path, *, clock: Callable[[], float] = time.time, sha: str = "oversight"):
        self.run_dir, self.clock, self.sha = run_dir, clock, sha
        self.path = ov.state_path(run_dir)
        self.pending: dict[str, tuple[ov.Command, dict, float]] = {}

    def _log(self, **rec) -> None:
        ov.log(self.run_dir, {"ts": self.clock(), **rec}, sha=self.sha)

    def handle(self, text: str, who: dict, *, sent_at: float | None = None, confirmed: bool = False) -> str | None:
        now = self.clock()
        try:
            cmd = ov.parse(text)
        except ov.CommandError as e:
            self._log(event="rejected", text=text[:200], who=who, error=str(e))
            return str(e)
        if cmd is None:
            return None                                   # chat, not a command
        if sent_at is not None and now - sent_at > MAX_AGE_S:
            self._log(event="stale", command=cmd.name, who=who, sent_at=sent_at)
            return f"پیام /{cmd.name} قدیمی است ({int((now - sent_at) / 60)} دقیقه)؛ اجرا نشد. اگر هنوز لازم است دوباره بفرستید."
        if cmd.name == "help":
            return HELP_FA
        if cmd.name == "status":
            return self.status_fa()
        if cmd.name == "review":
            if not cmd.reason:
                return "یادداشت را بعد از دستور بنویسید: /review همه‌چیز عادی است"
            self._log(event="review", note=cmd.reason[:2000], who=who)
            return "✅ یادداشت بازبینی ثبت شد."
        if cmd.name == "confirm":
            item = self.pending.get(cmd.code)
            if item is None or now > item[2]:
                self.pending.pop(cmd.code, None)
                self._log(event="rejected", command="confirm", who=who, error="unknown or expired code")
                return "کد تأیید نامعتبر یا منقضی است."
            if item[1].get("id") != who.get("id"):
                return "فقط همان کسی که دستور را داده می‌تواند تأیید کند."
            reply = self._apply(item[0], who, now)
            if reply.startswith("✅"):
                self.pending.pop(cmd.code, None)              # a failed save keeps the code for a retry
            return reply
        if cmd.name in ov.NEEDS_CONFIRM and not confirmed:
            self.pending = {k: v for k, v in self.pending.items() if v[2] >= now}
            code = f"{secrets.randbelow(10_000):04d}"
            while code in self.pending:                       # two pending commands never share a code
                code = f"{secrets.randbelow(10_000):04d}"
            self.pending[code] = (cmd, who, now + CONFIRM_S)
            self._log(event="needs_confirm", command=cmd.name, reason=cmd.reason, who=who)
            what = "همه‌ی دارایی‌ها از مسیر عادی فروش پایانی به تومان فروخته شود" if cmd.name == "flatten" else \
                "محدودیت‌های انسانی برداشته شود و کنترل به Claude و دروازه‌ی ریسک برگردد"
            return f"برای اینکه {what}، تا ۲ دقیقه بفرستید: /confirm {code}"
        return self._apply(cmd, who, now)

    def _apply(self, cmd: ov.Command, who: dict, now: float) -> str:
        note = ""
        try:
            with ov.locked(self.path):
                try:
                    before = ov.load_or_backup(self.path) if self.path.exists() else ov.OversightState()
                except (OSError, ValueError, KeyError, TypeError):
                    if cmd.name not in ov.LOWERS_RISK:
                        self._log(event="rejected", command=cmd.name, who=who, error="state file unreadable")
                        return "فایل وضعیت نظارت خراب است؛ /resume اجرا نشد. اول /pause بفرستید."
                    # brakes still work: start from the careful side (paused), never from "no limits"
                    before, note = ov.OversightState(paused=True), "\n⚠️ فایل وضعیت قبلی خراب بود؛ خرید هم متوقف شد."
                after = ov.apply(before, cmd, now=now, who=who)
                ov.save(self.path, after)
        except (OSError, TimeoutError) as e:
            self._log(event="failed", command=cmd.name, who=who, error=type(e).__name__)
            return f"❌ /{cmd.name} ثبت نشد ({type(e).__name__})؛ دوباره بفرستید."
        self._log(event="applied", command=cmd.name, value=cmd.value, reason=cmd.reason or "(بدون دلیل)", who=who,
                  seq=after.seq, before=before.summary_fa(), after=after.summary_fa())
        return (f"✅ ثبت شد (#{after.seq}). وضعیت نظارت: {after.summary_fa()}\nموتور در تیک بعدی (≤ ۱۰ ثانیه) اعمال می‌کند."
                + note)

    def status_fa(self) -> str:
        try:
            st = ov.load_or_backup(self.path) if self.path.exists() else ov.OversightState()
        except (OSError, ValueError, KeyError, TypeError):
            st = None
        lines = [f"👤 نظارت: {st.summary_fa() if st else 'فایل وضعیت نظارت خوانا نیست'}"]
        try:
            s = json.loads((self.run_dir / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "\n".join(lines + ["وضعیت موتور در دسترس نیست (status.json)."])
        age = self.clock() - float(s.get("ts", 0))
        pb = s.get("playbook") or {}
        lines += [f"موتور: {s.get('mode')} · سطح {s.get('level')} · مرحله {s.get('phase')} · "
                  f"{'⚠️ ' if age > 60 else ''}به‌روز {int(age)} ثانیه پیش",
                  f"دارایی: {s.get('equity') or 0:,.0f} تومان ({(s.get('gain') or 0):+.3%}) · سفارش باز: {s.get('working_orders')}"]
        if pb:
            lines.append(f"برنامه‌ی Claude: {pb.get('regime')} · هدف {pb.get('core_targets')} · ریسک ×{pb.get('risk_multiplier')}"
                         f" · {pb.get('valid_for_min')} دقیقه اعتبار")
        return "\n".join(lines)

    def digest_fa(self, now: float) -> str:
        day0 = datetime.fromtimestamp(now, tz=config.TEHRAN).replace(hour=0, minute=0, second=0).timestamp()
        counts = {}
        for kind in ("decisions", "fills"):
            p = self.run_dir / "logs" / f"{kind}.jsonl"
            n = 0
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines()[-5000:]:
                    try:
                        n += json.loads(line).get("ts", 0) >= day0
                    except ValueError:
                        continue
            counts[kind] = n
        return (f"📋 بازبینی روزانه ({_fa_time(now)})\n{self.status_fa()}\n"
                f"امروز: {counts['decisions']} تصمیم، {counts['fills']} معامله‌ی پرشده.\n"
                "لطفاً یادداشت بازبینی را بفرستید: /review ...")


class Bot:
    """Bale long-polling around a Controller."""

    def __init__(self, ctl: Controller, token: str, chat_id: str, admins: set[str] | None = None, *,
                 transport: httpx.BaseTransport | None = None, review_hour: int = REVIEW_HOUR):
        self.ctl, self._token, self.chat_id, self.admins = ctl, token, str(chat_id), admins or set()
        self._http = httpx.Client(timeout=35.0, transport=transport)
        self.review_hour = review_hour
        self.offset_path = ctl.run_dir / "oversight" / "bale_offset"
        self.review_path = ctl.run_dir / "oversight" / "review_sent"
        self.review_path.parent.mkdir(parents=True, exist_ok=True)
        self._unauth_logged: dict[str, float] = {}

    def _call(self, method: str, **params) -> dict:
        r = self._http.post(API.format(token=self._token, method=method), json=params)
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"bale {method}: HTTP {r.status_code}")      # never the URL (it holds the token)
        return body

    def send(self, text: str) -> None:
        self._call("sendMessage", chat_id=self.chat_id, text=text[:3500])

    def _offset(self) -> int | None:
        try:
            return int(self.offset_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def poll_once(self, timeout: int = 20) -> int:
        """One getUpdates round. Returns how many updates were seen."""
        offset = self._offset()
        if offset is None:                                # first start: skip the backlog, act only on new messages
            ups = self._call("getUpdates", timeout=0).get("result", [])
            nxt = max((u["update_id"] for u in ups), default=-1) + 1
            self.offset_path.parent.mkdir(parents=True, exist_ok=True)
            self.offset_path.write_text(str(nxt), encoding="utf-8")
            self.ctl._log(event="backlog_skipped", count=len(ups))
            return len(ups)
        ups = self._call("getUpdates", offset=offset, timeout=timeout).get("result", [])
        for u in ups:
            self.offset_path.write_text(str(u["update_id"] + 1), encoding="utf-8")   # before acting: no replays
            msg = u.get("message") or {}
            chat, frm = msg.get("chat") or {}, msg.get("from") or {}
            who = {"id": str(frm.get("id", "")), "name": " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x),
                   "username": frm.get("username"), "via": "bale"}
            if (str(chat.get("id")) != self.chat_id or not who["id"]
                    or (self.admins and who["id"] not in self.admins)):
                key = str(chat.get("id"))[:32]
                if self.ctl.clock() - self._unauth_logged.get(key, -1e18) >= 60:    # bounded log growth
                    self._unauth_logged[key] = self.ctl.clock()
                    self.ctl._log(event="unauthorized", chat_id=key, who={"id": who["id"][:32]})
                continue
            reply = self.ctl.handle(msg.get("text") or "", who, sent_at=float(msg.get("date") or 0))  # no date = stale
            if reply:
                try:
                    self.send(reply)
                except Exception as e:                    # the command itself is already applied and logged
                    self.ctl._log(event="reply_failed", error=type(e).__name__)
        return len(ups)

    def review_due(self, now: float) -> bool:
        local = datetime.fromtimestamp(now, tz=config.TEHRAN)
        try:
            last = self.review_path.read_text(encoding="utf-8").strip()
        except OSError:
            last = ""
        return local.hour >= self.review_hour and last != local.date().isoformat()

    def maybe_review(self, now: float) -> None:
        if self.review_due(now):
            self.send(self.ctl.digest_fa(now))
            self.review_path.write_text(datetime.fromtimestamp(now, tz=config.TEHRAN).date().isoformat(), encoding="utf-8")
            self.ctl._log(event="review_requested")

    def run(self) -> None:
        fails = 0
        while True:
            started = time.time()
            try:
                self.poll_once()
                fails = 0
            except Exception as e:                        # network, Bale hiccups: never die
                fails += 1
                self.ctl._log(event="bot_error", error=type(e).__name__)
                time.sleep(min(60.0, 5.0 * fails))
            try:
                self.maybe_review(self.ctl.clock())       # a failing digest never slows the commands down
            except Exception as e:
                self.ctl._log(event="review_failed", error=type(e).__name__)
            time.sleep(max(0.0, 1.0 - (time.time() - started)))   # Bale ignoring `timeout` can't make us spin


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--local", help="apply one command from this terminal instead of Bale, e.g. \"/pause reason\"")
    ap.add_argument("--yes", action="store_true", help="with --local: confirm /flatten or /resume")
    a = ap.parse_args(argv)
    ctl = Controller(a.run_dir)
    if a.local:
        who = {"id": f"local:{getpass.getuser()}", "name": getpass.getuser(), "via": "terminal"}
        first = a.local.strip().lstrip("/").split(" ", 1)[0].lower()
        if first in ov.NEEDS_CONFIRM and not a.yes:
            print(f"/{first} needs confirmation: run the same command again with --yes")
            return 1
        print(ctl.handle(a.local, who, confirmed=a.yes) or "not a command")
        return 0
    token, chat = os.environ.get("BALE_TOKEN"), os.environ.get("BALE_CHAT_ID")
    if not token or not chat:
        print("BALE_TOKEN and BALE_CHAT_ID are required (or use --local)", file=sys.stderr)
        return 2
    admins = {x.strip() for x in os.environ.get("BALE_ADMIN_IDS", "").split(",") if x.strip()}
    Bot(ctl, token, chat, admins).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
