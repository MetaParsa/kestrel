"""C5: LiveBroker and the go-live safety routine against a fake Wallex (no network, no real orders)."""
import json
from datetime import datetime
from decimal import Decimal

import httpx
import pytest

import config
from data.poll import MarketSnapshot
from engine.executor import Executor
from engine.gate import Verdict
from engine.live import LiveBroker, adopt_balances, live_startup
from engine.loop import Engine, EngineCfg
from engine.portfolio import Portfolio
from engine.types import Proposal
from tests.test_loop import GRIDS, Clock, Market, SentAlerts
from wallex.client import WallexClient
from wallex.trading import TradingClient, TradingDisarmed

NOW = datetime(2026, 10, 22, 13, 0, tzinfo=config.TEHRAN).timestamp()
RULES_JSON = {"USDTTMN": {"stepSize": 2, "tickSize": 0, "minNotional": 50000, "baseAsset": "USDT"},
              "BTCTMN": {"stepSize": 8, "tickSize": 0, "minNotional": 50000, "baseAsset": "BTC"}}


class FakeWallex:
    """Just enough of api.wallex.ir: markets, balances, orders (place/get/cancel/open)."""

    def __init__(self):
        self.balances = {"TMN": "5000000"}
        self.orders: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.fail_post: list[str] = []          # "timeout" | "500" | "accept_then_timeout" | "garbage"
        self.order_reply_body = True
        self.reply_override: dict | None = None  # replace the POST reply's order body
        self.ignore_cancel = False

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        p, m = req.url.path, req.method
        if p == "/v1/markets":
            return httpx.Response(200, json={"result": {"symbols": RULES_JSON}, "success": True})
        if p == "/v1/account/balances":
            return httpx.Response(200, json={"result": {"balances": {
                a: {"asset": a, "value": v, "locked": "0"} for a, v in self.balances.items()}}, "success": True})
        if p == "/v1/account/openOrders":
            return httpx.Response(200, json={"result": {"orders": [o for o in self.orders.values() if o["active"]]},
                                             "success": True})
        if p == "/v1/account/orders" and m == "DELETE":
            cid = req.url.params.get("clientOrderId")
            o = self.orders.get(cid) if cid else None
            if o is None or self.ignore_cancel:
                return httpx.Response(404 if o is None else 200, json={"message": "Not Found", "success": o is not None})
            if o["active"]:
                o.update(status="CANCELED", active=False)
            return httpx.Response(200, json={"result": o, "success": True})
        if p == "/v1/account/orders" and m == "POST":
            body = json.loads(req.content)
            fail = self.fail_post.pop(0) if self.fail_post else None
            if fail == "timeout":
                raise httpx.ReadTimeout("slow")
            if fail == "500":
                return httpx.Response(502)
            cid = body["client_id"]
            if cid in self.orders:
                return httpx.Response(400, json={"message": "duplicate client id", "success": False})
            self.orders[cid] = {"symbol": body["symbol"], "type": body["type"], "side": body["side"],
                                "clientOrderId": cid, "price": body.get("price"), "origQty": body["quantity"],
                                "executedQty": "0", "executedSum": "0", "executedPrice": "0",
                                "status": "NEW", "active": True, "fills": []}
            if fail == "accept_then_timeout":
                raise httpx.ReadTimeout("reply lost")
            if fail == "garbage":
                return httpx.Response(200, content=b"<html>oops</html>")
            reply = self.reply_override if self.reply_override is not None else self.orders[cid]
            return httpx.Response(200, json={"result": reply if self.order_reply_body else {}, "success": True})
        if p.startswith("/v1/account/orders/"):
            cid = p.rsplit("/", 1)[1]
            o = self.orders.get(cid)
            if o is None:
                return httpx.Response(404, json={"message": "Not Found", "success": False})
            if m == "DELETE":
                if o["active"]:
                    o.update(status="CANCELED", active=False)
                return httpx.Response(200, json={"result": o, "success": True})
            return httpx.Response(200, json={"result": o, "success": True})
        return httpx.Response(404, json={"message": "Not Found", "success": False})

    def fill(self, cid: str, qty: float, price: float, fee: float | None = None):
        o = self.orders[cid]
        done = float(o["executedQty"]) + qty
        o["executedQty"] = str(done)
        o["executedSum"] = str(float(o["executedSum"]) + qty * price)
        if fee is not None:
            o["fills"].append({"price": str(price), "quantity": str(qty), "fee": str(fee)})
        full = done >= float(o["origQty"]) - 1e-12
        o.update(status="FILLED" if full else "PARTIALLY_FILLED", active=not full)


