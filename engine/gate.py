"""The risk gate (spec v2 §8.1). Pure functions: same inputs → same verdict.

Nothing upstream (Claude, Clef, LightGBM, the core sleeve) can trade around it.
Risk-reducing orders (sells) are always allowed, only adjusted for dust and for
sells already pending; risk-increasing orders (buys) pass a list of blockers and
are then resized to fit every cap. Each rule that fired is named in the verdict.

Inputs are taken from one source of truth (`sleeve_value` + pending orders); exposure
and open risk are derived here, not passed in. Missing or non-finite data fails closed
for buys.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import config
from engine.types import Proposal

FlattenPhase = Literal["normal", "no_new", "aggressive", "market", "verify", "ended"]


@dataclass(frozen=True)
class GateContext:
    now: float
    equity: float
    gain: float                                        # vs START_EQUITY
    daily_pnl: float                                   # vs day-start equity
    sleeve_value: dict[tuple[str, str], float]         # (market, sleeve) → TMN value held (at bid)
    adds_allowed: bool                                 # from the regime (False in event_shock)
    core_cap: dict[str, float]                         # regime upper bound per market (fraction)
    tactical_allowed: dict[str, bool] = field(default_factory=dict)
    pending_buy: dict[tuple[str, str], float] = field(default_factory=dict)    # TMN, from the executor
    pending_sell: dict[tuple[str, str], float] = field(default_factory=dict)   # TMN, from the executor
    halted: bool = False
    flatten_phase: FlattenPhase = "normal"
    data_age_s: dict[str, float] = field(default_factory=dict)
    spread_ratio: dict[str, float] = field(default_factory=dict)   # spread / 24 h median spread
    tactical_entries_today: dict[str, int] = field(default_factory=dict)
    core_increases_today: dict[str, int] = field(default_factory=dict)
    entries_today: int = 0                                          # accepted risk-increasing orders
    core_stopped_at: dict[str, float] = field(default_factory=dict)

    def exposure(self, market: str) -> float:
        held = sum(v for (m, _), v in self.sleeve_value.items() if m == market)
        pend = sum(v for (m, _), v in self.pending_buy.items() if m == market)
        return (held + pend) / self.equity

    def sleeve_exposure(self, market: str, sleeve: str) -> float:
        return (self.sleeve_value.get((market, sleeve), 0.0)
                + self.pending_buy.get((market, sleeve), 0.0)) / self.equity

    def open_risk(self, lim: config.Limits) -> float:
        """Σ exposure × (stop distance + buffer) over everything held or pending."""
        total = 0.0
        for (m, s) in set(self.sleeve_value) | set(self.pending_buy):
            stop = lim.tactical_stop if s == "tactical" else lim.core_stop
            total += self.sleeve_exposure(m, s) * (stop + lim.cushion_buffer)
        return total


@dataclass(frozen=True)
class Verdict:
    action: Literal["allow", "resize", "block"]
    size_tmn: float
    rules: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.action != "block"


def _finite(*xs: float) -> bool:
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def mode(ctx: GateContext, lim: config.Limits = config.LIMITS) -> str:
    if ctx.halted or not _finite(ctx.gain) or ctx.gain <= -lim.hard_flatten_drawdown:
        return "hard_flatten"
    if ctx.gain <= -lim.micro_drawdown:
        return "micro"
    return "normal"


def check(p: Proposal, ctx: GateContext, lim: config.Limits = config.LIMITS) -> Verdict:
    if p.market not in config.TRADABLE:
        return Verdict("block", 0.0, ("G_MARKET_NOT_ALLOWED",))
    if not _finite(p.size_tmn) or p.size_tmn <= 0:
        return Verdict("block", 0.0, ("G_BAD_SIZE",))
    return _check_buy(p, ctx, lim) if p.increases_risk else _check_sell(p, ctx, lim)


def _check_sell(p: Proposal, ctx: GateContext, lim: config.Limits) -> Verdict:
    if p.side != "SELL":
        return Verdict("block", 0.0, ("G_REDUCING_MUST_SELL",))
    held = ctx.sleeve_value.get((p.market, p.sleeve), 0.0)
    held = held if _finite(held) else 0.0
    free = held - ctx.pending_sell.get((p.market, p.sleeve), 0.0)
    if free <= 0:
        return Verdict("block", 0.0, ("G_NOTHING_TO_SELL",))
    size, rules = min(p.size_tmn, free), []
    if size < p.size_tmn:
        rules.append("G_SELL_CAPPED_TO_HELD")
    if 0 < free - size < lim.dust_floor_tmn:
        size = free
        rules.append("G_DUST_CLOSE_ALL")              # caller sells the exact held qty
    if size < lim.exchange_min_notional_tmn:
        return Verdict("block", 0.0, tuple(rules + ["G_BELOW_EXCHANGE_MIN"]))
    return Verdict("resize" if abs(size - p.size_tmn) > 1 else "allow", size, tuple(rules))


def _check_buy(p: Proposal, ctx: GateContext, lim: config.Limits) -> Verdict:
    if p.side != "BUY":
        return Verdict("block", 0.0, ("G_INCREASING_MUST_BUY",))
    if not _finite(ctx.equity, ctx.gain, ctx.daily_pnl) or ctx.equity <= 0:
        return Verdict("block", 0.0, ("G_BAD_INPUT",))
    if not all(_finite(v) for v in (*ctx.sleeve_value.values(), *ctx.pending_buy.values())):
        return Verdict("block", 0.0, ("G_BAD_INPUT",))
    m = mode(ctx, lim)
    blockers = []
    if m == "hard_flatten":
        blockers.append("G_HARD_FLATTEN")
    if ctx.flatten_phase != "normal":
        blockers.append("G_FLATTEN_WINDOW")
    age = ctx.data_age_s.get(p.market)
    if age is None or not _finite(age) or age > lim.stale_after_s:
        blockers.append("G_STALE_DATA")
    spread = ctx.spread_ratio.get(p.market)
    if spread is None or not _finite(spread) or spread > lim.spread_mult_max:
        blockers.append("G_WIDE_SPREAD")
    if ctx.daily_pnl <= -lim.daily_loss:
        blockers.append("G_DAILY_LOSS")
    if m == "micro" and ctx.entries_today >= lim.micro_trades_per_day:
        blockers.append("G_MICRO_FREQ")
    if p.sleeve == "tactical":
        if not ctx.tactical_allowed.get(p.market, False):
            blockers.append("G_TACTICAL_OFF")
        if ctx.tactical_entries_today.get(p.market, 0) >= lim.tactical_per_market_day:
            blockers.append("G_TACTICAL_FREQ")
    else:
        if not ctx.adds_allowed:
            blockers.append("G_NO_ADDS")
        if ctx.core_increases_today.get(p.market, 0) >= lim.core_rebalances_per_market_day:
            blockers.append("G_CORE_FREQ")
        stopped = ctx.core_stopped_at.get(p.market)
        if stopped is not None and ctx.now - stopped < lim.core_cooldown_h * 3600:
            blockers.append("G_CORE_COOLDOWN")
    if blockers:
        return Verdict("block", 0.0, tuple(blockers))

    eq = ctx.equity
    caps: dict[str, float] = {"requested": p.size_tmn / eq}
    caps["G_MARKET_CAP"] = lim.max_market - ctx.exposure(p.market)
    total_now = sum(ctx.exposure(mk) for mk in config.MARKETS)
    total_cap = lim.micro_max_total if m == "micro" else lim.max_total
    caps["G_MICRO_TOTAL_CAP" if m == "micro" else "G_TOTAL_CAP"] = total_cap - total_now
    if p.sleeve == "tactical":
        caps["G_TACTICAL_SLOT"] = lim.tactical_slot - ctx.sleeve_exposure(p.market, "tactical")
        stop = lim.tactical_stop
    else:
        caps["G_CORE_STEP"] = lim.core_step
        caps["G_CORE_REGIME_CAP"] = ctx.core_cap.get(p.market, 0.0) - ctx.sleeve_exposure(p.market, "core")
        stop = lim.core_stop
    if ctx.gain >= lim.cushion_from_gain:
        budget = ctx.gain - lim.cushion_margin - ctx.open_risk(lim)
        caps["G_CUSHION"] = budget / (stop + lim.cushion_buffer)

    frac = max(0.0, min(caps.values()))
    size = frac * eq
    binding = tuple(k for k, v in caps.items() if k != "requested" and v * eq <= p.size_tmn + 1e-6)
    if size < lim.min_order_tmn - 1e-6:
        return Verdict("block", 0.0, binding + ("G_MIN_ORDER",))
    return Verdict("resize" if size < p.size_tmn - 1 else "allow", size, binding)
