"""Batch A fixes from the pre-go-live audit (docs/wallex-audit-2-2026-10-22.md)."""
import json
from datetime import datetime
from types import SimpleNamespace as NS

import httpx
import pytest

import config
from alerts.bale import Alerter
from data.poll import snapshot_from
from engine.exchange import SimExchange
from engine.executor import Executor
from engine.gate import Verdict
from engine.live import to_state
from engine.loop import Engine, EngineCfg
from engine.modes import ShadowBroker
from engine.types import Proposal
from tests.test_live import FakeWallex, make
from tests.test_loop import GRIDS, NOW, PX, Clock, Market, SentAlerts, cross, decisions, give_playbook, make_engine
from wallex.client import Depth, WallexError, _parse_order


# ---- A1: a buy that doesn't fill passively crosses the spread (the re-check no longer counts itself)
def test_unfilled_core_buy_crosses_after_reprices_and_fills(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng)
    for _ in range(40):                                 # ~6.5 min of 10 s ticks: 2 re-prices, then cross
        clock.t += 10
        eng.tick()
    fills = eng.logs.tail("fills", 50)
    assert {f["market"] for f in fills} == {"USDTTMN", "BTCTMN"}
    assert eng.pf.core_increases["USDTTMN"] == 1


def test_cancelled_unfilled_buy_does_not_use_up_the_day(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng)
    clock.t += 10
    eng.tick()
    eng.ex.cancel_all(clock.t, "test")
    assert eng.pf.core_increases["USDTTMN"] == 0 and eng.pf.activity_today == 0


# ---- A3: a fee in the wrong unit can't shrink a position
def test_implausible_fee_is_replaced_by_the_taker_estimate():
    o = _parse_order({"symbol": "USDTTMN", "side": "BUY", "type": "LIMIT", "clientOrderId": "c", "price": "266000",
                      "origQty": "3", "executedQty": "3", "executedSum": "798000", "status": "FILLED", "active": False,
                      "fee": "1995"})                    # TMN, but no asset field → would read as 1995 USDT
    st, est = to_state(o, (0.0025, 0.003))
    assert est and st.fee_total == pytest.approx(3 * 0.003)


