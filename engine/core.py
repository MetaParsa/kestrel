"""Core sleeve: regime → target exposure → at most one rebalance per market per day.

Increases move at most `core_step` (10% of equity) per day per market; reductions
toward a lower target are always allowed at once (they reduce risk). A ±2% band
around the target prevents churn. The activity floor makes sure something trades
every Tehran day unless risk says stay still.
"""
from __future__ import annotations

from datetime import datetime, timezone

import config
from engine.types import Proposal

BAND = 0.02          # don't trade differences smaller than 2% of equity


def core_targets(regime: str, current: dict[str, float], *, overrides: dict[str, float] | None = None,
                 risk_multiplier: float = 1.0, mode: str = "normal",
                 lim: config.Limits = config.LIMITS) -> dict[str, float]:
    """Target exposure per market (fraction of equity) after regime bounds, multiplier and mode."""
    rule = config.REGIMES[regime]
    mult = min(1.0, max(0.0, risk_multiplier))
    out = {}
    for m in config.MARKETS:
        lo, hi = rule.bounds[m]
        want = (overrides or {}).get(m, rule.default[m])
        t = min(hi, max(lo, want)) * mult
        if not rule.allow_adds:                       # event_shock: hold what we have, add nothing
            t = min(current.get(m, 0.0), hi)
        out[m] = t
    if mode == "hard_flatten":
        return {m: 0.0 for m in out}
    if mode == "micro":
        total = sum(out.values())
        if total > lim.micro_max_total:
            out = {m: t * lim.micro_max_total / total for m, t in out.items()}
    return out


def plan_rebalance(market: str, target: float, current: float, equity: float, *, increases_today: int,
                   adds_allowed: bool, decision_id: str, floor: bool = False, held_qty: float = 0.0,
                   price: float = 0.0, lim: config.Limits = config.LIMITS) -> Proposal | None:
    """`current` is the CORE sleeve's exposure only (tactical positions don't count here).
    For a reduction pass `held_qty` and `price` (bid) so the SELL carries an exact qty."""
    diff = target - current
    if diff < 0:
        closing = target <= 1e-9 and current > 0
        if not closing and -diff < BAND:
            return None
        size = current * equity if closing else -diff * equity
        qty = held_qty if closing else min(held_qty, size / price) if price > 0 else 0.0
        return Proposal(market, "core", "SELL", size, "rebalance_down", "manager", decision_id, "passive", qty)
    band = 0.0 if floor else BAND
    if diff <= band or not adds_allowed or increases_today >= lim.core_rebalances_per_market_day:
        return None
    size = min(diff, lim.core_step) * equity
    if size < lim.min_order_tmn:
        return None
    return Proposal(market, "core", "BUY", size, "floor" if floor else "rebalance_up",
                    "floor" if floor else "manager", decision_id, "passive")


def floor_due(now: float, *, trades_today: int, mode: str, regime: str, daily_stopped: bool,
              lim: config.Limits = config.LIMITS) -> bool:
    """Spec v2 §8.1 activity floor: ≥ 1 entry or rebalance per Tehran day."""
    hour = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(config.TEHRAN).hour
    if hour < lim.activity_floor_hour or trades_today > 0:
        return False
    return not (mode != "normal" or regime == "risk_off" or daily_stopped)


def floor_proposal(targets: dict[str, float], current: dict[str, float], equity: float, *,
                   increases_today: dict[str, int], adds_allowed: bool, decision_id: str,
                   lim: config.Limits = config.LIMITS) -> Proposal | None:
    """The single trade that satisfies the floor: the market furthest below its target.
    If every market is at target, nothing is forced (no churn for its own sake)."""
    gaps = sorted(((targets[m] - current.get(m, 0.0), m) for m in targets), reverse=True)
    for gap, m in gaps:
        if gap <= 0:
            break
        p = plan_rebalance(m, targets[m], current.get(m, 0.0), equity, floor=True, adds_allowed=adds_allowed,
                           increases_today=increases_today.get(m, 0), decision_id=decision_id, lim=lim)
        if p:
            return p
    return None
