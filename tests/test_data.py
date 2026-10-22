from decimal import Decimal

import pandas as pd
import pytest

from data import bars, fetch, poll
from tests.conftest import fixture
from wallex.account import AccountSnapshot, dust_assets
from wallex.client import Balance, Depth, Order, WallexError

T0 = 1_792_000_020 // 3600 * 3600  # a whole hour, so 5m/1h buckets line up


def minutes(rows):
    return pd.DataFrame([{"t": T0 + 60 * i, "o": o, "h": h, "l": l, "c": c, "v": v}
                         for i, (o, h, l, c, v) in rows])


def test_minute_grid_fills_missing_and_flags():
    df = minutes([(0, (10, 11, 9, 10, 1)), (1, (10, 10, 10, 10, 0)), (3, (12, 13, 11, 12, 2))])
    g = bars.to_minute_grid(df)
    assert len(g) == 4
    assert list(g["missing"]) == [False, False, True, False]
    assert list(g["traded"]) == [True, False, False, True]
    assert g["c"].iloc[2] == 10 and g["v"].iloc[2] == 0       # forward-filled close, zero volume


def test_resample_5m_ohlcv():
    df = minutes([(i, (100 + i, 101 + i, 99 + i, 100 + i, 1)) for i in range(10)])
    b = bars.resample(bars.to_minute_grid(df), "5min")
    assert len(b) == 2
    first = b.iloc[0]
    assert (first.o, first.h, first.l, first.c, first.v) == (100, 105, 99, 104, 5)
    assert first.traded_minutes == 5 and first.missing_minutes == 0


def test_gaps_detects_long_holes_only():
    rows = [(0, (1, 1, 1, 1, 1)), (2, (1, 1, 1, 1, 1)), (20, (1, 1, 1, 1, 1))]
    g = bars.to_minute_grid(minutes(rows))
    found = bars.gaps(g, min_minutes=5)
    assert len(found) == 1 and found[0][2] == 17


def test_staleness():
    assert not bars.is_stale(100.0, 129.0)
    assert bars.is_stale(100.0, 131.0)


class FakeCandleClient:
    def __init__(self):
        self.calls = 0

    def candles(self, symbol, res, frm, to):
        self.calls += 1
        ts = list(range(frm, to + 1, 60))
        return {"t": ts, "o": [1.0] * len(ts), "h": [1.0] * len(ts), "l": [1.0] * len(ts),
                "c": [1.0] * len(ts), "v": [0.0] * len(ts)}


def test_fetch_history_is_resumable(tmp_path):
    end = T0 + 3 * 86400
    c = FakeCandleClient()
    df = fetch.fetch_history(c, "USDTTMN", 2, end=end, store=tmp_path, log=lambda *_: None)
    first_calls = c.calls
    assert first_calls == 2 and len(df) == 2 * 1440 and df["t"].is_unique and df["t"].is_monotonic_increasing
    df2 = fetch.fetch_history(c, "USDTTMN", 2, end=end, store=tmp_path, log=lambda *_: None)
    assert c.calls == first_calls          # nothing re-downloaded
    assert len(df2) == len(df)


def test_snapshot_from_recorded_depth_and_trades():
    d = fixture("depth_USDTTMN.json")["result"]
    depth = Depth(bids=sorted(((float(x["price"]), float(x["quantity"])) for x in d["bid"]), key=lambda x: -x[0]),
                  asks=sorted((float(x["price"]), float(x["quantity"])) for x in d["ask"]), fetched_at=1.0)
    s = poll.snapshot_from("USDTTMN", depth, fixture("trades_USDTTMN.json")["result"]["latestTrades"])
    assert s.bid < s.ask and 0 < s.spread_bps < 100
    assert -1 <= s.imbalance <= 1
    assert s.last_trade_price > 0 and s.last_trade_at > 1.7e9


def test_snapshot_rejects_empty_book():
    with pytest.raises(WallexError):
        poll.snapshot_from("USDTTMN", Depth(bids=[], asks=[], fetched_at=0.0), [])


def test_poller_counts_errors_without_raising():
    class Broken:
        def depth(self, s): raise WallexError("down")
        def trades(self, s): return []
    p = poll.Poller(Broken(), ["USDTTMN", "BTCTMN"])
    assert p.poll_once() == {}
    assert p.errors_in_window(60) == 2


def _order(cid):
    return Order(client_id=cid, symbol="USDTTMN", side="BUY", type="LIMIT", status="NEW", active=True,
                 price=Decimal(1), orig_qty=Decimal(1), executed_qty=Decimal(0), executed_sum=Decimal(0),
                 executed_price=None)


def test_account_snapshot_helpers():
    snap = AccountSnapshot(
        balances={"TMN": Balance("TMN", Decimal(4_000_000), Decimal(0)),
                  "USDT": Balance("USDT", Decimal("3.5"), Decimal(0)),
                  "BTC": Balance("BTC", Decimal("0.0000001"), Decimal(0))},
        open_orders=[_order("d-1"), _order("manual-123")])
    assert [o.client_id for o in snap.our_open_orders] == ["d-1"]
    assert [o.client_id for o in snap.foreign_open_orders] == ["manual-123"]
    prices = {"USDT": 266_000.0, "BTC": 28_000_000_000.0}
    assert snap.equity_tmn(prices) == Decimal(4_000_000) + Decimal("3.5") * 266000 + Decimal("0.0000001") * 28_000_000_000
    assert set(dust_assets(snap, prices)) == {"BTC"}          # 2,800 TMN < 50,000 minNotional
    with pytest.raises(KeyError):
        snap.equity_tmn({"USDT": 1.0})
