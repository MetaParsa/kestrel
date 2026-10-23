"""End-of-contest schedule: everything back to TMN before END_AT (spec v2 §8.3).

T−8 h  no new tactical entries, no adds; every position sold with passive limits
T−3 h  aggressive limits
T−1 h  market orders for anything left
T−2 h  alert if any non-TMN value above dust remains
T−30 m verify (still selling at market); after END_AT: read-only
"""
from __future__ import annotations

from datetime import datetime

import config
from engine.gate import FlattenPhase
from engine.portfolio import Portfolio
from engine.types import Proposal


def phase(now: float, end_at: datetime = config.END_AT, cfg: config.FlattenCfg = config.FLATTEN) -> FlattenPhase:
    left_h = (end_at.timestamp() - now) / 3600
    if left_h <= 0:
        return "ended"
    if left_h <= cfg.verify_min / 60:
        return "verify"
    if left_h <= cfg.market_h:
        return "market"
    if left_h <= cfg.aggressive_h:
        return "aggressive"
    if left_h <= cfg.no_new_h:
        return "no_new"
    return "normal"


URGENCY = {"no_new": "passive", "aggressive": "aggressive", "market": "market", "verify": "market"}


def flatten_proposals(pf: Portfolio, prices: dict[str, float], ph: FlattenPhase, now: float) -> list[Proposal]:
    """One sell per non-empty (market, sleeve). Empty during 'normal' and after 'ended'."""
    if ph not in URGENCY:
        return []
    out = []
    for (m, sleeve), p in sorted(pf.positions.items()):
        if p.qty > 0 and p.value(prices[m]) >= config.LIMITS.exchange_min_notional_tmn:   # dust can't be sold
            out.append(Proposal(m, sleeve, "SELL", p.value(prices[m]), "flatten", "flatten",
                                f"fl{int(now) % 10**8:08d}", URGENCY[ph], p.qty))   # exact held qty; unique id
                                # per tick (the engine skips it while an equally urgent sell is working)
    return out


def alert_due(now: float, non_tmn_value_tmn: float, end_at: datetime = config.END_AT,
              cfg: config.FlattenCfg = config.FLATTEN, lim: config.Limits = config.LIMITS) -> bool:
    left_h = (end_at.timestamp() - now) / 3600
    return 0 < left_h <= cfg.alert_h and non_tmn_value_tmn > lim.exchange_min_notional_tmn
