"""C4: the engine loop in shadow mode, end to end, plus reconcile, alerts, watchdog, status page."""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as NS

import httpx
import pytest

import config
from alerts import watchdog
from alerts.bale import Alerter
from data.poll import MarketSnapshot
from engine.loop import Engine, EngineCfg
from engine.modes import ShadowBroker
from engine.portfolio import Portfolio
from engine.reconcile import portfolio_from_balances, reconcile
from manager.schema import PerMarketBool, PerMarketFloat, finalize
from tests.test_manager import grid, out
from wallex.client import Balance, Order

NOW = datetime(2026, 10, 22, 11, 0, tzinfo=config.TEHRAN).timestamp()
PX = {"USDTTMN": 266_000.0, "BTCTMN": 28_000_000_000.0}


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Market:
    def __init__(self, clock):
        self.clock = clock
        self.px = dict(PX)
        self.trade_age = 20.0

    def set(self, m, mid):
        self.px[m] = mid

    def __call__(self):
        t = self.clock()
        return {m: MarketSnapshot(m, p * 0.9999, p * 1.0001, t, 0.0, p, t - self.trade_age) for m, p in self.px.items()}


class SentAlerts(Alerter):
    def __init__(self):
        super().__init__("tok", "chat", dedupe_s=0)
        self.texts = []

    def send(self, text, *, key=None, force=False):
        self.texts.append(text)
        return True


GRIDS = {"USDTTMN": grid(266_000, 0.0, 1), "BTCTMN": grid(28e9, 0.0, 2)}


def make_engine(tmp_path, *, manager="remote", end_in_h=72, clock=None, market=None, alerter=None):
    clock = clock or Clock()
    market = market or Market(clock)
    alerter = alerter or SentAlerts()
    cfg = EngineCfg(run_dir=tmp_path / "run", mode="shadow", manager=manager, sha="test",
                    end_at=datetime.fromtimestamp(NOW + end_in_h * 3600, tz=config.TEHRAN))
    eng = Engine(cfg, broker=ShadowBroker(), market=market, bars=GRIDS.get, clock=clock, alerter=alerter)
    return eng, clock, market, alerter


def give_playbook(eng, regime="devaluation_drift", usd=0.20, btc=0.10, risk=1.0, minutes=60):
    """Answer the engine's latest bundle like the laptop would (through the inbox)."""
    bundle = json.loads((eng.outbox_dir / "bundle.json").read_text(encoding="utf-8"))
    o = out(regime=regime, core_targets=PerMarketFloat(USDTTMN=usd, BTCTMN=btc), risk_multiplier=risk,
            valid_minutes=minutes, tactical_enabled=PerMarketBool(USDTTMN=False, BTCTMN=False))
    pb = finalize(o, bundle_hash=bundle["hash"], now=eng.clock(), source="claude", model="test")
    inbox = eng.cfg.run_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"playbook-{pb.playbook_id}.json").write_text(pb.model_dump_json(), encoding="utf-8")
    return pb


def cross(eng, market, m):
    """Move the quote so our resting passive limits on market m fill."""
    bid, ask, _ = eng.broker.quote(m)
    market.set(m, bid * 0.995)          # buys at the old bid fill when the ask drops below it


def decisions(eng):
    return eng.logs.tail("decisions", 500)


