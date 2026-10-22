from decimal import Decimal

import httpx
import pytest

from tests.conftest import FAKE_KEY, fixture
from wallex.client import WallexClient, WallexError, WallexUnavailable

PUBLIC = {
    ("GET", "/v1/markets"): fixture("markets.json"),
    ("GET", "/v1/depth"): fixture("depth_USDTTMN.json"),
    ("GET", "/v1/trades"): fixture("trades_USDTTMN.json"),
    ("GET", "/v1/udf/history"): fixture("udf_USDTTMN_1m.json"),
}


def test_market_rules_from_recorded_markets(make_client):
    c = make_client(PUBLIC)
    r = c.market_rules("USDTTMN")
    assert (r.base, r.quote) == ("USDT", "TMN")
    assert r.step_decimals == 2 and r.tick_decimals == 0
    assert r.min_notional == Decimal(50000)
    assert r.market_orders_enabled is True
    assert c.market_rules("BTCUSDT").tick_decimals == 2


def test_depth_sorted_best_first(make_client):
    d = make_client(PUBLIC).depth("USDTTMN")
    assert d.best_bid < d.best_ask
    assert [p for p, _ in d.bids] == sorted((p for p, _ in d.bids), reverse=True)
    assert [p for p, _ in d.asks] == sorted(p for p, _ in d.asks)


def test_trades_and_candles(make_client):
    c = make_client(PUBLIC)
    assert c.trades("USDTTMN")[0]["symbol"] == "USDTTMN"
    cols = c.candles("USDTTMN", "1", 0, 1)
    assert len(cols["t"]) == len(cols["c"]) > 0
    assert all(isinstance(t, int) for t in cols["t"])
    assert all(b - a == 60 for a, b in zip(cols["t"], cols["t"][1:]))


def test_candles_no_data_is_empty(make_client):
    c = make_client({("GET", "/v1/udf/history"): fixture("udf_no_data.json")})
    assert c.candles("USDTTMN", "1", 0, 1) == {k: [] for k in "tohlcv"}


def test_private_reads(make_client):
    c = make_client({
        ("GET", "/v1/account/balances"): fixture("balances.json"),
        ("GET", "/v1/account/openOrders"): fixture("open_orders_empty.json"),
        ("GET", "/v1/account/orders/d-20261022-0001"): fixture("order_partial.json"),
        ("GET", "/v1/account/fee"): fixture("fees_sample.json"),
    })
    b = c.balances()
    assert b["TMN"].value == Decimal(5_000_000) and b["TMN"].free == Decimal(5_000_000)
    assert c.open_orders() == []
    o = c.order("d-20261022-0001")
    assert o.status == "PARTIALLY_FILLED" and o.active
    assert o.executed_qty == Decimal("1.25") and o.orig_qty == Decimal("3.75")
    assert c.order("d-unknown") is None                 # 404 -> None, not an exception
    f = c.fees()
    assert f["USDTTMN"] == (Decimal("0.0025"), Decimal("0.003"))
    assert "default" not in f and "metaData" not in f


def test_private_call_without_key_makes_no_request(make_client):
    seen = []
    c = make_client({}, key=None, seen=seen)
    with pytest.raises(WallexError):
        c.balances()
    assert seen == []


def test_key_only_in_header_and_never_in_errors(make_client):
    seen = []
    c = make_client({("GET", "/v1/account/balances"): httpx.Response(422, json={"message": "bad", "success": False})},
                    seen=seen)
    with pytest.raises(WallexError) as e:
        c.balances()
    assert FAKE_KEY not in str(e.value)
    req = seen[0]
    assert req.headers["X-API-Key"] == FAKE_KEY
    assert FAKE_KEY not in str(req.url)


def test_public_calls_send_no_key(make_client):
    seen = []
    make_client(PUBLIC, seen=seen).depth("USDTTMN")
    assert "X-API-Key" not in seen[0].headers


def test_retries_5xx_then_succeeds(make_client, clock):
    c = make_client({("GET", "/v1/depth"): [httpx.Response(503), httpx.Response(502), fixture("depth_USDTTMN.json")]})
    assert c.depth("USDTTMN").best_bid > 0
    assert c.requests_made == 3
    assert 0.5 in clock.slept and 1.0 in clock.slept      # exponential backoff


def test_gives_up_after_retries(make_client):
    c = make_client({("GET", "/v1/depth"): httpx.Response(503)})
    with pytest.raises(WallexUnavailable):
        c.depth("USDTTMN")
    assert c.requests_made == 5


def test_429_honours_retry_after(make_client, clock):
    c = make_client({("GET", "/v1/depth"): [httpx.Response(429, headers={"Retry-After": "3"}),
                                             fixture("depth_USDTTMN.json")]})
    c.depth("USDTTMN")
    assert 3.0 in clock.slept


def test_success_false_raises(make_client):
    c = make_client({("GET", "/v1/account/balances"): {"success": False, "message": "denied"}})
    with pytest.raises(WallexError, match="denied"):
        c.balances()


def test_throttle_spaces_requests(make_client, clock):
    c = make_client(PUBLIC)
    c.depth("USDTTMN")
    c.depth("USDTTMN")
    assert any(abs(s - 0.25) < 1e-9 for s in clock.slept)


def test_client_cannot_change_account_state(make_client):
    """C1 contract: read-only. Every request is a GET and no order/cancel/transfer method exists."""
    seen = []
    c = make_client({**PUBLIC,
                     ("GET", "/v1/account/balances"): fixture("balances.json"),
                     ("GET", "/v1/account/openOrders"): fixture("open_orders_empty.json"),
                     ("GET", "/v1/account/fee"): fixture("fees_sample.json")}, seen=seen)
    c.markets(); c.depth("USDTTMN"); c.trades("USDTTMN"); c.candles("USDTTMN", "1", 0, 1)
    c.balances(); c.open_orders(); c.order("x"); c.fees()
    assert {r.method for r in seen} == {"GET"}
    for name in dir(WallexClient):
        assert not any(w in name.lower() for w in ("place", "cancel", "post", "delete", "transfer", "withdraw"))
