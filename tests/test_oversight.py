"""Human oversight: a person can only make the engine more careful — never place, approve or size a trade."""
import json
import re
from pathlib import Path

import httpx
import pytest

import config
import engine.run as engine_run
from engine.modes import ShadowBroker
from engine.loop import EngineCfg
from oversight import bot as ovbot
from oversight import run as ovrun
from oversight import state as ov
from oversight.engine import OverseenEngine
from tests.test_loop import GRIDS, NOW, PX, Clock, Market, SentAlerts, cross, decisions, give_playbook
from datetime import datetime

ME = {"id": "42", "name": "Parsa", "via": "test"}


def make(tmp_path, end_in_h=72):
    clock = Clock()
    market = Market(clock)
    cfg = EngineCfg(run_dir=tmp_path / "run", mode="shadow", manager="remote", sha="test",
                    end_at=datetime.fromtimestamp(NOW + end_in_h * 3600, tz=config.TEHRAN))
    eng = OverseenEngine(cfg, broker=ShadowBroker(), market=market, bars=GRIDS.get, clock=clock, alerter=SentAlerts())
    ctl = ovbot.Controller(cfg.run_dir, clock=clock)
    return eng, clock, market, ctl


def oversight_log(run_dir: Path) -> list[dict]:
    p = run_dir / "logs" / "oversight.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def confirm(ctl, text, who=ME):
    reply = ctl.handle(text, who)
    code = re.search(r"/confirm (\d{4})", reply).group(1)
    return ctl.handle(f"/confirm {code}", who)


# ---- parsing and the pure state machine -------------------------------------------------------
def test_parse_commands_persian_digits_and_reasons():
    assert ov.parse("/risk ۰٫۵ اسپرد عجیب است") == ov.Command("risk", 0.5, "اسپرد عجیب است")
    assert ov.parse("/pause@wallexbot news at 14:00") == ov.Command("pause", None, "news at 14:00")
    assert ov.parse("hello") is None
    for bad in ("/risk 1.5", "/risk -0.1", "/risk nan", "/risk", "/buy 1 BTC", "/confirm 12"):
        with pytest.raises(ov.CommandError):
            ov.parse(bad)


def test_state_machine_only_lowers_risk_and_resume_hands_back():
    st = ov.OversightState()
    assert st.ai_in_control
    st = ov.apply(st, ov.Command("pause", reason="r"), now=1.0, who=ME)
    st = ov.apply(st, ov.Command("risk", 0.3), now=2.0, who=ME)
    st = ov.apply(st, ov.Command("flatten"), now=3.0, who=ME)
    st2 = ov.apply(st, ov.Command("flatten"), now=9.0, who=ME)
    assert (st.paused, st.risk_cap, st.flatten_at, st.seq) == (True, 0.3, 3.0, 3)
    assert st2.flatten_at == 3.0                          # a second /flatten keeps the first start time
    assert st.last["who"] == ME and st.last["command"] == "flatten" and st.last["reason"] == "(بدون دلیل)"
    assert ov.apply(st, ov.Command("status"), now=4.0, who=ME) is st
    back = ov.apply(st, ov.Command("resume"), now=5.0, who=ME)
    assert back.ai_in_control and back.seq == 4


# ---- the controller (what the bot and the terminal share) -------------------------------------
def test_every_human_action_is_logged_with_who_when_why(tmp_path):
    ctl = ovbot.Controller(tmp_path, clock=lambda: 1000.0)
    assert "✅" in ctl.handle("/pause اخبار مهم", ME)
    assert "✅" in ctl.handle("/review همه‌چیز عادی بود", ME)
    log = oversight_log(tmp_path)
    applied = [r for r in log if r["event"] == "applied"]
    assert applied[0]["who"] == ME and applied[0]["reason"] == "اخبار مهم" and applied[0]["ts"] == 1000.0
    assert applied[0]["before"] != applied[0]["after"] and applied[0]["seq"] == 1
    assert any(r["event"] == "review" and r["note"] == "همه‌چیز عادی بود" for r in log)
    assert ov.load(ov.state_path(tmp_path)).paused


def test_flatten_and_resume_need_a_confirmation_from_the_same_person(tmp_path):
    t = [1000.0]
    ctl = ovbot.Controller(tmp_path, clock=lambda: t[0])
    reply = ctl.handle("/flatten", ME)
    code = re.search(r"/confirm (\d{4})", reply).group(1)
    assert ov.load(ov.state_path(tmp_path)).flatten_at is None          # nothing happened yet
    assert "فقط همان" in ctl.handle(f"/confirm {code}", {"id": "99"})
    assert "✅" in ctl.handle(f"/confirm {code}", ME)
    assert ov.load(ov.state_path(tmp_path)).flatten_at == 1000.0
    reply = ctl.handle("/resume", ME)
    code = re.search(r"/confirm (\d{4})", reply).group(1)
    t[0] += ovbot.CONFIRM_S + 1
    assert "منقضی" in ctl.handle(f"/confirm {code}", ME)                 # expired
    assert ov.load(ov.state_path(tmp_path)).flatten_at == 1000.0


