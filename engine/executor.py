"""Turns gate-approved proposals into orders and fills.

- passive: limit at our side of the book; re-price up to `max_reprices` times every
  `reprice_after_s`; then cross the spread only if the decision is still valid.
- aggressive: limit at the opposite best price. If still unfilled after one interval,
  risk-REDUCING orders go to market; risk-INCREASING orders are cancelled (never a
  blind market buy).
- market: market order (stops, time stops, final flatten).

Live-exchange safety:
- A cancel is only trusted once `get()` shows the order terminal. Fills that land
  between our last read and the cancel are reported before anything is re-placed; an
  unconfirmed cancel keeps the order tracked and is retried next tick.
- A POST that times out is looked up by client_id before any retry; a retry reuses
  the same client_id, and a "duplicate" rejection on the retry adopts the live order.
- One working order per (market, sleeve, side); a more urgent SELL replaces a less
  urgent one, anything else is refused. Self-trade guard: a buy is refused while one
  of our sells rests on the market; a sell first cancels our resting buys there.
- Every order needs an approving gate Verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import config
from engine.exchange import Exchange, ExchangeTimeout, OrderRejected, OrderState
from engine.gate import Verdict
from engine.types import URGENCY_RANK, Fill, Proposal


@dataclass
class Working:
    p: Proposal
    key: str                       # stable id for this working order (decision + market + sleeve)
    qty: float                     # base qty wanted in total
    urgency: str
    client_id: str = ""
    placed_at: float = 0.0
    reprices: int = 0
    sub: int = 0                   # suffix counter for replacement orders
    filled_before: float = 0.0     # banked fills of earlier (replaced) orders
    seen_qty: float = 0.0          # already-reported fills of the current order
    seen_value: float = 0.0
    seen_fee: float = 0.0
    after_cancel: tuple[str, str] | None = None   # ("replace", urgency) | ("finish", status) once a cancel is sent
    price: float | None = None

    @property
    def market(self) -> str:
        return self.p.market

    @property
    def side(self) -> str:
        return self.p.side

    @property
    def remaining(self) -> float:
        return max(0.0, self.qty - self.filled_before - self.seen_qty)


class Executor:
    def __init__(self, ex: Exchange, *, on_fill: Callable[[Fill], None],
                 on_event: Callable[[dict], None] = lambda e: None,
                 on_accept: Callable[[Proposal], None] = lambda p: None,
                 still_valid: Callable[[Proposal], bool] = lambda p: True,
                 urgency_cap: Callable[[Proposal], str] = lambda p: "market",
                 cfg: config.ExecutionCfg = config.EXECUTION):
        """`urgency_cap(p)`: the most aggressive way this order may be escalated to (e.g. a take-profit
        stays passive; the final sell-off follows its phase). Never lower than the order's own urgency."""
        self.ex, self.on_fill, self.on_event = ex, on_fill, on_event
        self.on_accept, self.still_valid, self.cfg = on_accept, still_valid, cfg
        self.urgency_cap = urgency_cap
        self.working: dict[str, Working] = {}

    # ---- public -------------------------------------------------------------
    def submit(self, p: Proposal, verdict: Verdict, now: float) -> str | None:
        if not verdict.ok:
            self._event(now, "submit_refused_by_gate", p, rules=list(verdict.rules))
            return None
        if p.side == "SELL" and not p.qty:
            raise ValueError("SELL proposals must carry the exact base qty to sell")
        key = self.key_of(p)
        if key in self.working:
            self._event(now, "submit_refused_duplicate_decision", p, key=key)
            return None
        for w in list(self.working.values()):
            if w.market != p.market:
                continue
            if w.side == p.side and w.p.sleeve == p.sleeve:
                if p.side == "SELL" and URGENCY_RANK[p.urgency] > URGENCY_RANK[w.urgency]:
                    if not self._cancel_now(w, now, ("finish", "CANCELED_SUPERSEDED")):
                        return None
                else:
                    self._event(now, "submit_refused_pending_order", p, other=w.client_id)
                    return None
            elif w.side != p.side:
                if p.side == "BUY":
                    self._event(now, "self_trade_guard_refused", p, other=w.client_id)
                    return None
                if not self._cancel_now(w, now, ("finish", "CANCELED_SELF_TRADE_GUARD")):
                    return None
        bid, ask, _ = self.ex.quote(p.market)
        if p.side == "SELL":
            qty = p.qty
        else:
            qty = verdict.size_tmn / (ask if p.urgency != "passive" else bid)
        w = Working(p=p, key=key, qty=qty, urgency=p.urgency)
        self.working[key] = w
        if not self._place_current(w, now):
            self.working.pop(key, None)
            return None
        self.on_accept(p)
        return w.client_id

    def tick(self, now: float) -> None:
        for w in list(self.working.values()):
            try:                                            # one bad order never starves the others
                self._step(w, now)
            except Exception as e:
                self._event(now, "order_step_error", w.p, client_id=w.client_id,
                            error=f"{type(e).__name__}: {e}"[:200])

    @staticmethod
    def key_of(p: Proposal) -> str:
        return f"{p.decision_id}-{p.market}-{p.sleeve[0]}"

    def pending_tmn(self, supersede: tuple[str, str, str] | None = None, exclude_key: str | None = None
                    ) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
        """(pending buys, pending sells) in TMN per (market, sleeve) — feeds the gate.

        `supersede=(market, sleeve, urgency)`: leave out working SELLs on that (market, sleeve) that are
        LESS urgent — `submit` will cancel and replace them, so they must not block the urgent sell.
        `exclude_key`: leave out one working order (re-checking that order must not count itself).
        """
        buys: dict[tuple[str, str], float] = {}
        sells: dict[tuple[str, str], float] = {}
        for w in self.working.values():
            if exclude_key is not None and w.key == exclude_key:
                continue
            if (supersede and w.side == "SELL" and (w.market, w.p.sleeve) == supersede[:2]
                    and URGENCY_RANK[w.urgency] < URGENCY_RANK[supersede[2]]):
                continue
            bid, ask, _ = self.ex.quote(w.market)
            k = (w.market, w.p.sleeve)
            if w.side == "BUY":
                buys[k] = buys.get(k, 0.0) + w.remaining * (w.price or ask)
            else:
                sells[k] = sells.get(k, 0.0) + w.remaining * bid
        return buys, sells

    def cancel_all(self, now: float, reason: str) -> None:
        for w in list(self.working.values()):
            self._cancel_now(w, now, ("finish", f"CANCELED_{reason.upper()}"))

    def cancel_buys(self, now: float, reason: str) -> int:
        """Cancel every working BUY (risk must not grow in level 3, the flatten window or a hard flatten)."""
        n = 0
        for w in list(self.working.values()):
            if w.side == "BUY":
                self._cancel_now(w, now, ("finish", f"CANCELED_{reason.upper()}"))
                n += 1
        return n

    # ---- state machine ------------------------------------------------------
    def _step(self, w: Working, now: float) -> None:
        st = self.ex.get(w.client_id)
        if st is None:
            self._event(now, "order_missing", w.p, client_id=w.client_id)
            self.working.pop(w.key, None)
            return
        self._report(w, st, now)
        status = st.status
        if st.active:
            if w.after_cancel is None:
                wait = self.cfg.market_reprice_s if w.urgency == "market" else self.cfg.reprice_after_s
                if now - w.placed_at < wait:
                    return
                w.after_cancel = self._next_step(w)
            status = self._cancel_and_confirm(w, now)
            if status is None:
                return                                   # still live: keep tracking, retry next tick
        self._after_terminal(w, now, status)

    def _next_step(self, w: Working) -> tuple[str, str]:
        cap = URGENCY_RANK[self.urgency_cap(w.p)]
        if w.urgency == "passive":
            if w.reprices < self.cfg.max_reprices or cap < URGENCY_RANK["aggressive"]:
                w.reprices += 1
                return ("replace", "passive")               # re-price at the new best price, stay maker
            if not self.still_valid(w.p):
                return ("finish", "CANCELED_NOT_VALID")
            return ("replace", "aggressive")
        if w.p.increases_risk:
            return ("finish", "CANCELED_NO_FILL")           # never chase a buy with a market order
        if w.urgency == "aggressive" and cap < URGENCY_RANK["market"]:
            return ("replace", "aggressive")                # re-cross at the new best price
        return ("replace", "market")

    def _after_terminal(self, w: Working, now: float, status: str) -> None:
        plan = w.after_cancel
        w.filled_before += w.seen_qty
        w.seen_qty = w.seen_value = w.seen_fee = 0.0
        w.after_cancel = None
        if status == "FILLED" or w.remaining <= 0:
            return self._finish(w, now, "FILLED")
        if plan is None or plan[0] == "finish":
            return self._finish(w, now, plan[1] if plan else status)
        bid, ask, _ = self.ex.quote(w.market)
        if w.remaining * (ask if w.side == "BUY" else bid) < self.ex.rules(w.market).min_notional:
            self._event(now, "remainder_below_min_notional", w.p, remaining_qty=w.remaining)
            return self._finish(w, now, "FILLED_ENOUGH")
        w.urgency, w.sub = plan[1], w.sub + 1
        self._event(now, "order_escalated", w.p, urgency=w.urgency, reprices=w.reprices)
        if not self._place_current(w, now):
            self._finish(w, now, "REPLACEMENT_FAILED")

    def _cancel_and_confirm(self, w: Working, now: float) -> str | None:
        """Send a cancel; return the terminal status once `get()` confirms it (fills reported),
        or None if the order is still live (the caller keeps tracking it)."""
        try:
            self.ex.cancel(w.client_id)
        except (ExchangeTimeout, OrderRejected) as e:
            self._event(now, "cancel_error", w.p, client_id=w.client_id, error=type(e).__name__)
        st = self.ex.get(w.client_id)
        if st is None:
            return "CANCELED"
        self._report(w, st, now)
        if st.active:
            self._event(now, "cancel_unconfirmed", w.p, client_id=w.client_id)
            return None
        return st.status

    def _cancel_now(self, w: Working, now: float, plan: tuple[str, str]) -> bool:
        w.after_cancel = plan
        status = self._cancel_and_confirm(w, now)
        if status is None:
            return False
        self._after_terminal(w, now, status)
        return True

    def _place_current(self, w: Working, now: float) -> bool:
        w.client_id = f"d-{w.key}" + (f"-r{w.sub}" if w.sub else "")
        w.placed_at = now
        bid, ask, _ = self.ex.quote(w.market)
        if w.urgency == "market":
            otype, w.price = "MARKET", None
        elif w.urgency == "aggressive":
            otype, w.price = "LIMIT", (ask if w.side == "BUY" else bid)
        else:
            otype, w.price = "LIMIT", (bid if w.side == "BUY" else ask)
        qty = w.remaining
        for attempt in (1, 2):
            try:
                st = self.ex.place(w.client_id, w.market, w.side, otype, qty, w.price)
            except ExchangeTimeout:
                st = self.ex.get(w.client_id) if self.cfg.lookup_before_retry else None
                self._event(now, "order_lookup_before_retry", w.p, client_id=w.client_id, found=st is not None)
                if st is None:
                    continue
            except OrderRejected as e:
                st = self.ex.get(w.client_id) if attempt == 2 else None   # late-accepted first POST?
                if st is None:
                    self._event(now, "order_rejected", w.p, client_id=w.client_id, reason=str(e))
                    return False
                self._event(now, "order_adopted_after_duplicate", w.p, client_id=w.client_id)
            self._event(now, "order_placed", w.p, client_id=w.client_id, type=st.type or otype,
                        price=st.price if st.price is not None else w.price,
                        qty=st.qty, urgency=w.urgency)
            self._report(w, st, now)
            if not st.active:
                self._after_terminal(w, now, st.status)
            return True
        self._event(now, "order_place_failed", w.p, client_id=w.client_id)
        return False

    def _report(self, w: Working, st: OrderState, now: float) -> None:
        dq = st.filled_qty - w.seen_qty
        if dq <= 1e-12:
            return
        dv, df = st.filled_value - w.seen_value, st.fee_total - w.seen_fee
        w.seen_qty, w.seen_value, w.seen_fee = st.filled_qty, st.filled_value, st.fee_total
        base = self.ex.rules(w.market).base
        self.on_fill(Fill(client_id=st.client_id, market=w.market, sleeve=w.p.sleeve, side=w.side, qty=dq,
                          price=dv / dq, fee=df, fee_asset=base if w.side == "BUY" else "TMN", ts=now,
                          decision_id=w.p.decision_id, kind=w.p.kind))

    def _finish(self, w: Working, now: float, status: str) -> None:
        self.working.pop(w.key, None)
        self._event(now, "order_done", w.p, client_id=w.client_id, status=status,
                    filled_qty=w.filled_before + w.seen_qty, wanted_qty=w.qty)

    def _event(self, now: float, kind: str, p: Proposal, **kw) -> None:
        self.on_event({"ts": now, "event": kind, "decision_id": p.decision_id, "market": p.market,
                       "side": p.side, "sleeve": p.sleeve, "kind": p.kind, **kw})
