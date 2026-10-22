from datetime import datetime

import pytest

import config
from engine import core, exits, flatten
from engine.exchange import SimExchange
from engine.executor import Executor
from engine.gate import GateContext, Verdict, check
from engine.modes import BacktestBroker, LiveBroker
from engine.portfolio import Portfolio
from engine.types import Fill, Proposal

T0 = datetime(2026, 10, 22, 10, 0, tzinfo=config.TEHRAN).timestamp()
EQ = 5_000_000.0
PX = {"USDTTMN": 266_000.0, "BTCTMN": 28_000_000_000.0}
OK = lambda size: Verdict("allow", size, ())


# ---- core sleeve ------------------------------------------------------------
def test_regime_defaults_and_bounds():
    t = core.core_targets("devaluation_drift", {}, overrides={"USDTTMN": 0.50, "BTCTMN": -1})
    assert t == {"USDTTMN": 0.25, "BTCTMN": 0.0}
    assert core.core_targets("risk_off", {"USDTTMN": 0.2}) == {"USDTTMN": 0.0, "BTCTMN": 0.0}


def test_risk_multiplier_only_cuts():
    assert core.core_targets("trend_up", {}, risk_multiplier=0.5)["USDTTMN"] == pytest.approx(0.075)
    assert core.core_targets("trend_up", {}, risk_multiplier=3.0)["USDTTMN"] == pytest.approx(0.15)


def test_event_shock_holds_without_adds():
    assert core.core_targets("event_shock", {"USDTTMN": 0.18, "BTCTMN": 0.0}) == {"USDTTMN": 0.18, "BTCTMN": 0.0}


def test_micro_and_hard_flatten_modes():
    assert sum(core.core_targets("trend_up", {}, mode="micro").values()) == pytest.approx(config.LIMITS.micro_max_total)
    assert core.core_targets("trend_up", {}, mode="hard_flatten") == {"USDTTMN": 0.0, "BTCTMN": 0.0}


def test_increase_is_one_10pct_step_per_day():
    p = core.plan_rebalance("USDTTMN", 0.25, 0.0, EQ, increases_today=0, adds_allowed=True, decision_id="x")
    assert p.side == "BUY" and p.size_tmn == pytest.approx(EQ * 0.10)
    assert core.plan_rebalance("USDTTMN", 0.25, 0.10, EQ, increases_today=1, adds_allowed=True, decision_id="x") is None


def test_reductions_are_immediate_and_carry_exact_qty():
    p = core.plan_rebalance("USDTTMN", 0.0, 0.22, EQ, increases_today=1, adds_allowed=False, decision_id="x",
                            held_qty=4.1357, price=266_000)
    assert p.side == "SELL" and p.qty == 4.1357                       # closing → exact held qty
    p2 = core.plan_rebalance("USDTTMN", 0.10, 0.22, EQ, increases_today=0, adds_allowed=True, decision_id="x",
                             held_qty=4.1357, price=266_000)
    assert p2.qty == pytest.approx(EQ * 0.12 / 266_000)


def test_band_prevents_churn():
    assert core.plan_rebalance("USDTTMN", 0.20, 0.19, EQ, increases_today=0, adds_allowed=True, decision_id="x") is None
    assert core.plan_rebalance("USDTTMN", 0.20, 0.21, EQ, increases_today=0, adds_allowed=True, decision_id="x") is None


def test_activity_floor_timing_and_exceptions():
    evening = datetime(2026, 10, 22, 18, 5, tzinfo=config.TEHRAN).timestamp()
    noon = datetime(2026, 10, 22, 12, 0, tzinfo=config.TEHRAN).timestamp()
    kw = dict(trades_today=0, mode="normal", regime="range", daily_stopped=False)
    assert core.floor_due(evening, **kw)
    assert not core.floor_due(noon, **kw)
    assert not core.floor_due(evening, **{**kw, "trades_today": 1})
    assert not core.floor_due(evening, **{**kw, "regime": "risk_off"})
    assert not core.floor_due(evening, **{**kw, "mode": "micro"})
    assert not core.floor_due(evening, **{**kw, "daily_stopped": True})