def make(fake: FakeWallex, armed=True):
    t = httpx.MockTransport(fake.handler)
    reader = WallexClient(api_key="k-test", transport=t, sleep=lambda s: None, min_interval=0)
    trader = TradingClient("k-test", armed=armed, transport=t)
    broker = LiveBroker(reader, trader)
    broker.feed({"USDTTMN": MarketSnapshot("USDTTMN", 266_000, 266_040, NOW, 0, 266_000, NOW),
                 "BTCTMN": MarketSnapshot("BTCTMN", 28e9, 28.01e9, NOW, 0, 28e9, NOW)})
    return reader, trader, broker


def buy(did="b1", urgency="passive"):
    return Proposal("USDTTMN", "core", "BUY", 1_000_000, "rebalance_up", "manager", did, urgency)


# ---- the two locks ---------------------------------------------------------------------
def test_disarmed_client_sends_nothing():
    fake = FakeWallex()
    t = TradingClient("k-test", armed=False, transport=httpx.MockTransport(fake.handler))
    with pytest.raises(TradingDisarmed):
        t.place(symbol="USDTTMN", side="BUY", type="LIMIT", quantity="1", price="1", client_id="x")
    assert fake.requests == []


def test_env_lock_is_required(monkeypatch):
    monkeypatch.delenv("WALLEX_LIVE_ARMED", raising=False)
    monkeypatch.setenv("WALLEX_API_KEY", "k-test")
    assert TradingClient.from_env(armed=True).armed is False
    monkeypatch.setenv("WALLEX_LIVE_ARMED", "yes")                    # wrong phrase
    assert TradingClient.from_env(armed=True).armed is False
    monkeypatch.setenv("WALLEX_LIVE_ARMED", "yes-real-money")
    assert TradingClient.from_env(armed=True).armed is True
    assert TradingClient.from_env(armed=False).armed is False


def test_live_mode_refuses_to_start_unarmed(monkeypatch, tmp_path):
    from engine import run
    monkeypatch.delenv("WALLEX_LIVE_ARMED", raising=False)
    with pytest.raises(SystemExit, match="WALLEX_LIVE_ARMED"):
        run.start_live(EngineCfg(run_dir=tmp_path), None, None, None, None)


