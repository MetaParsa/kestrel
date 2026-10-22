"""Live trading: the Exchange the executor talks to, and the start-up safety routine.

LiveBroker maps Wallex orders onto the executor's OrderState. Assumptions the canary
order (engine/canary.py) and the first real fill must confirm: the order JSON fields
(`executedQty`, `executedSum`, `status`, `active`, `fills[{fee, feeAsset, quantity}]`).
Where Wallex reports no fee, it is ESTIMATED at the taker rate (conservative) and
flagged; the periodic reconcile against real balances catches any drift.

Rules that keep a real order from being lost or doubled:
- missing state is never read as "finished": an unreadable reply leaves the order tracked
  as NEW (from what we sent) until a read-back says otherwise;
- `active=false` always means terminal, whatever `status` says;
- a transient read error returns the last known state, never "missing";
- SELLs are capped at the real free balance of the coin.

live_startup (before the engine starts trading):
  1. cancel every one of OUR resting orders ("d-…"), then re-read open orders and refuse to
     start if any is still there; foreign orders are reported, never touched;
  2. read balances; load state.json and, if it disagrees with the exchange, ADOPT the
     exchange's numbers; with no state, start from balances (TMN only, ≈ 5,000,000).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path

import config
from data.poll import MarketSnapshot
from engine.exchange import ExchangeTimeout, OrderRejected, OrderState, Rules
from engine.portfolio import Portfolio
from engine.reconcile import ReconcileReport, portfolio_from_balances, reconcile
from wallex.account import OUR_ORDER_PREFIX
from wallex.client import Order, WallexClient, WallexError
from wallex.trading import TradeRejected, TradeTimeout, TradingClient

CANCELLED = {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}
LIVE_STATUSES = {"NEW", "PARTIALLY_FILLED"}
START_TOLERANCE_TMN = 50_000.0
LOOKUP_ERRORS_NOTE = 5


class UnknownOrderState(ValueError):
    pass


def to_state(o: Order, fee_rates: tuple[float, float]) -> tuple[OrderState, bool]:
    """(OrderState, fee_estimated). Raises UnknownOrderState if the reply says nothing about the state."""
    raw = o.raw
    has_status, has_active = bool(raw.get("status")), "active" in raw
    if not has_status and not has_active:
        raise UnknownOrderState("order reply has neither status nor active")
    status = o.status.upper()
    qty, filled = float(o.orig_qty), float(o.executed_qty)
    full = filled >= qty - 1e-12 and qty > 0
    if status in CANCELLED:
        status = "FILLED" if full else "CANCELED"
    elif has_active and not raw.get("active"):          # terminal, whatever status says
        status = "FILLED" if full else "CANCELED"
    elif status == "FILLED" or (status not in LIVE_STATUSES and full):
        status = "FILLED"
    else:
        status = "PARTIALLY_FILLED" if filled > 0 else "NEW"
    value = float(o.executed_sum)
    if filled > 0 and value <= 0:                       # no executedSum: rebuild it from a price
        px = float(o.executed_price or 0) or float(o.price or 0)
        value = filled * px
    avg = value / filled if filled > 0 else 0.0
    fee, covered = _reported_fee(raw, filled, o.side, avg)
    uncovered = max(0.0, filled - covered)
    estimated = uncovered > 1e-12
    if estimated:                     # fee not reported for (part of) the fill: estimate it at the taker rate
        taker = fee_rates[1]
        fee += uncovered * taker if o.side == "BUY" else uncovered * avg * taker
    return OrderState(client_id=o.client_id, market=o.symbol, side=o.side, type=o.type,
                      price=float(o.price) if o.price is not None else None, qty=qty, filled_qty=filled,
                      filled_value=value, fee_total=fee, status=status), estimated


def _reported_fee(raw: dict, filled: float, side: str, avg: float) -> tuple[float, float]:
    """(fee in the RECEIVED asset's units, qty that fee covers). Converts a fee reported in the
    other asset (e.g. TMN on a BUY) with the average fill price."""
    def units(v: float, asset) -> float:
        a = str(asset or "").upper()
        if side == "BUY" and a == "TMN":
            return v / avg if avg > 0 else 0.0
        if side == "SELL" and a and a != "TMN":
            return v * avg
        return v
    for key in ("fee", "commission"):
        if raw.get(key) not in (None, ""):
            return units(float(raw[key]), raw.get("feeAsset") or raw.get("fee_asset")), filled
    fee = covered = 0.0
    for f in raw.get("fills") or []:
        v = f.get("fee") or f.get("commission")
        if v not in (None, ""):
            fee += units(float(v), f.get("feeAsset") or f.get("fee_asset"))
            covered += float(f.get("quantity") or f.get("qty") or 0.0)
    return fee, covered


def floor_to(x: float, decimals: int) -> Decimal:
    """Floor to `decimals`, after removing float noise (0.29999999997 → 0.30, not 0.29)."""
    clean = Decimal(str(round(x, decimals + 6)))
    return clean.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)


class LiveBroker:
    def __init__(self, reader: WallexClient, trader: TradingClient, rules: dict[str, Rules] | None = None,
                 fees: dict[str, tuple[float, float]] | None = None, on_note=lambda e: None):
        self.reader, self.trader, self.on_note = reader, trader, on_note
        self.fees = fees or config.FEES
        if rules is None:
            mk = reader.markets()
            rules = {m: Rules(int(mk[m]["stepSize"]), int(mk[m]["tickSize"]), float(mk[m]["minNotional"]),
                              mk[m]["baseAsset"]) for m in config.MARKETS}
        self._rules = rules
        self._quotes: dict[str, tuple[float, float, float]] = {}
        self._last: dict[str, OrderState] = {}
        self._cancel_sent: set[str] = set()
        self._fee_noted: set[str] = set()
        self._lookup_errors: dict[str, int] = {}

    # ---- market feed (from the engine's poller) -----------------------------------------
    def feed(self, snaps: dict[str, MarketSnapshot]) -> None:
        for m, s in snaps.items():
            if m in config.MARKETS:
                self._quotes[m] = (s.bid, s.ask, s.fetched_at)

    def rules(self, market: str) -> Rules:
        return self._rules[market]

    def quote(self, market: str) -> tuple[float, float, float]:
        return self._quotes[market]

    # ---- orders ----------------------------------------------------------------------------
    def place(self, client_id, market, side, type, qty, price=None) -> OrderState:
        r = self._rules[market]
        q = floor_to(qty, r.step_decimals)
        if side == "SELL":                                   # never sell more than we really have
            try:
                bal = self.reader.balances().get(r.base)
            except WallexError as e:
                raise ExchangeTimeout(f"balance check failed: {type(e).__name__}") from e
            free = floor_to(float(bal.free), r.step_decimals) if bal else Decimal(0)
            if free < q:
                self.on_note({"event": "sell_capped_to_balance", "client_id": client_id,
                              "wanted": str(q), "free": str(free)})
                q = free
        px = None
        if type == "LIMIT":
            rounding = ROUND_FLOOR if side == "BUY" else ROUND_CEILING     # never cross by rounding
            px = Decimal(str(price)).quantize(Decimal(1).scaleb(-r.tick_decimals), rounding=rounding)
        ref = float(px) if px is not None else (self._quotes[market][1] if side == "BUY" else self._quotes[market][0])
        if q <= 0 or float(q) * ref < r.min_notional:
            raise OrderRejected("below min notional")
        try:
            res = self.trader.place(symbol=market, side=side, type=type, quantity=format(q, "f"),
                                    price=format(px, "f") if px is not None else None, client_id=client_id)
        except TradeTimeout as e:
            raise ExchangeTimeout(str(e)) from e
        except TradeRejected as e:
            raise OrderRejected(str(e)) from e
        # Accepted. From here the order is REAL: track it even if the reply can't be read.
        sent = OrderState(client_id=client_id, market=market, side=side, type=type,
                          price=float(px) if px is not None else None, qty=float(q))
        self._last[client_id] = sent
        try:
            st, est = to_state(_order_from(res, client_id, sent), self.fees[market]) if res else (sent, False)
        except (UnknownOrderState, KeyError, ValueError, InvalidOperation, TypeError):
            self.on_note({"event": "order_reply_unreadable", "client_id": client_id})
            st, est = sent, False
        self._last[client_id] = st
        if est and client_id not in self._fee_noted:
            self._fee_noted.add(client_id)
            self.on_note({"event": "fee_estimated", "client_id": client_id})
        return st

    def get(self, client_id) -> OrderState | None:
        last = self._last.get(client_id)
        try:
            o = self.reader.order(client_id)
        except (WallexError, KeyError, ValueError, InvalidOperation, TypeError) as e:   # incl. odd JSON
            n = self._lookup_errors[client_id] = self._lookup_errors.get(client_id, 0) + 1
            if n == LOOKUP_ERRORS_NOTE:
                self.on_note({"event": "order_lookup_failing", "client_id": client_id, "error": type(e).__name__})
            return last                         # transient: keep the last known state, never "missing"
        self._lookup_errors.pop(client_id, None)
        if o is None:
            if last is None:
                return None
            if client_id in self._cancel_sent:  # cancelled and already purged by Wallex
                return replace(last, status="FILLED" if last.filled_qty >= last.qty - 1e-12 else "CANCELED")
            return last if last.active else None
        try:
            st, est = to_state(_order_from(o.raw, client_id, last), self.fees.get(o.symbol, config.FEES["USDTTMN"]))
        except (UnknownOrderState, KeyError, ValueError, InvalidOperation, TypeError):
            return last
        if est and client_id not in self._fee_noted:
            self._fee_noted.add(client_id)
            self.on_note({"event": "fee_estimated", "client_id": client_id})
        self._last[client_id] = st
        return st

    def cancel(self, client_id) -> OrderState | None:
        try:
            self.trader.cancel(client_id)
            self._cancel_sent.add(client_id)
        except (TradeTimeout, TradeRejected) as e:
            self.on_note({"event": "cancel_error", "client_id": client_id, "error": str(e)[:200]})
        return self.get(client_id)


def _order_from(res: dict, client_id: str, sent: OrderState | None) -> Order:
    """Parse an order reply, filling gaps from what we sent (symbol/side/type/qty)."""
    from wallex.client import _parse_order
    data = dict(res)
    if sent is not None:
        data.setdefault("symbol", sent.market)
        data.setdefault("side", sent.side)
        data.setdefault("type", sent.type)
        data.setdefault("origQty", str(sent.qty))
        if sent.price is not None:
            data.setdefault("price", str(sent.price))
    o = _parse_order(data)
    return o if o.client_id else replace(o, client_id=client_id)


# ---- start-up -------------------------------------------------------------------------------
@dataclass
class StartupReport:
    cancelled: list[str] = field(default_factory=list)
    foreign_orders: list[str] = field(default_factory=list)
    adopted: dict = field(default_factory=dict)
    fresh_start: bool = False
    reconcile: ReconcileReport | None = None


def live_startup(reader: WallexClient, broker: LiveBroker, state_path: Path, bids: dict[str, float],
                 now: float | None = None, wait_s: float = 2.0) -> tuple[Portfolio, StartupReport]:
    now = now or time.time()
    rep = StartupReport()
    for o in reader.open_orders():
        if o.client_id.startswith(OUR_ORDER_PREFIX):
            broker.cancel(o.client_id)
            rep.cancelled.append(o.client_id)
        else:
            rep.foreign_orders.append(o.client_id or "(no client id)")
    if rep.cancelled:
        time.sleep(wait_s)
        still = [o.client_id for o in reader.open_orders() if o.client_id.startswith(OUR_ORDER_PREFIX)]
        if still:
            raise RuntimeError(f"our orders still resting after cancel {still}; refusing to trade")
    balances = reader.balances()
    if state_path.exists():
        pf = Portfolio.load(state_path, now)
    else:
        pf = portfolio_from_balances(balances, now)
        if abs(pf.cash - config.START_EQUITY_TMN) > START_TOLERANCE_TMN:
            raise RuntimeError(f"fresh start but the account holds {pf.cash:,.0f} TMN, not "
                               f"{config.START_EQUITY_TMN:,.0f} — is the key on the right (prop) account?")
        rep.fresh_start = True
    rec = reconcile(pf, balances, [], bids, set())
    if rec.asset_diffs:
        rep.adopted = adopt_balances(pf, balances, bids, now)
        rec = reconcile(pf, balances, [], bids, set())
    rep.reconcile = rec
    pf.save(state_path)
    return pf, rep


def adopt_balances(pf: Portfolio, balances: dict, bids: dict[str, float], now: float) -> dict:
    """Make our books match the exchange. Tactical holdings are kept up to what exists; the rest
    is core. New coins are costed at the current bid (conservative for stops)."""
    changes = {"TMN": {"from": pf.cash, "to": float(balances["TMN"].value) if "TMN" in balances else 0.0}}
    pf.cash = changes["TMN"]["to"]
    for m, base in config.BASE_ASSET.items():
        ex_qty = float(balances[base].value) if base in balances else 0.0
        before = pf.qty(m)
        tac = pf.pos(m, "tactical")
        tac_keep = min(tac.qty, ex_qty)
        if tac_keep < tac.qty:
            tac.cost_tmn *= tac_keep / tac.qty if tac.qty else 0.0
            tac.qty = tac_keep
        core = pf.pos(m, "core")
        want = ex_qty - tac_keep
        if want > core.qty:
            core.cost_tmn += (want - core.qty) * bids[m]
            core.opened_at = core.opened_at or now
            core.peak_price = core.peak_price or bids[m]
        elif want < core.qty:
            core.cost_tmn = core.avg_price * want
        core.qty = want
        if abs(before - ex_qty) > 1e-12:
            changes[base] = {"from": before, "to": ex_qty}
    return changes