def test_floor_picks_the_market_furthest_below_target_and_never_churns():
    p = core.floor_proposal({"USDTTMN": 0.20, "BTCTMN": 0.10}, {"USDTTMN": 0.19, "BTCTMN": 0.0}, EQ,
                            increases_today={}, adds_allowed=True, decision_id="f")
    assert p.market == "BTCTMN" and p.kind == "floor" and p.decided_by == "floor"
    assert core.floor_proposal({"USDTTMN": 0.1}, {"USDTTMN": 0.1}, EQ, increases_today={},
                               adds_allowed=True, decision_id="f") is None


# ---- portfolio ----------------------------------------------------------------
def test_portfolio_buy_sell_fees_and_pnl():
    pf = Portfolio(EQ, T0)
    pf.apply_fill(Fill("c1", "USDTTMN", "core", "BUY", 3.0, 266_000, fee=3.0 * 0.0025, fee_asset="USDT", ts=T0))
    p = pf.pos("USDTTMN", "core")
    assert pf.cash == pytest.approx(EQ - 798_000)
    assert p.qty == pytest.approx(3.0 * 0.9975)
    pf.apply_fill(Fill("c2", "USDTTMN", "core", "SELL", p.qty, 270_000, fee=p.qty * 270_000 * 0.0025,
                       fee_asset="TMN", ts=T0 + 60))
    assert pf.qty("USDTTMN") == 0
    assert pf.realized_pnl == pytest.approx(pf.cash - EQ)


def test_counters_change_only_on_accept():
    pf = Portfolio(EQ, T0)
    pf.note_accept(Proposal("USDTTMN", "tactical", "BUY", 1, "entry", "lgbm", "a"), T0)
    pf.note_accept(Proposal("BTCTMN", "core", "BUY", 1, "rebalance_up", "manager", "b"), T0)
    pf.note_accept(Proposal("BTCTMN", "core", "SELL", 1, "stop", "gate", "c", "market", 1.0), T0 + 5)
    assert pf.tactical_entries["USDTTMN"] == 1 and pf.core_increases["BTCTMN"] == 1
    assert pf.entries_today == 2 and pf.activity_today == 2          # a stop is not "activity"
    assert pf.core_stopped_at == {"BTCTMN": T0 + 5}


def test_day_roll_resets_counters_at_tehran_midnight():
    pf = Portfolio(EQ, T0)
    pf.note_accept(Proposal("USDTTMN", "tactical", "BUY", 1, "entry", "lgbm", "a"), T0)
    assert not pf.roll_day(datetime(2026, 10, 22, 23, 59, tzinfo=config.TEHRAN).timestamp(), PX)
    assert pf.roll_day(datetime(2026, 10, 23, 0, 1, tzinfo=config.TEHRAN).timestamp(), PX)
    assert pf.entries_today == 0 and pf.tactical_entries["USDTTMN"] == 0


def test_state_survives_restart(tmp_path):
    pf = Portfolio(EQ, T0)
    pf.apply_fill(Fill("c1", "USDTTMN", "core", "BUY", 3.0, 266_000, 0.0075, "USDT", T0))
    pf.note_accept(Proposal("USDTTMN", "core", "BUY", 1, "rebalance_up", "manager", "a"), T0)
    pf.day_start_equity = 4_990_000
    pf.latch_halt("test")
    pf.core_stopped_at["BTCTMN"] = T0
    pf.save(tmp_path / "state.json")
    back = Portfolio.load(tmp_path / "state.json", T0 + 60)
    assert back.to_dict() == pf.to_dict()
    assert back.halted and back.core_increases["USDTTMN"] == 1 and back.day_start_equity == 4_990_000


# ---- executor + simulated exchange -----------------------------------------------
def make_exec(ex=None, still_valid=lambda p: True):
    ex = ex or SimExchange()
    ex.set_quote("USDTTMN", 265_990, 266_010, T0)
    ex.set_quote("BTCTMN", 27_990_000_000, 28_010_000_000, T0)
    fills, events, accepted = [], [], []
    x = Executor(ex, on_fill=fills.append, on_event=events.append, on_accept=accepted.append, still_valid=still_valid)
    return ex, x, fills, events, accepted


