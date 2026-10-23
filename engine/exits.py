"""Exit rules that fire on price and time.

- Core: −2.5% from average cost per market → sell the market's core at market;
  a 12 h cooldown starts when the order is accepted (Portfolio.note_accept).
- Tactical: stop −1.5%; once the peak is +1.5% the stop moves to entry +0.5% (trail);
  take-profit +2.5% (passive, maker); time stop after 36 h (market).
- Hard flatten: equity ≤ −3% vs start → latch the halt and sell everything at market.

Prices passed in are BIDs (what a sell would get). Average cost already includes the
buy fee (via the smaller received qty), so levels are relative to all-in cost.
"""
from __future__ import annotations

import config
from engine.portfolio import Portfolio
from engine.types import Proposal


def check_exits(pf: Portfolio, bids: dict[str, float], now: float,
                lim: config.Limits = config.LIMITS) -> list[Proposal]:
    out = []
    for (m, sleeve), p in sorted(pf.positions.items()):
        if p.qty <= 0 or m not in bids or p.value(bids[m]) < lim.exchange_min_notional_tmn:
            continue                              # dust below Wallex's minimum can't be sold; don't keep trying
        px, entry = bids[m], p.avg_price
        did = f"x{int(now) % 10**8:08d}"          # short: becomes part of the exchange client_id
        if sleeve == "core":
            if px <= entry * (1 - lim.core_stop):
                out.append(Proposal(m, "core", "SELL", p.value(px), "stop", "gate", did, "market", p.qty))
            continue
        trailing = p.peak_price >= entry * (1 + lim.trail_after)
        stop = entry * (1 + lim.trail_to) if trailing else entry * (1 - lim.tactical_stop)
        if px <= stop:
            out.append(Proposal(m, "tactical", "SELL", p.value(px), "stop", "gate", did, "market", p.qty))
        elif px >= entry * (1 + lim.tactical_tp):
            out.append(Proposal(m, "tactical", "SELL", p.value(px), "take_profit", "gate", did, "passive", p.qty))
        elif p.opened_at is not None and now - p.opened_at >= lim.tactical_time_h * 3600:
            out.append(Proposal(m, "tactical", "SELL", p.value(px), "time_stop", "gate", did, "market", p.qty))
    return out


def hard_flatten_due(pf: Portfolio, bids: dict[str, float], lim: config.Limits = config.LIMITS) -> bool:
    return pf.halted or pf.gain(bids) <= -lim.hard_flatten_drawdown


def hard_flatten(pf: Portfolio, bids: dict[str, float], now: float) -> list[Proposal]:
    """Latch the halt (only the user restarts) and sell everything at market."""
    if not pf.halted:
        pf.latch_halt(f"equity {pf.gain(bids):+.2%} vs start at {int(now)}")
    return [Proposal(m, s, "SELL", p.value(bids[m]), "flatten", "gate", f"hf{int(now) % 10**8:08d}", "market", p.qty)
            for (m, s), p in sorted(pf.positions.items())
            if p.qty > 0 and p.value(bids[m]) >= config.LIMITS.exchange_min_notional_tmn]