def test_old_messages_are_not_acted_on(tmp_path):
    ctl = ovbot.Controller(tmp_path, clock=lambda: 10_000.0)
    assert "قدیمی" in ctl.handle("/pause", ME, sent_at=10_000.0 - ovbot.MAX_AGE_S - 1)
    assert not ov.state_path(tmp_path).exists()
    assert oversight_log(tmp_path)[-1]["event"] == "stale"


def test_unreadable_state_file_is_rebuilt_from_the_safe_side(tmp_path):
    p = ov.state_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    ctl = ovbot.Controller(tmp_path, clock=lambda: 1.0)
    assert "✅" in ctl.handle("/pause", ME)
    assert ov.load(p).paused


# ---- the Bale bot --------------------------------------------------------------------------------
class FakeBale:
    def __init__(self, updates):
        self.updates, self.sent, self.urls = list(updates), [], []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.urls.append(str(req.url))
        body = json.loads(req.content or b"{}")
        if req.url.path.endswith("/getUpdates"):
            off = body.get("offset", 0)
            return httpx.Response(200, json={"ok": True, "result": [u for u in self.updates if u["update_id"] >= off]})
        self.sent.append(body["text"])
        return httpx.Response(200, json={"ok": True, "result": {}})


def msg(uid, text, chat="777", frm="42", date=1000.0):
    return {"update_id": uid, "message": {"date": date, "text": text, "chat": {"id": int(chat)},
                                          "from": {"id": int(frm), "first_name": "Parsa", "username": "p"}}}


def test_bot_skips_backlog_obeys_only_its_chat_and_admins_and_never_logs_the_token(tmp_path):
    fake = FakeBale([msg(1, "/flatten old backlog")])
    ctl = ovbot.Controller(tmp_path, clock=lambda: 1000.0)
    b = ovbot.Bot(ctl, "SECRET-TOKEN", "777", {"42"}, transport=httpx.MockTransport(fake))
    b.poll_once()                                                      # first start: backlog skipped
    assert not ov.state_path(tmp_path).exists() and not fake.sent
    fake.updates += [msg(2, "/pause from a stranger", chat="666"), msg(3, "/pause not an admin", frm="13"),
                     msg(4, "/pause spread 5x"), msg(5, "/status")]
    b.poll_once()
    st = ov.load(ov.state_path(tmp_path))
    assert st.paused and st.last["reason"] == "spread 5x" and st.last["who"]["id"] == "42"
    assert len(fake.sent) == 2 and "✅" in fake.sent[0] and "نظارت" in fake.sent[1]
    log = oversight_log(tmp_path)
    assert [r["event"] for r in log].count("unauthorized") == 2
    assert all("text" not in r for r in log if r["event"] == "unauthorized")
    assert "SECRET-TOKEN" not in (tmp_path / "logs" / "oversight.jsonl").read_text(encoding="utf-8")
    b.poll_once()
    assert len(fake.sent) == 2                                         # offset moved: nothing replayed


def test_daily_review_digest_once_per_day(tmp_path):
    fake = FakeBale([])
    t = [datetime(2026, 10, 22, 21, 5, tzinfo=config.TEHRAN).timestamp()]
    ctl = ovbot.Controller(tmp_path, clock=lambda: t[0])
    b = ovbot.Bot(ctl, "tok", "777", transport=httpx.MockTransport(fake))
    b.maybe_review(t[0])
    b.maybe_review(t[0] + 60)
    assert len(fake.sent) == 1 and "/review" in fake.sent[0]
    t[0] += 86400
    b.maybe_review(t[0])
    assert len(fake.sent) == 2


# ---- the engine obeys (shadow broker, same engine code as live) -------------------------------
def test_pause_blocks_buys_and_cancels_working_ones(tmp_path):
    eng, clock, market, ctl = make(tmp_path)
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()
    assert any(w.side == "BUY" for w in eng.ex.working.values())
    ctl.handle("/pause اخبار", ME)
    clock.t += 10
    eng.tick()
    assert not any(w.side == "BUY" for w in eng.ex.working.values())
    ev = [e for e in eng.logs.tail("events", 200) if e["event"] in ("oversight_applied", "buys_cancelled")]
    assert ev[0]["event"] == "oversight_applied" and ev[0]["by"]["who"] == ME and ev[1]["reason"] == "human_oversight"
    n = len(decisions(eng))
    for _ in range(5):
        clock.t += 60
        eng.tick()
    assert not [d for d in decisions(eng)[n:] if d["side"] == "BUY" and d["order_client_id"]]
    ctl.handle("/resume", ME, confirmed=True)
    clock.t += 10
    eng.tick()
    assert any(w.side == "BUY" for w in eng.ex.working.values())          # Claude's plan applies again