def buy(size=1_000_000, urgency="passive", did="b1", market="USDTTMN", sleeve="core"):
    return Proposal(market, sleeve, "BUY", size, "rebalance_up", "manager", did, urgency)


def sell(qty=3.0, urgency="passive", did="s1", market="USDTTMN", sleeve="core", kind="rebalance_down"):
    return Proposal(market, sleeve, "SELL", qty * 266_000, kind, "manager", did, urgency, qty)


def test_passive_buy_rests_at_bid_then_fills_as_maker():
    ex, x, fills, _, accepted = make_exec()
    cid = x.submit(buy(), OK(1_000_000), T0)
    assert cid == "d-b1-USDTTMN-c" and ex.get(cid).price == 265_990 and ex.get(cid).active
    assert len(accepted) == 1
    ex.trade_through("USDTTMN", 265_980, 266_000)
    x.tick(T0 + 10)
    assert len(fills) == 1 and fills[0].price == 265_990
    assert fills[0].fee == pytest.approx(fills[0].qty * 0.0025)
    assert x.working == {}


def test_touch_is_not_a_fill():
    ex, x, fills, *_ = make_exec()
    x.submit(buy(), OK(1_000_000), T0)
    ex.trade_through("USDTTMN", 265_990, 266_000)
    x.tick(T0 + 10)
    assert fills == []


def test_blocked_verdict_never_reaches_the_exchange():
    ex, x, *_ = make_exec()
    assert x.submit(buy(), Verdict("block", 0, ("G_DAILY_LOSS",)), T0) is None
    assert ex.place_calls == 0


def test_sell_without_qty_is_a_programming_error():
    ex, x, *_ = make_exec()
    with pytest.raises(ValueError):
        x.submit(Proposal("USDTTMN", "core", "SELL", 500_000, "exit", "t", "s"), OK(500_000), T0)


def test_reprices_twice_then_crosses_if_still_valid():
    ex, x, fills, events, _ = make_exec()
    x.submit(buy(), OK(1_000_000), T0)
    for i in (1, 2):
        ex.set_quote("USDTTMN", 266_100 + i, 266_200 + i, T0)
        x.tick(T0 + 90 * i)
    assert [e["event"] for e in events].count("order_escalated") == 2
    ex.set_quote("USDTTMN", 266_300, 266_400, T0)
    x.tick(T0 + 270)
    assert fills and fills[-1].price == 266_400
    assert fills[-1].fee == pytest.approx(fills[-1].qty * 0.0030)
    assert sum(f.qty for f in fills) == pytest.approx(1_000_000 / 265_990, abs=0.01)


def test_unfilled_aggressive_buy_is_cancelled_not_sent_to_market():
    ex, x, fills, events, _ = make_exec()
    ex.cross_on_place = False                            # the ask vanished as we sent the order
    x.submit(buy(urgency="aggressive"), OK(1_000_000), T0)
    ex.set_quote("USDTTMN", 266_500, 266_600, T0)       # market ran away, our limit at 266,010 rests
    x.tick(T0 + 90)
    assert fills == [] and x.working == {}
    assert events[-1]["status"] == "CANCELED_NO_FILL"
    assert all(o.type == "LIMIT" for o in ex.orders.values())


def test_unfilled_aggressive_sell_does_go_to_market():
    ex, x, fills, events, _ = make_exec()
    ex.cross_on_place = False
    x.submit(sell(urgency="aggressive"), OK(798_000), T0)
    ex.cross_on_place = True
    ex.set_quote("USDTTMN", 265_500, 265_600, T0)
    x.tick(T0 + 90)
    assert fills and fills[-1].price == 265_500 and x.working == {}


def test_gives_up_instead_of_crossing_when_decision_expired():
    ex, x, fills, events, _ = make_exec(still_valid=lambda p: False)
    x.submit(buy(), OK(1_000_000), T0)
    for i in (1, 2, 3):
        x.tick(T0 + 90 * i)
    assert fills == [] and x.working == {}
    assert events[-1]["status"] == "CANCELED_NOT_VALID"


