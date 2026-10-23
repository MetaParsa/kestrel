"""Every number that changes trading behaviour lives here.

The playbook can only tighten these limits, never loosen them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")

# ---- contest ---------------------------------------------------------------
START_EQUITY_TMN = 5_000_000.0
# Unconfirmed: earliest plausible end (Torob's deadline). Override with WALLEX_END_AT (ISO 8601).
END_AT = datetime.fromisoformat(os.environ.get("WALLEX_END_AT", "2026-10-23T23:50:00+03:30"))
if END_AT.tzinfo is None:   # a naive time would silently use the machine's timezone
    raise ValueError("WALLEX_END_AT must include a UTC offset, e.g. 2026-10-23T23:50:00+03:30")

MARKETS = ("USDTTMN", "BTCTMN")         # read, forecast and scored by the manager
# The prop account only accepts orders in BTCTMN (Wallex: «امکان ثبت سفارش در بازار USDTTMN وجود ندارد»,
# 2026-10-23). USDTTMN stays an input; its regime bounds are forced to 0 below and the gate refuses it.
TRADABLE = tuple(m for m in os.environ.get("WALLEX_TRADABLE", "BTCTMN").split(",") if m in MARKETS)
BASE_ASSET = {"USDTTMN": "USDT", "BTCTMN": "BTC"}
FEATURE_MARKETS = ("BTCUSDT",)          # read only, never traded

# (maker, taker) as fractions — read from the account 2026-10-21; re-check on the sub-account
FEES = {"USDTTMN": (0.0025, 0.0030), "BTCTMN": (0.0025, 0.0030)}
# Typical half-spread for simulated taker fills (research/wallex/FINDINGS.md §1: 5 / 9.5 bps spreads)
SIM_HALF_SPREAD = {"USDTTMN": 0.00026, "BTCTMN": 0.00048}


# ---- regimes ------------------------------------------------
@dataclass(frozen=True)
class RegimeRule:
    default: dict[str, float]
    bounds: dict[str, tuple[float, float]]
    tactical: bool
    allow_adds: bool = True       # event_shock: hold what we have, add nothing


REGIMES: dict[str, RegimeRule] = {
    "devaluation_drift": RegimeRule({"USDTTMN": 0.20, "BTCTMN": 0.15},
                                    {"USDTTMN": (0.10, 0.25), "BTCTMN": (0.00, 0.25)}, tactical=True),
    "trend_up": RegimeRule({"USDTTMN": 0.15, "BTCTMN": 0.20},
                           {"USDTTMN": (0.10, 0.25), "BTCTMN": (0.05, 0.30)}, tactical=True),
    "range": RegimeRule({"USDTTMN": 0.10, "BTCTMN": 0.05},
                        {"USDTTMN": (0.00, 0.15), "BTCTMN": (0.00, 0.10)}, tactical=True),
    "risk_off": RegimeRule({"USDTTMN": 0.0, "BTCTMN": 0.0},
                           {"USDTTMN": (0.0, 0.0), "BTCTMN": (0.0, 0.0)}, tactical=False),
    "event_shock": RegimeRule({"USDTTMN": 0.0, "BTCTMN": 0.0},
                              {"USDTTMN": (0.0, 0.25), "BTCTMN": (0.0, 0.20)}, tactical=False, allow_adds=False),
}
# Markets we can't trade get target 0 in every regime, so the manager, the gate and the core sleeve agree.
REGIMES = {name: RegimeRule({m: (v if m in TRADABLE else 0.0) for m, v in r.default.items()},
                            {m: (b if m in TRADABLE else (0.0, 0.0)) for m, b in r.bounds.items()},
                            r.tactical, r.allow_adds)
           for name, r in REGIMES.items()}


# ---- risk limits -------------------------------------------
@dataclass(frozen=True)
class Limits:
    max_market: float = 0.40          # exposure per market, core + tactical
    max_total: float = 0.60
    tactical_slot: float = 0.15
    core_stop: float = 0.025          # from average cost, per market
    core_cooldown_h: float = 12.0
    tactical_stop: float = 0.015
    tactical_tp: float = 0.025
    tactical_time_h: float = 36.0
    trail_after: float = 0.015        # once up this much, stop moves to entry + trail_to
    trail_to: float = 0.005
    tactical_per_market_day: int = 2
    core_rebalances_per_market_day: int = 2   # FILLED increases only; reductions are always allowed
    core_step: float = 0.10           # max increase per rebalance, fraction of equity
    daily_loss: float = 0.010         # vs day-start equity → no new entries / adds until 00:00 Tehran
    micro_drawdown: float = 0.020     # vs START_EQUITY → micro mode
    micro_max_total: float = 0.10
    micro_trades_per_day: int = 1
    hard_flatten_drawdown: float = 0.030
    cushion_from_gain: float = 0.008
    cushion_buffer: float = 0.006     # added to each stop distance (fees + slippage)
    cushion_margin: float = 0.003
    activity_floor_hour: int = 10     # Tehran — early enough to also work on the last day (sell-off starts T−8 h)
    min_order_tmn: float = 150_000.0
    dust_floor_tmn: float = 60_000.0  # never leave a remainder smaller than this
    exchange_min_notional_tmn: float = 50_000.0
    stale_after_s: float = 30.0
    spread_mult_max: float = 3.0      # vs 24 h median spread


LIMITS = Limits()


# ---- execution ---------------------------------------
@dataclass(frozen=True)
class ExecutionCfg:
    reprice_after_s: float = 90.0
    max_reprices: int = 2
    lookup_before_retry: bool = True
    market_protection: float = 0.02   # live "market" orders are limits at most 2% through the touch
                                      # (Wallex rejects prices more than ~3% from its fair price)
    market_reprice_s: float = 10.0    # ...re-priced every 10 s until filled (stops must not wait 90 s)
    max_quote_age_s: float = 20.0     # never price an order from a quote older than this


EXECUTION = ExecutionCfg()


# ---- engine loop ----------------------------------------
@dataclass(frozen=True)
class LoopCfg:
    tick_s: float = 10.0
    bundle_every_s: float = 60.0
    # WALLEX_MANAGER_EVERY_MIN asks Claude more often (e.g. 15 on the last evening); default hourly
    manager_every_s: float = float(os.environ.get("WALLEX_MANAGER_EVERY_MIN", "60")) * 60
    request_reissue_s: float = 20 * 60          # unanswered manager request → new request id
    playbook_grace_s: float = 2 * 3600          # degrade level 2 window after valid_until
    max_trade_age_s: float = 30 * 60            # no trade for this long → treat the market as stale for buys
    wallex_errors_limit: int = 20               # per 10 min → degrade level 3
    event_move_sigma: float = 2.5               # 1 h move vs 30 d hourly σ → off-cycle manager call
    event_drawdown: float = -0.007              # daily P&L → off-cycle manager call
    offcycle_min_gap_s: float = 3600.0
    bars_refresh_s: float = 300.0
    blocked_log_every_s: float = 15 * 60        # repeat log of the same blocked proposal at most this often
    heartbeat_stale_s: float = 120.0


LOOP = LoopCfg()
# Typical spreads (bps) used until 30 min of live spread history exist (FINDINGS §1).
REF_SPREAD_BPS = {"USDTTMN": 5.1, "BTCTMN": 9.5}


# ---- flatten, hours before END_AT ---------------------------
@dataclass(frozen=True)
class FlattenCfg:
    no_new_h: float = 8.0            # no tactical entries, no adds; core → 0 via passive limits
    aggressive_h: float = 3.0
    market_h: float = 1.0
    alert_h: float = 2.0             # Bale alert if any non-TMN above dust remains
    verify_min: float = 30.0


def _flatten_cfg() -> FlattenCfg:
    """WALLEX_FLATTEN_H="no_new,aggressive,market" (hours before END_AT) shortens the sell-off window,
    e.g. "2.5,1.5,1.0" keeps buying until 2.5 h before the end. Unset = the defaults above."""
    raw = os.environ.get("WALLEX_FLATTEN_H", "").strip()
    if not raw:
        return FlattenCfg()
    no_new, aggressive, market = (float(x) for x in raw.split(","))
    verify_h = FlattenCfg.verify_min / 60
    if not no_new > aggressive > market > verify_h:
        raise ValueError(f"WALLEX_FLATTEN_H must be decreasing and above {verify_h} h, got {raw!r}")
    # alert if anything non-TMN is still held 15 min into the forced-sell phase
    return FlattenCfg(no_new_h=no_new, aggressive_h=aggressive, market_h=market,
                      alert_h=max(verify_h + 0.1, market - 0.25))


FLATTEN = _flatten_cfg()

# WALLEX_PROFIT_LOCK=0.0003: once equity minus the cost of selling everything is this far above the
# start capital, sell everything and stop buying until the end (latched). Unset = off.
_lock = os.environ.get("WALLEX_PROFIT_LOCK", "").strip()
PROFIT_LOCK: float | None = float(_lock) if _lock else None