def test_risk_cap_shrinks_targets_but_never_above_claudes(tmp_path):
    eng, clock, market, ctl = make(tmp_path)
    eng.tick()
    give_playbook(eng, usd=0.20, btc=0.10, risk=0.8)
    clock.t += 10
    eng.tick()
    assert eng._targets["BTCTMN"] == pytest.approx(0.08)
    ctl.handle("/risk 0.5", ME)
    clock.t += 10
    eng.tick()
    assert eng._targets["BTCTMN"] == pytest.approx(0.05)
    ctl.handle("/risk 1", ME)                                            # lifting the cap: back to Claude's 0.8
    clock.t += 10
    eng.tick()
    assert eng._targets["BTCTMN"] == pytest.approx(0.08)


def test_flatten_runs_the_normal_sell_off_and_resume_restores_the_contest_end(tmp_path):
    eng, clock, market, ctl = make(tmp_path)
    real_end = eng.cfg.end_at
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()
    for m in PX:
        cross(eng, market, m)
    clock.t += 10
    eng.tick()
    assert eng.pf.qty("BTCTMN") > 0
    confirm(ctl, "/flatten تست")
    clock.t += 10
    eng.tick()
    assert eng.cfg.end_at.timestamp() == pytest.approx(clock.t - 10 + 3600, abs=11)
    assert not any(w.side == "BUY" for w in eng.ex.working.values())
    step = 0
    while step < 70:                                                    # past the 1 h compressed schedule
        clock.t += 60
        step += 1
        for m in PX:
            market.set(m, PX[m] * (1 + 0.002 * ((step % 5) - 2)))
        eng.tick()
    for m in PX:
        assert eng.pf.qty(m) * PX[m] < config.LIMITS.exchange_min_notional_tmn
    sells = [d for d in decisions(eng) if d["side"] == "SELL"]
    assert sells and all(d["decided_by"].startswith("flatten (human #") for d in sells)
    finals = [e for e in eng.logs.tail("events", 3000) if e["event"] == "reconcile" and e.get("final")]
    assert not finals and not any(t.startswith("🏁") for t in eng.alerter.texts)
    confirm(ctl, "/resume")
    clock.t += 10
    eng.tick()
    assert eng.cfg.end_at == real_end and eng.last_phase == "normal"


def test_bad_state_file_keeps_the_last_good_state(tmp_path):
    eng, clock, market, ctl = make(tmp_path)
    ctl.handle("/pause", ME)
    eng.tick()
    assert eng.oversight.paused
    ov.state_path(eng.cfg.run_dir).write_text('{"paused": false, "risk_cap": 7, "seq": 9}', encoding="utf-8")
    clock.t += 10
    eng.tick()
    assert eng.oversight.paused
    assert any(e["event"] == "oversight_state_invalid" for e in eng.logs.tail("events", 100))


def test_no_state_file_changes_nothing(tmp_path):
    eng, clock, market, ctl = make(tmp_path)
    eng.tick()
    assert eng.oversight.ai_in_control and eng.cfg.end_at.timestamp() == pytest.approx(NOW + 72 * 3600)


def test_oversight_run_reuses_the_traded_entry_point(monkeypatch):
    seen = {}
    monkeypatch.setattr(engine_run, "main", lambda argv: seen.setdefault("engine", engine_run.Engine))
    monkeypatch.setattr(engine_run, "Engine", engine_run.Engine)
    ovrun.main(["--mode", "shadow"])
    assert seen["engine"] is OverseenEngine


def test_oversight_code_never_trades():
    """Compliance: no order placement, no trading client, no sizing in the oversight package."""
    src = "\n".join(p.read_text(encoding="utf-8") for p in (Path(__file__).parent.parent / "oversight").glob("*.py"))
    for forbidden in ("TradingClient", "place_order", ".submit(", "wallex.trading", "LiveBroker", "Proposal("):
        assert forbidden not in src, forbidden


def test_decision_log_shows_human_commands(tmp_path, monkeypatch):
    from data import fetch
    from judge.demo import SAMPLE
    from reports import shadow_report
    monkeypatch.setattr(fetch.load, "__defaults__", (SAMPLE,))
    logs = tmp_path / "logs"
    ctl = ovbot.Controller(tmp_path, clock=lambda: NOW)
    ctl.handle("/risk 0.5 بازار شلوغ است", ME)
    ctl.handle("/review روز آرام", ME)
    monkeypatch.chdir(tmp_path)
    shadow_report.main(["--logs", str(logs), "--name", "t"])
    md = (tmp_path / "delivery" / "DECISIONS-t.md").read_text(encoding="utf-8")
    assert "## نظارت انسانی" in md and "بازار شلوغ است" in md and "روز آرام" in md and "Parsa" in md
    rep = json.loads((tmp_path / "reports" / "out" / "t-report.json").read_text(encoding="utf-8"))
    assert rep["human_oversight"] == {"applied": 1, "review": 1}