def test_cancel_race_fill_is_reported_before_replacing():
    """A fill lands between our read and the cancel, and the cancel only acks: no double buy."""
    ex, x, fills, events, _ = make_exec()
    ex.cancel_ack_only, ex.fill_before_cancel = True, 0.5
    x.submit(buy(), OK(1_000_000), T0)
    x.tick(T0 + 90)                                       # re-price: cancel → half filled meanwhile
    wanted = 1_000_000 / 265_990
    assert sum(f.qty for f in fills) == pytest.approx(wanted / 2, abs=0.01)
    replacement = ex.get("d-b1-USDTTMN-c-r1")
    assert replacement.qty == pytest.approx(wanted / 2, abs=0.02)     # only the rest is re-placed
    ex.fill_before_cancel = 0.0
    ex.trade_through("USDTTMN", 265_000, 266_000)
    x.tick(T0 + 100)
    assert sum(f.qty for f in fills) == pytest.approx(wanted, abs=0.02)


def test_unconfirmed_cancel_keeps_tracking_and_never_stacks_orders():
    ex, x, fills, events, _ = make_exec()
    ex.cancel_ignored = 1
    x.submit(buy(), OK(1_000_000), T0)
    x.tick(T0 + 90)                                       # cancel ignored → still live
    assert "cancel_unconfirmed" in [e["event"] for e in events]
    assert len([o for o in ex.orders.values() if o.active]) == 1 and x.working
    x.tick(T0 + 91)                                       # retry succeeds → replacement placed
    assert len([o for o in ex.orders.values() if o.active]) == 1
    assert ex.get("d-b1-USDTTMN-c-r1") is not None


def test_partial_fill_then_reprice_accounting():
    ex, x, fills, *_ = make_exec()
    x.submit(buy(), OK(1_000_000), T0)
    ex.trade_through("USDTTMN", 265_000, 266_000, fraction=0.3)
    x.tick(T0 + 10)
    x.tick(T0 + 90)                                       # re-price the remaining 70%
    ex.trade_through("USDTTMN", 265_000, 266_000)
    x.tick(T0 + 100)
    assert sum(f.qty for f in fills) == pytest.approx(1_000_000 / 265_990, abs=0.02)
    assert x.working == {}


def test_timeout_after_accept_is_found_by_lookup_not_duplicated():
    ex = SimExchange()
    ex.fail_next = ["timeout_after_accept"]
    ex, x, fills, events, _ = make_exec(ex)
    assert x.submit(buy(), OK(1_000_000), T0) == "d-b1-USDTTMN-c"
    assert ex.place_calls == 1 and len(ex.orders) == 1
    assert any(e["event"] == "order_lookup_before_retry" and e["found"] for e in events)


def test_timeout_before_accept_retries_once_with_same_id():
    ex = SimExchange()
    ex.fail_next = ["timeout_before_accept"]
    ex, x, *_ = make_exec(ex)
    assert x.submit(buy(), OK(1_000_000), T0) == "d-b1-USDTTMN-c"
    assert ex.place_calls == 2 and list(ex.orders) == ["d-b1-USDTTMN-c"]


def test_late_accepted_first_post_is_adopted_on_duplicate_reject():
    class LateAccept(SimExchange):
        def place(self, client_id, *a, **kw):
            if self.place_calls == 0:
                super().place(client_id, *a, **kw)         # accepted …
                raise __import__("engine.exchange", fromlist=["x"]).ExchangeTimeout("lost reply")
            return super().place(client_id, *a, **kw)
        def get(self, cid):                                # … but invisible to the first lookup
            return None if self.place_calls == 1 else super().get(cid)
    ex, x, fills, events, _ = make_exec(LateAccept())
    assert x.submit(buy(), OK(1_000_000), T0) == "d-b1-USDTTMN-c"
    assert "order_adopted_after_duplicate" in [e["event"] for e in events]
    assert len(ex.orders) == 1