# ---- LiveBroker ---------------------------------------------------------------------------
def test_place_sends_quantised_limit_with_client_id():
    fake = FakeWallex()
    _, _, broker = make(fake)
    st = broker.place("d-b1-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3.7591234, 266_000.7)
    body = json.loads(fake.requests[-1].content)
    assert body == {"symbol": "USDTTMN", "side": "BUY", "type": "LIMIT", "quantity": "3.75",
                    "price": "266000", "client_id": "d-b1-USDTTMN-c"}       # qty floored, buy price floored
    assert fake.requests[-1].headers["X-API-Key"] == "k-test"
    assert st.status == "NEW" and st.active


def test_sell_price_rounds_up_and_min_notional_checked_locally():
    fake = FakeWallex()
    fake.balances = {"TMN": "5000000", "USDT": "5"}
    _, _, broker = make(fake)
    broker.place("d-s1-USDTTMN-c", "USDTTMN", "SELL", "LIMIT", 1.0, 266_000.2)
    assert json.loads(fake.requests[-1].content)["price"] == "266001"
    n = len(fake.requests)
    from engine.exchange import OrderRejected
    with pytest.raises(OrderRejected):
        broker.place("d-t-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 0.1, 266_000)
    assert len(fake.requests) == n                                           # never sent


def test_unknown_outcomes_become_exchange_timeouts():
    from engine.exchange import ExchangeTimeout, OrderRejected
    fake = FakeWallex()
    _, _, broker = make(fake)
    fake.fail_post = ["timeout", "500"]
    with pytest.raises(ExchangeTimeout):
        broker.place("d-a-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)
    with pytest.raises(ExchangeTimeout):
        broker.place("d-b-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)
    broker.place("d-c-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)
    with pytest.raises(OrderRejected):                                       # duplicate id is a 4xx
        broker.place("d-c-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)


def test_executor_adopts_a_late_accepted_order_without_duplicating():
    fake = FakeWallex()
    _, _, broker = make(fake)
    fake.fail_post = ["accept_then_timeout"]
    fills, events = [], []
    x = Executor(broker, on_fill=fills.append, on_event=events.append)
    assert x.submit(buy(), Verdict("allow", 1_000_000, ()), NOW) == "d-b1-USDTTMN-c"
    posts = [r for r in fake.requests if r.method == "POST"]
    assert len(posts) == 1 and len(fake.orders) == 1
    assert any(e["event"] == "order_lookup_before_retry" and e["found"] for e in events)


def test_fills_flow_through_with_reported_or_estimated_fees():
    fake = FakeWallex()
    _, _, broker = make(fake)
    notes = []
    broker.on_note = notes.append
    fills = []
    x = Executor(broker, on_fill=fills.append)
    x.submit(buy(), Verdict("allow", 1_000_000, ()), NOW)
    cid = "d-b1-USDTTMN-c"
    fake.fill(cid, 1.5, 266_000, fee=0.00375)              # reported fee
    x.tick(NOW + 10)
    fake.fill(cid, 2.25, 266_000)                          # no fee reported → estimated at taker
    x.tick(NOW + 20)
    assert [round(f.qty, 4) for f in fills] == [1.5, 2.25]
    assert fills[0].fee == pytest.approx(0.00375)
    assert fills[1].fee == pytest.approx(2.25 * 0.003)
    assert x.working == {}


def test_transient_read_errors_never_make_an_order_disappear():
    fake = FakeWallex()
    reader, _, broker = make(fake)
    broker.place("d-z-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)
    orig = fake.handler

    def flaky(req):
        if req.url.path.startswith("/v1/account/orders/") and req.method == "GET":
            return httpx.Response(503)
        return orig(req)
    reader._http._transport = httpx.MockTransport(flaky)
    st = broker.get("d-z-USDTTMN-c")
    assert st is not None and st.active                   # last known state, not "missing"


def test_cancel_confirms_with_a_read_back():
    fake = FakeWallex()
    _, _, broker = make(fake)
    broker.place("d-y-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)
    st = broker.cancel("d-y-USDTTMN-c")
    assert st.status == "CANCELED" and not st.active
    delete, get = fake.requests[-2:]
    assert (delete.method, get.method) == ("DELETE", "GET")
    assert delete.url.path == "/v1/account/orders" and delete.url.params["clientOrderId"] == "d-y-USDTTMN-c"


# ---- go-live start-up ----------------------------------------------------------------------
def test_startup_cancels_our_orders_keeps_foreign_and_starts_from_balances(tmp_path):
    fake = FakeWallex()
    reader, _, broker = make(fake)
    broker.place("d-old-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)
    fake.orders["manual-1"] = {"symbol": "BTCTMN", "type": "LIMIT", "side": "BUY", "clientOrderId": "manual-1",
                               "price": "1", "origQty": "1", "executedQty": "0", "executedSum": "0",
                               "status": "NEW", "active": True}
    pf, rep = live_startup(reader, broker, tmp_path / "state.json", {"USDTTMN": 266_000, "BTCTMN": 28e9},
                           now=NOW, wait_s=0)
    assert rep.cancelled == ["d-old-USDTTMN-c"] and rep.foreign_orders == ["manual-1"]
    assert fake.orders["manual-1"]["active"] and not fake.orders["d-old-USDTTMN-c"]["active"]
    assert rep.fresh_start and pf.cash == 5_000_000 and pf.start_equity == config.START_EQUITY_TMN
    assert (tmp_path / "state.json").exists()


def test_startup_adopts_the_exchange_when_books_disagree(tmp_path):
    fake = FakeWallex()
    fake.balances = {"TMN": "4200000", "USDT": "3.0"}       # a fill happened while the engine was down
    reader, _, broker = make(fake)
    Portfolio(5_000_000, NOW).save(tmp_path / "state.json")
    pf, rep = live_startup(reader, broker, tmp_path / "state.json", {"USDTTMN": 266_000, "BTCTMN": 28e9},
                           now=NOW, wait_s=0)
    assert pf.cash == 4_200_000 and pf.pos("USDTTMN", "core").qty == 3.0
    assert pf.pos("USDTTMN", "core").avg_price == 266_000 and "USDT" in rep.adopted
    assert rep.reconcile.ok


def test_fresh_start_refuses_an_account_holding_coins(tmp_path):
    fake = FakeWallex()
    fake.balances = {"TMN": "4000000", "BTC": "0.01"}
    reader, _, broker = make(fake)
    with pytest.raises(RuntimeError, match="non-TMN"):
        live_startup(reader, broker, tmp_path / "state.json", {"USDTTMN": 266_000, "BTCTMN": 28e9}, now=NOW)


def test_adopt_keeps_tactical_up_to_what_exists():
    pf = Portfolio(1_000_000, NOW)
    t = pf.pos("BTCTMN", "tactical")
    t.qty, t.cost_tmn = 0.00002, 560_000
    from wallex.client import Balance
    adopt_balances(pf, {"TMN": Balance("TMN", Decimal(1_000_000), Decimal(0)),
                        "BTC": Balance("BTC", Decimal("0.00003"), Decimal(0))}, {"USDTTMN": 266_000, "BTCTMN": 28e9}, NOW)
    assert pf.pos("BTCTMN", "tactical").qty == 0.00002
    assert pf.pos("BTCTMN", "core").qty == pytest.approx(0.00001)


def test_shadow_state_is_archived_before_live(tmp_path):
    from engine import run
    Portfolio(4_000_000, NOW).save(tmp_path / "state.json")
    (tmp_path / "state.mode").write_text("shadow", encoding="utf-8")
    dest = run.archive_shadow_state(tmp_path)
    assert dest is not None and not (tmp_path / "state.json").exists()
    Portfolio(5_000_000, NOW).save(tmp_path / "state.json")
    (tmp_path / "state.mode").write_text("live", encoding="utf-8")
    assert run.archive_shadow_state(tmp_path) is None and (tmp_path / "state.json").exists()


# ---- reconcile in the loop -------------------------------------------------------------------
class Account:
    def __init__(self, tmn="5000000", usdt=None):
        from wallex.client import Balance
        self.b = {"TMN": Balance("TMN", Decimal(tmn), Decimal(0))}
        if usdt:
            self.b["USDT"] = Balance("USDT", Decimal(usdt), Decimal(0))

    def balances(self):
        return self.b

    def open_orders(self):
        return []


def test_engine_reconciles_at_t_minus_30_and_at_the_end(tmp_path):
    from engine.modes import ShadowBroker
    clock = Clock(NOW)
    alerts = SentAlerts()
    end = datetime.fromtimestamp(NOW + 3600, tz=config.TEHRAN)
    eng = Engine(EngineCfg(run_dir=tmp_path / "run", mode="live", sha="t", end_at=end), broker=ShadowBroker(),
                 market=Market(clock), bars=GRIDS.get, clock=clock, alerter=alerts, account=Account())
    while clock.t < NOW + 3600 + 30:
        eng.tick()
        clock.t += 60
    recs = [e for e in eng.logs.tail("events", 500) if e["event"] == "reconcile"]
    assert [r["final"] for r in recs][-2:] == [False, True]
    assert any(t.startswith("🏁") for t in alerts.texts)


def test_periodic_reconcile_alerts_on_mismatch(tmp_path):
    from engine.modes import ShadowBroker
    clock = Clock(NOW)
    alerts = SentAlerts()
    end = datetime.fromtimestamp(NOW + 72 * 3600, tz=config.TEHRAN)
    eng = Engine(EngineCfg(run_dir=tmp_path / "run", mode="live", sha="t", end_at=end), broker=ShadowBroker(),
                 market=Market(clock), bars=GRIDS.get, clock=clock, alerter=alerts, account=Account(usdt="2"))
    eng.tick()
    clock.t += 31 * 60
    eng.tick()
    assert any("⚠️ تطبیق حساب" in t for t in alerts.texts)


# ---- C5 review follow-ups ---------------------------------------------------------------------
from engine.exchange import ExchangeTimeout as _ExTimeout   # noqa: E402
from engine.live import floor_to, to_state                  # noqa: E402
from wallex.client import _parse_order                      # noqa: E402


def _o(**kw):
    base = {"symbol": "USDTTMN", "side": "BUY", "type": "LIMIT", "clientOrderId": "c", "price": "266000",
            "origQty": "3", "executedQty": "0", "executedSum": "0"}
    base.update(kw)
    return _parse_order(base)


def test_inactive_partial_fill_is_terminal_not_live():
    st, _ = to_state(_o(status="PARTIALLY_FILLED", active=False, executedQty="1", executedSum="266000"),
                     (0.0025, 0.003))
    assert st.status == "CANCELED" and not st.active and st.filled_qty == 1


def test_missing_status_and_active_is_unknown_not_cancelled():
    with pytest.raises(ValueError):
        to_state(_o(), (0.0025, 0.003))


def test_reply_without_state_keeps_the_order_tracked_as_new():
    fake = FakeWallex()
    _, _, broker = make(fake)
    fake.reply_override = {"clientOrderId": "d-q-USDTTMN-c"}           # no status, no active
    st = broker.place("d-q-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)
    assert st.status == "NEW" and st.active
    assert broker.get("d-q-USDTTMN-c").status == "NEW"                 # read-back from the real record


def test_empty_reply_is_tracked_and_never_duplicated():
    fake = FakeWallex()
    _, _, broker = make(fake)
    fake.order_reply_body = False
    events = []
    x = Executor(broker, on_fill=lambda f: None, on_event=events.append)
    assert x.submit(buy("e1"), Verdict("allow", 1_000_000, ()), NOW) == "d-e1-USDTTMN-c"
    assert len([r for r in fake.requests if r.method == "POST"]) == 1 and x.working


def test_executed_sum_missing_uses_price():
    st, _ = to_state(_o(status="FILLED", active=False, executedQty="3", executedSum="0", executedPrice="266100"),
                     (0.0025, 0.003))
    assert st.filled_value == pytest.approx(3 * 266_100)


def test_fee_reported_in_tmn_on_a_buy_is_converted_to_coin_units():
    st, est = to_state(_o(status="FILLED", active=False, executedQty="3", executedSum="798000",
                          fills=[{"quantity": "3", "fee": "1995", "feeAsset": "TMN"}]), (0.0025, 0.003))
    assert not est and st.fee_total == pytest.approx(1995 / 266_000)


def test_404_after_our_cancel_means_cancelled():
    fake = FakeWallex()
    _, _, broker = make(fake)
    broker.place("d-g-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)
    broker.cancel("d-g-USDTTMN-c")
    del fake.orders["d-g-USDTTMN-c"]                                     # Wallex purged it
    st = broker.get("d-g-USDTTMN-c")
    assert st.status == "CANCELED" and not st.active


def test_sell_is_capped_to_the_real_balance():
    fake = FakeWallex()
    fake.balances = {"TMN": "1000000", "USDT": "2.99"}
    _, _, broker = make(fake)
    notes = []
    broker.on_note = notes.append
    broker.place("d-s9-USDTTMN-c", "USDTTMN", "SELL", "LIMIT", 3.0, 266_100)
    sent = json.loads([r for r in fake.requests if r.method == "POST"][-1].content)
    assert sent["quantity"] == "2.99" and notes[0]["event"] == "sell_capped_to_balance"


def test_unreadable_200_is_an_unknown_outcome():
    fake = FakeWallex()
    _, _, broker = make(fake)
    fake.fail_post = ["garbage"]
    with pytest.raises(_ExTimeout):
        broker.place("d-h-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)


def test_startup_refuses_if_our_orders_wont_cancel(tmp_path):
    fake = FakeWallex()
    reader, _, broker = make(fake)
    broker.place("d-stuck-USDTTMN-c", "USDTTMN", "BUY", "LIMIT", 3, 266_000)
    fake.ignore_cancel = True
    with pytest.raises(RuntimeError, match="still resting"):
        live_startup(reader, broker, tmp_path / "state.json", {"USDTTMN": 266_000, "BTCTMN": 28e9}, now=NOW, wait_s=0)


def test_fresh_start_refuses_a_balance_that_is_not_the_contest_capital(tmp_path):
    fake = FakeWallex()
    fake.balances = {"TMN": "1234567"}
    reader, _, broker = make(fake)
    with pytest.raises(RuntimeError, match=r"right \(prop\) account"):
        live_startup(reader, broker, tmp_path / "state.json", {"USDTTMN": 266_000, "BTCTMN": 28e9}, now=NOW)


def test_float_noise_does_not_lose_a_step():
    assert floor_to(0.29999999997, 2) == Decimal("0.30")
    assert floor_to(0.2949, 2) == Decimal("0.29")


def test_reconcile_error_at_verify_never_blocks_the_flatten(tmp_path):
    from engine.modes import ShadowBroker

    class Broken(Account):
        def balances(self):
            raise ConnectionError("wallex down")
    clock = Clock(NOW)
    end = datetime.fromtimestamp(NOW + 40 * 60, tz=config.TEHRAN)        # verify starts in 10 min
    eng = Engine(EngineCfg(run_dir=tmp_path / "run", mode="live", sha="t", end_at=end), broker=ShadowBroker(),
                 market=Market(clock), bars=GRIDS.get, clock=clock, alerter=SentAlerts(), account=Broken())
    for _ in range(15):
        eng.tick()
        clock.t += 60
    ev = [e["event"] for e in eng.logs.tail("events", 500)]
    assert "reconcile_failed" in ev and eng.last_phase == "verify"
    assert float((tmp_path / "run" / "heartbeat").read_text()) >= NOW + 13 * 60