# ---- the loop ----------------------------------------------------------------------------
def test_no_playbook_means_no_buys_and_a_manager_request(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    assert eng.level == 3 and not eng.broker.orders
    b = json.loads((eng.outbox_dir / "bundle.json").read_text(encoding="utf-8"))
    assert b["manager_due"] and b["manager_request_id"].startswith("req-") and b["reason"] == "scheduled"
    st = json.loads((tmp_path / "run" / "status.json").read_text(encoding="utf-8"))
    assert st["level"] == 3 and st["equity"] == 5_000_000
    assert float((tmp_path / "run" / "heartbeat").read_text()) == NOW


def test_playbook_drives_core_buys_then_fills_once_per_day(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    pb = give_playbook(eng)
    clock.t += 10
    eng.tick()
    assert eng.level == 0 and eng.playbook.playbook_id == pb.playbook_id
    buys = [d for d in decisions(eng) if d["side"] == "BUY"]
    assert {d["market"] for d in buys} == {"USDTTMN", "BTCTMN"}
    assert all(d["decided_by"] == "manager" and d["gate"]["verdict"] != "block" and d["order_client_id"]
               and "تصمیم‌گیرنده" in d["reason_fa"] for d in buys)
    for m in PX:
        cross(eng, market, m)
    clock.t += 10
    eng.tick()
    bids = eng.bids
    assert eng.pf.exposure("USDTTMN", bids, "core") == pytest.approx(0.10, abs=0.01)    # first 10% step
    assert eng.pf.exposure("BTCTMN", bids, "core") == pytest.approx(0.10, abs=0.01)    # BTC target reached
    clock.t += 10
    eng.tick()                                                   # second (and last) increase today: USDT → 20%
    cross(eng, market, "USDTTMN")
    clock.t += 10
    eng.tick()
    assert eng.pf.exposure("USDTTMN", eng.bids, "core") == pytest.approx(0.20, abs=0.015)
    assert eng.pf.core_increases["USDTTMN"] == 2 and eng.pf.core_increases["BTCTMN"] == 1
    n = len(eng.broker.orders)
    clock.t += 10
    eng.tick()
    assert len(eng.broker.orders) == n                           # at target: no more orders
    assert len(eng.logs.tail("fills", 50)) >= 3


def test_expired_playbook_goes_level_2_then_3_with_alerts(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng, minutes=30)
    clock.t += 10
    eng.tick()
    assert eng.level == 0
    clock.t += 31 * 60
    eng.tick()
    assert eng.level == 2 and any("سطح احتیاط 2" in t for t in alerts.texts)
    clock.t += 2 * 3600
    eng.tick()
    assert eng.level == 3


def test_risk_off_sells_the_core(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()
    for m in PX:
        cross(eng, market, m)
    clock.t += 10
    eng.tick()
    assert eng.pf.qty("USDTTMN") > 0
    clock.t += 61 * 60                                # next hourly request
    eng.tick()
    give_playbook(eng, regime="risk_off", usd=0, btc=0)
    clock.t += 10
    eng.tick()
    sells = [d for d in decisions(eng) if d["side"] == "SELL" and d["kind"] == "rebalance_down"]
    assert {d["market"] for d in sells} == {"USDTTMN", "BTCTMN"}
    for m in PX:
        bid, ask, _ = eng.broker.quote(m)
        market.set(m, ask * 1.005)                    # sells at the old ask fill when the bid rises above it
    clock.t += 10
    eng.tick()
    assert eng.pf.qty("USDTTMN") == pytest.approx(0, abs=0.02)


def test_idle_market_blocks_buys_and_logs_once(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    market.trade_age = 3 * 3600                         # book fresh, nobody traded for 3 h
    eng.tick()
    give_playbook(eng)
    for _ in range(5):
        clock.t += 10
        eng.tick()
    buys = [d for d in decisions(eng) if d["side"] == "BUY"]
    assert buys and all("G_STALE_DATA" in d["gate"]["rules"] for d in buys)
    assert len(buys) == 2                               # one per market, not one per tick
    assert not eng.broker.orders


def test_flatten_window_ends_all_in_tmn(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path, end_in_h=8.5)
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()
    for m in PX:
        cross(eng, market, m)
    clock.t += 10
    eng.tick()
    assert eng.pf.qty("BTCTMN") > 0
    step = 0
    while clock.t < NOW + 8.5 * 3600 + 60:
        clock.t += 60
        step += 1
        for m in PX:                                    # a market that wobbles, so passive sells fill too
            market.set(m, PX[m] * (1 + 0.002 * ((step % 5) - 2)))
        eng.tick()
    for m in PX:
        assert eng.pf.qty(m) * PX[m] < config.LIMITS.exchange_min_notional_tmn
    phases = [e["phase"] for e in eng.logs.tail("events", 2000) if e["event"] == "flatten_phase"]
    assert phases[:2] == ["normal", "no_new"] and "market" in phases


def test_hard_flatten_latches_and_alerts(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()
    for m in PX:
        cross(eng, market, m)
    clock.t += 10
    eng.tick()
    for m in PX:
        market.set(m, PX[m] * 0.80)                   # −20% on ~20% exposure → about −4%
    clock.t += 10
    eng.tick()
    assert not eng.pf.halted                          # one sample is not enough (glitch guard)
    clock.t += 10
    eng.tick()
    assert eng.pf.halted and any("🛑" in t for t in alerts.texts)
    clock.t += 10
    eng.tick()
    assert eng.pf.qty("USDTTMN") == pytest.approx(0, abs=0.02) and eng.pf.qty("BTCTMN") * PX["BTCTMN"] < 50_000


def test_restart_restores_state_and_playbook(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()
    for m in PX:
        cross(eng, market, m)
    clock.t += 10
    eng.tick()
    qty, pid, inc = eng.pf.qty("USDTTMN"), eng.playbook.playbook_id, dict(eng.pf.core_increases)
    eng2, *_ = make_engine(tmp_path, clock=clock, market=market)
    assert eng2.pf.qty("USDTTMN") == pytest.approx(qty) and eng2.playbook.playbook_id == pid
    assert dict(eng2.pf.core_increases) == inc


def test_rules_manager_baseline_needs_no_inbox(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path, manager="rules")
    eng.tick()
    assert eng.playbook is not None and eng.playbook.source == "mock"
    clock.t += 10
    eng.tick()
    assert eng.level == 0


# ---- reconcile ------------------------------------------------------------------------------
def _order(cid):
    return Order(cid, "USDTTMN", "BUY", "LIMIT", "NEW", True, Decimal(1), Decimal(1), Decimal(0), Decimal(0), None)


def test_reconcile_reports_differences_and_foreign_orders():
    pf = Portfolio(4_000_000, NOW)
    p = pf.pos("USDTTMN", "core")
    p.qty, p.cost_tmn = 3.0, 798_000
    bal = {"TMN": Balance("TMN", Decimal(4_000_000), Decimal(0)), "USDT": Balance("USDT", Decimal("3.5"), Decimal(0)),
           "DOGE": Balance("DOGE", Decimal(10), Decimal(0))}
    rep = reconcile(pf, bal, [_order("d-1-USDTTMN-c"), _order("manual")], PX, known_client_ids=set())
    assert not rep.ok and "USDT" in rep.asset_diffs and rep.foreign_orders == ["manual"]
    assert rep.orphan_orders == ["d-1-USDTTMN-c"] and "DOGE" in rep.other_assets
    assert "⚠️" in rep.summary_fa()
    clean = reconcile(pf, {"TMN": Balance("TMN", Decimal(4_000_000), Decimal(0)),
                           "USDT": Balance("USDT", Decimal(3), Decimal(0))}, [], PX, set())
    assert clean.ok


def test_fresh_start_requires_an_all_tmn_account():
    pf = portfolio_from_balances({"TMN": Balance("TMN", Decimal(5_000_000), Decimal(0))}, NOW)
    assert pf.cash == 5_000_000
    with pytest.raises(RuntimeError):
        portfolio_from_balances({"TMN": Balance("TMN", Decimal(1), Decimal(0)),
                                 "BTC": Balance("BTC", Decimal("0.1"), Decimal(0))}, NOW)


# ---- alerts, watchdog, status page ---------------------------------------------------------------
def test_bale_alerter_dedupes_and_never_leaks_the_token():
    seen, errors = [], []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"ok": True})
    t = {"now": 0.0}
    al = Alerter("SECRET-TOKEN", "123", transport=httpx.MockTransport(handler), clock=lambda: t["now"],
                 on_error=errors.append, sync=True)
    assert al.send("hello", key="k") and not al.send("hello again", key="k")
    t["now"] = 1801
    assert al.send("hello again", key="k")
    assert json.loads(seen[0].content)["chat_id"] == "123"

    def boom(req):
        raise httpx.ConnectError("down")
    bad = Alerter("SECRET-TOKEN", "123", transport=httpx.MockTransport(boom), on_error=errors.append, sync=True)
    assert bad.send("x") is False
    assert errors and all("SECRET-TOKEN" not in e for e in errors)
    assert Alerter(None, None).send("x") is False


def test_watchdog_alerts_once_down_and_once_up(tmp_path):
    al = SentAlerts()
    (tmp_path / "heartbeat").write_text(str(NOW), encoding="utf-8")
    assert watchdog.check(tmp_path, al, now=NOW + 30) == "ok"
    assert watchdog.check(tmp_path, al, now=NOW + 300) == "alerted_down"
    assert watchdog.check(tmp_path, al, now=NOW + 360) == "ok"            # no repeat while still down
    (tmp_path / "heartbeat").write_text(str(NOW + 400), encoding="utf-8")
    assert watchdog.check(tmp_path, al, now=NOW + 410) == "alerted_up"
    assert len(al.texts) == 2


def test_status_page_renders_and_lights(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from web import status as web
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    monkeypatch.setattr(web, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(web.time, "time", lambda: NOW + 5)
    c = TestClient(web.app)
    r = c.get("/")
    assert r.status_code == 200 and 'dir="rtl"' in r.text and "ربات خرید نمی‌کند" in r.text   # level 3 → red
    assert c.get("/status.json").json()["light"] == "red"
    st, beat = web.load(tmp_path / "run")
    assert web.light(dict(st, level=0), beat, NOW + 5) == "green"
    assert web.light(dict(st, level=0), beat, NOW + 500) == "red"                               # stale heartbeat


# ---- C4 review follow-ups ------------------------------------------------------------------
def held_engine(tmp_path, **kw):
    """An engine that already holds its first core step in both markets."""
    eng, clock, market, alerts = make_engine(tmp_path, **kw)
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()
    for m in PX:
        cross(eng, market, m)
    clock.t += 10
    eng.tick()
    assert eng.pf.qty("USDTTMN") > 0 and eng.pf.qty("BTCTMN") > 0
    return eng, clock, market, alerts


def test_level_2_holds_and_never_sells_because_the_laptop_is_late(tmp_path):
    eng, clock, market, alerts = held_engine(tmp_path)
    clock.t += 61 * 60                                     # playbook expired, no new one
    eng.tick()
    clock.t += 10
    eng.tick()
    assert eng.level == 2
    assert not [d for d in decisions(eng) if d["kind"] == "rebalance_down"]


def test_next_playbook_is_requested_before_expiry(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng, minutes=60)
    clock.t += 10
    eng.tick()
    first_req = [e for e in eng.logs.tail("events", 100) if e["event"] == "manager_request"]
    clock.t += 41 * 60                                     # 19 min of validity left
    eng.tick()
    reqs = [e for e in eng.logs.tail("events", 200) if e["event"] == "manager_request"]
    assert len(reqs) == len(first_req) + 1 and eng.level == 0


def test_stop_is_not_blocked_by_a_resting_passive_sell(tmp_path):
    eng, clock, market, alerts = held_engine(tmp_path)
    clock.t += 61 * 60
    eng.tick()
    give_playbook(eng, regime="range", usd=0.0, btc=0.0)   # reduce → passive sells start resting
    clock.t += 10
    eng.tick()
    assert any(w.side == "SELL" and w.urgency == "passive" for w in eng.ex.working.values())
    for m in PX:
        market.set(m, PX[m] * 0.96)                        # −4% → core stop (−2.5%), two polls
    for _ in range(2):
        clock.t += 10
        eng.tick()
    stops = [d for d in decisions(eng) if d["kind"] == "stop"]
    assert stops and all(d["gate"]["verdict"] != "block" and d["order_client_id"] for d in stops)
    assert eng.pf.qty("USDTTMN") == pytest.approx(0, abs=0.02)


def test_working_buys_are_cancelled_when_risk_must_not_grow(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng, minutes=15)
    clock.t += 10
    eng.tick()
    assert any(w.side == "BUY" for w in eng.ex.working.values())
    clock.t += 15 * 60 + 2 * 3600 + 60                     # past the 2 h grace → level 3
    eng.tick()
    assert eng.level == 3 and not any(w.side == "BUY" for w in eng.ex.working.values())
    assert any(e["event"] == "buys_cancelled" for e in eng.logs.tail("events", 200))


def test_a_side_task_error_never_stops_state_and_heartbeat(tmp_path):
    def broken_bars(m):
        raise RuntimeError("bars exploded")
    clock = Clock()
    cfg = EngineCfg(run_dir=tmp_path / "run", sha="t", end_at=datetime.fromtimestamp(NOW + 72 * 3600, tz=config.TEHRAN))
    eng = Engine(cfg, broker=ShadowBroker(), market=Market(clock), bars=broken_bars, clock=clock, alerter=SentAlerts())
    eng.tick()
    assert float((tmp_path / "run" / "heartbeat").read_text()) == NOW
    assert (tmp_path / "run" / "state.json").exists()
    assert any(e["event"] == "side_task_error" for e in eng.logs.tail("events", 50))


def test_active_playbook_survives_log_rotation(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    pb = give_playbook(eng)
    clock.t += 10
    eng.tick()
    (tmp_path / "run" / "logs" / "playbooks.jsonl").write_text("", encoding="utf-8")   # copytruncate
    eng2, *_ = make_engine(tmp_path, clock=clock, market=market)
    assert eng2.playbook is not None and eng2.playbook.playbook_id == pb.playbook_id


def test_single_glitch_quote_does_not_fire_a_stop(tmp_path):
    eng, clock, market, alerts = held_engine(tmp_path)
    market.set("BTCTMN", PX["BTCTMN"] * 0.90)              # one bad sample
    clock.t += 10
    eng.tick()
    market.set("BTCTMN", PX["BTCTMN"])                     # back to normal
    clock.t += 10
    eng.tick()
    assert not [d for d in decisions(eng) if d["kind"] == "stop"] and eng.pf.qty("BTCTMN") > 0


def test_unrefreshed_snapshots_go_stale(tmp_path):
    clock = Clock()
    frozen = Market(clock)
    snap = frozen()                                        # the poller keeps returning this old one
    eng, *_ = make_engine(tmp_path, clock=clock, market=lambda: snap)
    eng.tick()
    clock.t += 60
    eng.tick()
    assert eng.level == 3                                   # stale after 30 s, without counting errors


def test_watchdog_retries_when_bale_fails(tmp_path):
    class Failing(SentAlerts):
        def send(self, text, *, key=None, force=False):
            self.texts.append(text)
            return False
    al = Failing()
    (tmp_path / "heartbeat").write_text(str(NOW), encoding="utf-8")
    assert watchdog.check(tmp_path, al, now=NOW + 300) == "alert_failed"
    assert watchdog.check(tmp_path, al, now=NOW + 360) == "alert_failed"    # tried again
    assert len(al.texts) == 2


def test_clear_halt_command(tmp_path):
    from engine import run
    pf = Portfolio(5_000_000, NOW)
    pf.latch_halt("test")
    pf.save(tmp_path / "state.json")
    run.main(["--run-dir", str(tmp_path), "--clear-halt"])
    assert not Portfolio.load(tmp_path / "state.json", NOW).halted


def test_crash_restart_does_not_spam_start_alerts(tmp_path):
    al = SentAlerts()
    make_engine(tmp_path, alerter=al)
    make_engine(tmp_path, alerter=al)
    assert sum("روشن شد" in t for t in al.texts) == 1


def test_recovery_to_level_0_is_not_an_event_and_fresh_playbooks_suppress_events(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()                                                 # level 3 → 0
    clock.t += 61
    eng.tick()
    reqs = [e for e in eng.logs.tail("events", 200) if e["event"] == "manager_request"]
    assert [r["reason"] for r in reqs] == ["scheduled"]       # no "event: degrade level 0"