def test_same_decision_two_markets_get_distinct_orders():
    ex, x, *_ = make_exec()
    assert x.submit(buy(did="pb1"), OK(1_000_000), T0)
    assert x.submit(buy(did="pb1", market="BTCTMN"), OK(500_000), T0)
    assert len(x.working) == 2 and len(ex.orders) == 2
    assert x.submit(buy(did="pb1"), OK(1_000_000), T0) is None          # true duplicate refused


def test_one_working_order_per_market_sleeve_side_and_urgent_sell_supersedes():
    ex, x, fills, events, _ = make_exec()
    assert x.submit(sell(did="s1"), OK(798_000), T0)
    assert x.submit(sell(did="s2"), OK(798_000), T0) is None           # same urgency: refused
    assert x.submit(sell(did="s3", urgency="market", kind="stop"), OK(798_000), T0)
    assert ex.get("d-s1-USDTTMN-c").status == "CANCELED"
    assert fills and fills[-1].client_id == "d-s3-USDTTMN-c"


def test_self_trade_guard():
    ex, x, fills, events, _ = make_exec()
    assert x.submit(sell(), OK(798_000), T0)
    assert x.submit(buy(did="b2", sleeve="tactical"), OK(500_000), T0) is None
    assert any(e["event"] == "self_trade_guard_refused" for e in events)
    ex2, x2, *_ = make_exec()
    x2.submit(buy(), OK(500_000), T0)
    x2.submit(sell(sleeve="tactical"), OK(798_000), T0)                 # a sell cancels our resting buy
    assert ex2.get("d-b1-USDTTMN-c").status == "CANCELED"


def test_pending_orders_are_reported_for_the_gate():
    ex, x, *_ = make_exec()
    x.submit(buy(), OK(1_000_000), T0)
    x.submit(sell(market="BTCTMN", qty=0.00002), OK(560_000), T0)
    buys, sells = x.pending_tmn()
    assert buys[("USDTTMN", "core")] == pytest.approx(1_000_000, rel=0.001)
    assert sells[("BTCTMN", "core")] == pytest.approx(0.00002 * 27_990_000_000)


def test_market_urgency_fills_now_and_below_min_notional_is_rejected():
    ex, x, fills, events, _ = make_exec()
    x.submit(buy(urgency="market"), OK(1_000_000), T0)
    assert fills and fills[0].price == 266_010
    x.submit(buy(did="tiny"), OK(10_000), T0)
    assert events[-1]["event"] == "order_rejected"


# ---- exits ---------------------------------------------------------------------
def held(market, sleeve, entry, qty, opened=T0, peak=None):
    pf = Portfolio(EQ, T0)
    p = pf.pos(market, sleeve)
    p.qty, p.cost_tmn, p.opened_at, p.peak_price = qty, entry * qty, opened, peak or entry
    return pf


def test_core_stop_at_minus_2_5pct():
    pf = held("USDTTMN", "core", 266_000, 4.0)
    assert exits.check_exits(pf, {"USDTTMN": 266_000 * 0.976}, T0) == []
    (p,) = exits.check_exits(pf, {"USDTTMN": 266_000 * 0.975}, T0)
    assert (p.kind, p.urgency, p.qty) == ("stop", "market", 4.0)


def test_tactical_stop_tp_time_and_trail():
    pf = held("BTCTMN", "tactical", 28e9, 0.00002)
    assert exits.check_exits(pf, {"BTCTMN": 28e9 * 0.9849}, T0)[0].kind == "stop"
    assert exits.check_exits(pf, {"BTCTMN": 28e9 * 0.9851}, T0) == []
    assert exits.check_exits(pf, {"BTCTMN": 28e9 * 1.025}, T0)[0].kind == "take_profit"
    assert exits.check_exits(pf, {"BTCTMN": 28e9 * 1.001}, T0 + 36 * 3600)[0].kind == "time_stop"
    assert exits.check_exits(pf, {"BTCTMN": 28e9 * 1.001}, T0 + 35 * 3600) == []
    trailed = held("BTCTMN", "tactical", 28e9, 0.00002, peak=28e9 * 1.016)
    (p,) = exits.check_exits(trailed, {"BTCTMN": 28e9 * 1.004}, T0)    # fell back under entry +0.5%
    assert p.kind == "stop"


