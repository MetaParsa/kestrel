"""What the executor needs from an exchange, and an in-memory simulator of it.

`SimExchange` is the single fill model used by tests, the shadow broker and the
backtest broker, so shadow/backtest results describe the same execution logic
that will run live.

Fill rules:
- A limit order that crosses the book when placed (or when the quote moves through
  it) fills immediately at the best opposite price, as a taker.
- A resting limit order fills as a maker at its own price only when the market
  trades *through* it (`trade_through`), never on a touch.
- Market orders fill at the best opposite price, as a taker.
Fees are taken from the received asset (base on BUY, TMN on SELL).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, Decimal
from typing import Literal, Protocol

import config

Status = Literal["NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED"]


class ExchangeTimeout(Exception):
    """The request may or may not have reached the exchange (outcome unknown)."""


class OrderRejected(Exception):
    pass


@dataclass(frozen=True)
class Rules:
    step_decimals: int
    tick_decimals: int
    min_notional: float
    base: str


DEFAULT_RULES = {"USDTTMN": Rules(2, 0, 50_000.0, "USDT"), "BTCTMN": Rules(8, 0, 50_000.0, "BTC")}


@dataclass(frozen=True)
class OrderState:
    client_id: str
    market: str
    side: str
    type: str                    # LIMIT | MARKET
    price: float | None
    qty: float
    filled_qty: float = 0.0
    filled_value: float = 0.0    # Σ qty × price over fills (TMN)
    fee_total: float = 0.0       # in the received asset
    status: Status = "NEW"

    @property
    def active(self) -> bool:
        return self.status in ("NEW", "PARTIALLY_FILLED")

    @property
    def remaining(self) -> float:
        return max(0.0, self.qty - self.filled_qty)


class Exchange(Protocol):
    def rules(self, market: str) -> Rules: ...
    def quote(self, market: str) -> tuple[float, float, float]: ...      # bid, ask, unix ts
    def place(self, client_id: str, market: str, side: str, type: str, qty: float,
              price: float | None) -> OrderState: ...
    def cancel(self, client_id: str) -> OrderState | None: ...
    def get(self, client_id: str) -> OrderState | None: ...


def quantize_qty(qty: float, decimals: int) -> float:
    return float(Decimal(str(qty)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN))


class SimExchange:
    def __init__(self, rules: dict[str, Rules] | None = None, fees: dict[str, tuple[float, float]] | None = None):
        self._rules = rules or DEFAULT_RULES
        self._fees = fees or config.FEES
        self._quotes: dict[str, tuple[float, float, float]] = {}
        self.orders: dict[str, OrderState] = {}
        self.fail_next: list[str] = []     # "timeout_after_accept" | "timeout_before_accept" | "reject"
        self.place_calls = 0
        # Live-exchange realism for tests: a cancel may only be acknowledged (returns None) and
        # take effect later, and a fill can land between our last read and the cancel.
        self.cancel_ack_only = False
        self.fill_before_cancel: float = 0.0     # fraction of remaining filled (maker) just before a cancel
        self.cancel_ignored = 0                  # next N cancels are ignored (order stays active)
        self.cross_on_place = True               # False: the best price vanished as we sent the order

    # ---- market feed --------------------------------------------------------
    def set_quote(self, market: str, bid: float, ask: float, ts: float) -> None:
        self._quotes[market] = (bid, ask, ts)
        for cid, o in list(self.orders.items()):
            if o.market == market and o.active and o.type == "LIMIT":
                if (o.side == "BUY" and ask <= o.price) or (o.side == "SELL" and bid >= o.price):
                    self.orders[cid] = self._fill(o, o.remaining, ask if o.side == "BUY" else bid, taker=True)

    def trade_through(self, market: str, low: float, high: float, fraction: float = 1.0) -> None:
        """Trades printed between low and high: resting limits strictly inside fill as maker."""
        for cid, o in list(self.orders.items()):
            if o.market == market and o.active and o.type == "LIMIT":
                if (o.side == "BUY" and low < o.price) or (o.side == "SELL" and high > o.price):
                    qty = o.remaining if fraction >= 1 else quantize_qty(o.remaining * fraction,
                                                                          self._rules[market].step_decimals)
                    if qty > 0:
                        self.orders[cid] = self._fill(o, qty, o.price, taker=False)

    # ---- Exchange protocol --------------------------------------------------
    def rules(self, market: str) -> Rules:
        return self._rules[market]

    def quote(self, market: str) -> tuple[float, float, float]:
        return self._quotes[market]

    def place(self, client_id, market, side, type, qty, price=None) -> OrderState:
        self.place_calls += 1
        fail = self.fail_next.pop(0) if self.fail_next else None
        if fail == "timeout_before_accept":
            raise ExchangeTimeout("simulated timeout, order not accepted")
        if fail == "reject":
            raise OrderRejected("simulated reject")
        if client_id in self.orders:
            raise OrderRejected(f"duplicate client_id {client_id}")
        r = self._rules[market]
        qty = quantize_qty(qty, r.step_decimals)
        bid, ask, _ = self._quotes[market]
        ref = price if type == "LIMIT" else (ask if side == "BUY" else bid)
        if qty <= 0 or qty * ref < r.min_notional:
            raise OrderRejected("below min notional")
        o = OrderState(client_id, market, side, type, price, qty)
        if type == "MARKET":
            o = self._fill(o, qty, ask if side == "BUY" else bid, taker=True)
        elif self.cross_on_place and ((side == "BUY" and ask <= price) or (side == "SELL" and bid >= price)):
            o = self._fill(o, qty, ask if side == "BUY" else bid, taker=True)
        self.orders[client_id] = o
        if fail == "timeout_after_accept":
            raise ExchangeTimeout("simulated timeout, order accepted")
        return o

    def cancel(self, client_id) -> OrderState | None:
        o = self.orders.get(client_id)
        if o and o.active and self.fill_before_cancel > 0:
            qty = quantize_qty(o.remaining * self.fill_before_cancel, self._rules[o.market].step_decimals)
            if qty > 0:
                o = self._fill(o, qty, o.price, taker=False)
                self.orders[client_id] = o
        if self.cancel_ignored > 0:
            self.cancel_ignored -= 1
            return None
        if o and o.active:
            o = replace(o, status="CANCELED")
            self.orders[client_id] = o
        return None if self.cancel_ack_only else o

    def get(self, client_id) -> OrderState | None:
        return self.orders.get(client_id)

    # ---- internals ----------------------------------------------------------
    def _fill(self, o: OrderState, qty: float, price: float, *, taker: bool) -> OrderState:
        maker_fee, taker_fee = self._fees[o.market]
        rate = taker_fee if taker else maker_fee
        fee = qty * rate if o.side == "BUY" else qty * price * rate
        filled = o.filled_qty + qty
        return replace(o, filled_qty=filled, filled_value=o.filled_value + qty * price,
                       fee_total=o.fee_total + fee,
                       status="FILLED" if filled >= o.qty - 1e-12 else "PARTIALLY_FILLED")