# ---- A5: live "market" orders are protected limits
def test_market_order_goes_out_as_a_protected_limit():
    fake = FakeWallex()
    fake.balances = {"TMN": "5000000", "USDT": "5"}
    _, _, broker = make(fake)
    broker.place("d-m1-USDTTMN-c", "USDTTMN", "SELL", "MARKET", 3.0)
    body = json.loads([r for r in fake.requests if r.method == "POST"][-1].content)
    assert body["type"] == "LIMIT" and body["price"] == str(int(-(-266_000 * 0.98 // 1)))   # bid −2%, ceil


def test_no_order_is_priced_from_a_stale_quote():
    from engine.exchange import OrderRejected
    fake = FakeWallex()
    _, _, broker = make(fake)
    broker.clock = lambda: broker.quote("USDTTMN")[2] + 60
    with pytest.raises(OrderRejected, match="fresh quote"):
        broker.place("d-s1-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3.0, 266_000)
    assert not [r for r in fake.requests if r.method == "POST"]


# ---- A6: one glitch quote changes neither stops nor targets
def test_single_glitch_tick_does_not_trigger_reductions(tmp_path):
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
        market.set(m, PX[m] * 0.70)                    # one absurd sample: −30% on ~20% exposure ≈ −6%
    clock.t += 10
    eng.tick()
    assert not [d for d in decisions(eng) if d["side"] == "SELL"] and not eng.pf.halted


# ---- A7: one bad step can't stop the heartbeat; alerts never block
def test_route_exception_is_isolated(tmp_path, monkeypatch):
    eng, clock, market, alerts = make_engine(tmp_path)
    eng.tick()
    give_playbook(eng)
    monkeypatch.setattr(eng, "_route", lambda p, now: (_ for _ in ()).throw(RuntimeError("boom")))
    clock.t += 10
    eng.tick()
    assert float((tmp_path / "run" / "heartbeat").read_text()) == clock.t
    assert any(e["event"].startswith("route_error") for e in eng.logs.tail("events", 100))


def test_async_alerts_retry_critical_messages_in_the_background():
    calls = {"n": 0}

    def flaky(req):
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})
    al = Alerter("tok", "1", transport=httpx.MockTransport(flaky), sleep=lambda s: None)
    assert al.send("🛑 critical", force=True) is True          # queued instantly, never blocks
    al.flush(5)
    assert al.sent == ["🛑 critical"] and calls["n"] == 3


# ---- A8: unusable order books are rejected
@pytest.mark.parametrize("bid,ask", [(266_100, 266_000), (266_000, 266_000), (0, 266_000)])
def test_crossed_or_zero_books_are_rejected(bid, ask):
    with pytest.raises(WallexError):
        snapshot_from("USDTTMN", Depth(bids=[(bid, 1.0)], asks=[(ask, 1.0)], fetched_at=0), [])


# ---- A10: urgency caps
def _exec(cap):
    ex = SimExchange()
    ex.set_quote("USDTTMN", 265_990, 266_010, 0)
    ex.cross_on_place = False
    return ex, Executor(ex, on_fill=lambda f: None, urgency_cap=cap)


def test_core_reductions_never_go_to_market():
    ex, x = _exec(lambda p: "aggressive")
    p = Proposal("USDTTMN", "core", "SELL", 798_000, "rebalance_down", "manager", "r1", "passive", 3.0)
    x.submit(p, Verdict("allow", 798_000, ()), 0)
    for i in range(1, 8):
        x.tick(90 * i)
    assert {o.type for o in ex.orders.values()} == {"LIMIT"}


def test_take_profit_stays_maker():
    ex, x = _exec(lambda p: "passive")
    p = Proposal("USDTTMN", "tactical", "SELL", 798_000, "take_profit", "gate", "t1", "passive", 3.0)
    x.submit(p, Verdict("allow", 798_000, ()), 0)
    for i in range(1, 8):
        x.tick(90 * i)
    assert all(o.price == 266_010 for o in ex.orders.values())      # always at the ask, never crossing


def test_flatten_follows_its_phase(tmp_path):
    eng, clock, market, alerts = make_engine(tmp_path, end_in_h=7.9)    # already in "no_new"
    p = Proposal("USDTTMN", "core", "SELL", 1, "flatten", "flatten", "f1", "passive", 1.0)
    assert eng._urgency_cap(p) == "passive"
    clock.t = NOW + 7.9 * 3600 - 0.5 * 3600                              # T−0.5 h
    assert eng._urgency_cap(p) == "market"
    hard = Proposal("USDTTMN", "core", "SELL", 1, "flatten", "gate", "h1", "market", 1.0)
    assert eng._urgency_cap(hard) == "market"


# ---- A4: before the end, the books follow the real balances
class Account:
    def __init__(self, usdt):
        from decimal import Decimal
        from wallex.client import Balance
        self.b = {"TMN": Balance("TMN", Decimal(4_200_000), Decimal(0)),
                  "USDT": Balance("USDT", Decimal(str(usdt)), Decimal(0))}

    def balances(self):
        return self.b

    def open_orders(self):
        return []


def test_market_phase_adopts_real_balances_so_the_sell_off_sells_them(tmp_path):
    clock = Clock(NOW)
    end = datetime.fromtimestamp(NOW + 3600 + 30, tz=config.TEHRAN)     # "market" phase starts in 30 s
    eng = Engine(EngineCfg(run_dir=tmp_path / "run", mode="live", sha="t", end_at=end), broker=ShadowBroker(),
                 market=Market(clock), bars=GRIDS.get, clock=clock, alerter=SentAlerts(), account=Account(3.0))
    eng.tick()
    assert eng.pf.qty("USDTTMN") == 0
    clock.t += 60
    eng.tick()
    assert any(e["event"] == "balances_adopted" for e in eng.logs.tail("events", 100))
    clock.t += 10
    eng.tick()                                                      # adopted after the fills, sold next tick
    sells = [d for d in decisions(eng) if d["kind"] == "flatten"]
    assert sells and sells[0]["qty"] == pytest.approx(3.0)          # the sell-off sold what Wallex really held
    assert eng.pf.qty("USDTTMN") == pytest.approx(0, abs=1e-9)


# ---- A2: a redeploy can't turn live into shadow
def test_shadow_refuses_a_run_dir_holding_live_state(tmp_path):
    from engine import run
    (tmp_path / "state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "state.mode").write_text("live", encoding="utf-8")
    with pytest.raises(SystemExit, match="LIVE state"):
        run.refuse_mode_mismatch(tmp_path, "shadow")
    run.refuse_mode_mismatch(tmp_path, "live")                          # fine


def test_baseline_can_never_run_live(tmp_path):
    from engine import run
    with pytest.raises(SystemExit, match="baseline"):
        run.main(["--mode", "live", "--manager", "rules", "--run-dir", str(tmp_path)])


def test_unit_file_reads_the_mode_from_the_env_file():
    from pathlib import Path
    unit = (Path(__file__).parent.parent / "deploy" / "wallex-agent.service").read_text(encoding="utf-8")
    assert "--mode ${WALLEX_MODE}" in unit and "--mode shadow" not in unit.split("ExecStart=")[1]


# ---- shadow realism: real trades fill resting orders
def test_shadow_fills_a_resting_buy_when_a_real_trade_prints_through_it():
    from data.poll import MarketSnapshot
    b = ShadowBroker()
    b.feed({"USDTTMN": MarketSnapshot("USDTTMN", 266_000, 266_040, 1.0, 0, 266_020, 1.0)})
    b.place("d-x-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3.0, 266_000)
    b.feed({"USDTTMN": MarketSnapshot("USDTTMN", 266_000, 266_040, 2.0, 0, 266_000, 2.0)})   # at our price
    assert b.get("d-x-USDTTMN-c").filled_qty == 0
    b.feed({"USDTTMN": MarketSnapshot("USDTTMN", 265_990, 266_040, 3.0, 0, 265_990, 3.0)})   # through it
    assert b.get("d-x-USDTTMN-c").status == "FILLED"


# ---- review fixes
def test_market_urgency_orders_are_repriced_every_ten_seconds():
    ex, x = _exec(lambda p: "market")
    place = ex.place                                      # a gapping book: the protected limit rests
    ex.place = lambda cid, m, side, t, q, px=None: place(cid, m, side, "LIMIT", q, 300_000)
    p = Proposal("USDTTMN", "core", "SELL", 798_000, "stop", "gate", "s1", "market", 3.0)
    x.submit(p, Verdict("allow", 798_000, ()), 0)
    first = len(ex.orders)
    x.tick(11)
    x.tick(22)
    assert len(ex.orders) > first                         # re-placed long before 90 s


def test_one_failing_order_does_not_stop_the_others(monkeypatch):
    ex, x = _exec(lambda p: "aggressive")
    a = Proposal("USDTTMN", "core", "SELL", 798_000, "rebalance_down", "manager", "a1", "passive", 3.0)
    b = Proposal("BTCTMN", "core", "SELL", 798_000, "rebalance_down", "manager", "b1", "passive", 0.00003)
    ex.set_quote("BTCTMN", 27.99e9, 28.01e9, 0)
    x.submit(a, Verdict("allow", 798_000, ()), 0)
    x.submit(b, Verdict("allow", 798_000, ()), 0)
    real, seen = x._step, []

    def step(w, now):
        seen.append(w.market)
        if w.market == "USDTTMN":
            raise RuntimeError("boom")
        return real(w, now)
    monkeypatch.setattr(x, "_step", step)
    x.tick(100)
    assert "BTCTMN" in seen


def test_resize_verdict_is_not_enough_to_cross(tmp_path, monkeypatch):
    import engine.loop as loop_mod
    eng, clock, market, alerts = make_engine(tmp_path)
    p = Proposal("USDTTMN", "core", "BUY", 798_000, "rebalance_up", "manager", "r9", "passive", 3.0)
    monkeypatch.setattr(loop_mod, "check", lambda p, ctx: Verdict("resize", 100_000, ("cap",)))
    assert eng._still_valid(p) is False


def test_adoption_is_retried_after_a_failed_balance_read(tmp_path):
    clock = Clock(NOW)
    end = datetime.fromtimestamp(NOW + 3600 + 30, tz=config.TEHRAN)
    acct = Account(3.0)
    real = acct.balances
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise WallexError("down")
        return real()
    acct.balances = flaky
    eng = Engine(EngineCfg(run_dir=tmp_path / "run", mode="live", sha="t", end_at=end), broker=ShadowBroker(),
                 market=Market(clock), bars=GRIDS.get, clock=clock, alerter=SentAlerts(), account=acct)
    eng.tick()
    clock.t += 60
    eng.tick()                                            # fails once
    clock.t += 10
    eng.tick()                                            # retried
    ev = [e["event"] for e in eng.logs.tail("events", 200)]
    assert "adopt_failed" in ev and "balances_adopted" in ev


def test_permanent_bale_error_does_not_block_the_queue():
    seen = []

    def handler(req):
        text = json.loads(req.content)["text"]
        seen.append(text)
        if text == "bad":
            return httpx.Response(400, json={"ok": False})
        if text == "odd":
            return httpx.Response(200, json=["not", "a", "dict"])
        return httpx.Response(200, json={"ok": True})
    al = Alerter("tok", "1", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    al.send("bad", force=True)
    al.send("odd")
    al.send("good", force=True)
    al.flush(5)
    assert seen.count("bad") == 1 and "good" in al.sent and al._worker.is_alive()


def test_start_failure_alerts_once_per_half_hour(tmp_path):
    from engine import run

    class A:
        sent = []

        def send(self, text, **kw):
            self.sent.append(text)
            return True
    a = A()
    run.alert_start_failure(tmp_path, SystemExit("refusing"), alerter=a)
    run.alert_start_failure(tmp_path, SystemExit("refusing"), alerter=a)
    assert len(a.sent) == 1