def test_hard_flatten_latches_and_sells_everything_at_market():
    pf = held("USDTTMN", "core", 266_000, 4.0)
    pf.cash = EQ * 0.96 - 4 * 266_000
    bids = {"USDTTMN": 266_000.0}
    assert exits.hard_flatten_due(pf, bids)
    ps = exits.hard_flatten(pf, bids, T0)
    assert pf.halted and [(p.urgency, p.qty) for p in ps] == [("market", 4.0)]


# ---- flatten ---------------------------------------------------------------------
END = datetime(2026, 10, 23, 23, 50, tzinfo=config.TEHRAN)


@pytest.mark.parametrize("hours_left,expected", [
    (8.01, "normal"), (8.0, "no_new"), (3.0, "aggressive"), (1.0, "market"), (0.5, "verify"), (0.0, "ended")])
def test_flatten_phase_boundaries(hours_left, expected):
    assert flatten.phase(END.timestamp() - hours_left * 3600, END) == expected


def test_end_at_must_be_timezone_aware(monkeypatch):
    import importlib
    monkeypatch.setenv("WALLEX_END_AT", "2026-10-23T23:50:00")
    with pytest.raises(ValueError):
        importlib.reload(config)
    monkeypatch.delenv("WALLEX_END_AT")
    importlib.reload(config)


def test_alert_due_two_hours_before_end_with_holdings():
    assert flatten.alert_due(END.timestamp() - 1.5 * 3600, 400_000, END)
    assert not flatten.alert_due(END.timestamp() - 1.5 * 3600, 20_000, END)
    assert not flatten.alert_due(END.timestamp() - 3 * 3600, 400_000, END)


def gate_ctx(pf, bids, now, ex_pending=({}, {})):
    return GateContext(now=now, equity=pf.equity(bids), gain=pf.gain(bids), daily_pnl=pf.daily_pnl(bids),
                       sleeve_value=pf.sleeve_values(bids), adds_allowed=True, core_cap={},
                       pending_buy=ex_pending[0], pending_sell=ex_pending[1])


def test_end_to_end_flatten_leaves_no_coins_without_qty_hints():
    """Proposals come only from flatten_proposals → gate → executor: 0 sellable coins at the end."""
    bt = BacktestBroker()
    start = END.timestamp() - 9 * 3600
    for m, px in PX.items():
        bt.feed_bar(m, start, px, px, px, px, 1.0)
    pf = Portfolio(EQ, start)
    events = []
    x = Executor(bt, on_fill=pf.apply_fill, on_event=events.append, on_accept=lambda p: pf.note_accept(p, start))
    for m, sleeve, size in (("USDTTMN", "core", 1_000_000), ("USDTTMN", "tactical", 600_000),
                            ("BTCTMN", "core", 500_000)):
        x.submit(Proposal(m, sleeve, "BUY", size, "rebalance_up", "test", f"{m}-{sleeve}", "market"), OK(size), start)
    assert pf.qty("USDTTMN") > 0 and pf.qty("BTCTMN") > 0

    t, step = start, 0
    while t < END.timestamp() + 60:
        t += 60
        step += 1
        for m, px in PX.items():
            c = px * (1 + 0.0001 * ((step % 7) - 3))
            bt.feed_bar(m, t, c, c * 1.0003, c * 0.9997, c, 0.0 if step % 3 else 1.0)
        bids = {m: bt.quote(m)[0] for m in PX}
        ph = flatten.phase(t, END)
        for p in flatten.flatten_proposals(pf, bids, ph, t):
            v = check(p, gate_ctx(pf, bids, t, x.pending_tmn()))
            if v.ok:
                x.submit(p, v, t)
        x.tick(t)
    for m in PX:
        assert pf.qty(m) * PX[m] < config.LIMITS.exchange_min_notional_tmn   # only sub-step dust
    assert pf.qty("USDTTMN") < 0.01 * 2
    assert pf.cash > EQ * 0.98
    assert not [e for e in events if e["event"] == "order_rejected"]


def test_live_broker_is_not_wired_yet():
    with pytest.raises(NotImplementedError):
        LiveBroker()
