"""Every §8.1 limit gets a test that tries to break it."""
import math
from dataclasses import replace

import pytest

import config
from engine.gate import GateContext, check, mode
from engine.types import Proposal

EQ = 5_000_000.0
NOW = 1_792_650_000.0
L = config.LIMITS
CAP = {"USDTTMN": 0.25, "BTCTMN": 0.20}


def ctx(held: dict | None = None, **kw) -> GateContext:
    """held: {(market, sleeve): fraction of equity}."""
    base = dict(now=NOW, equity=EQ, gain=0.0, daily_pnl=0.0,
                sleeve_value={k: v * EQ for k, v in (held or {}).items()},
                adds_allowed=True, core_cap=CAP,
                data_age_s={"USDTTMN": 5.0, "BTCTMN": 5.0}, spread_ratio={"USDTTMN": 1.0, "BTCTMN": 1.0},
                tactical_allowed={"USDTTMN": True, "BTCTMN": True})
    base.update(kw)
    return GateContext(**base)


def buy(size, sleeve="core", market="USDTTMN", kind=None):
    return Proposal(market=market, sleeve=sleeve, side="BUY", size_tmn=size,
                    kind=kind or ("rebalance_up" if sleeve == "core" else "entry"),
                    decided_by="test", decision_id="t")


def sell(size, sleeve="core", market="USDTTMN", kind="rebalance_down"):
    return Proposal(market=market, sleeve=sleeve, side="SELL", size_tmn=size, kind=kind,
                    decided_by="test", decision_id="t", qty=1.0)


# ---- caps -----------------------------------------------------------------
def test_core_step_caps_a_big_buy():
    v = check(buy(EQ * 0.30), ctx())
    assert v.action == "resize" and v.size_tmn == pytest.approx(EQ * L.core_step)
    assert "G_CORE_STEP" in v.rules


def test_core_regime_cap():
    v = check(buy(EQ * 0.10), ctx({("USDTTMN", "core"): 0.20}))
    assert v.size_tmn == pytest.approx(EQ * 0.05) and "G_CORE_REGIME_CAP" in v.rules
    assert check(buy(EQ * 0.10), ctx(core_cap={})).action == "block"        # no cap given → nothing allowed


def test_market_cap_40pct_counts_both_sleeves():
    v = check(buy(EQ * 0.10, sleeve="tactical"), ctx({("USDTTMN", "core"): 0.25, ("USDTTMN", "tactical"): 0.10}))
    assert v.size_tmn == pytest.approx(EQ * 0.05) and "G_MARKET_CAP" in v.rules


def test_total_cap_60pct():
    v = check(buy(EQ * 0.10, sleeve="tactical", market="BTCTMN"),
              ctx({("USDTTMN", "core"): 0.25, ("USDTTMN", "tactical"): 0.15, ("BTCTMN", "core"): 0.16}))
    assert v.size_tmn == pytest.approx(EQ * 0.04) and "G_TOTAL_CAP" in v.rules


def test_pending_buys_count_against_caps():
    c = ctx({("USDTTMN", "core"): 0.20}, pending_buy={("USDTTMN", "tactical"): EQ * 0.15})
    v = check(buy(EQ * 0.10, sleeve="tactical"), c)
    assert v.action == "block"                       # slot full (pending) and market at 35% → 5% < min...
    assert "G_TACTICAL_SLOT" in v.rules


def test_never_all_in():
    v = check(buy(EQ, sleeve="tactical"), ctx())
    assert v.size_tmn <= EQ * L.tactical_slot + 1


def test_resize_below_min_order_blocks():
    v = check(buy(EQ * 0.10), ctx({("USDTTMN", "core"): 0.24}))
    assert v.action == "block" and "G_MIN_ORDER" in v.rules


# ---- blockers ---------------------------------------------------------------
@pytest.mark.parametrize("kw,rule", [
    (dict(halted=True), "G_HARD_FLATTEN"),
    (dict(gain=-0.03), "G_HARD_FLATTEN"),                        # boundary: exactly −3%
    (dict(flatten_phase="no_new"), "G_FLATTEN_WINDOW"),
    (dict(data_age_s={"USDTTMN": 30.01}), "G_STALE_DATA"),
    (dict(data_age_s={}), "G_STALE_DATA"),                       # missing → fail closed
    (dict(data_age_s={"USDTTMN": math.nan}), "G_STALE_DATA"),
    (dict(spread_ratio={"USDTTMN": 3.5}), "G_WIDE_SPREAD"),
    (dict(spread_ratio={}), "G_WIDE_SPREAD"),                    # missing → fail closed
    (dict(daily_pnl=-0.01), "G_DAILY_LOSS"),                     # boundary: exactly −1%
    (dict(gain=-0.02, entries_today=1), "G_MICRO_FREQ"),         # boundary: exactly −2%
    (dict(adds_allowed=False), "G_NO_ADDS"),
    (dict(core_increases_today={"USDTTMN": 1}), "G_CORE_FREQ"),
    (dict(core_stopped_at={"USDTTMN": NOW - 3600}), "G_CORE_COOLDOWN"),
    (dict(equity=0.0), "G_BAD_INPUT"),
    (dict(gain=math.nan), "G_BAD_INPUT"),
    (dict(sleeve_value={("BTCTMN", "core"): math.inf}), "G_BAD_INPUT"),
])
def test_core_buy_blockers(kw, rule):
    v = check(buy(500_000), ctx(**kw))
    assert v.action == "block" and rule in v.rules


def test_stale_boundary_30s_is_still_fresh():
    assert check(buy(500_000), ctx(data_age_s={"USDTTMN": 30.0})).ok


@pytest.mark.parametrize("kw,rule", [
    (dict(tactical_allowed={"USDTTMN": False}), "G_TACTICAL_OFF"),
    (dict(tactical_allowed={}), "G_TACTICAL_OFF"),
    (dict(tactical_entries_today={"USDTTMN": 2}), "G_TACTICAL_FREQ"),
])
def test_tactical_blockers(kw, rule):
    v = check(buy(500_000, sleeve="tactical"), ctx(**kw))
    assert v.action == "block" and rule in v.rules


def test_cooldown_expires_at_12h():
    assert check(buy(500_000), ctx(core_stopped_at={"USDTTMN": NOW - 12 * 3600})).ok


def test_micro_mode_caps_total_at_10pct():
    c = ctx({("USDTTMN", "core"): 0.06}, gain=-0.021)
    assert mode(c) == "micro"
    v = check(buy(EQ * 0.10), c)
    assert v.size_tmn == pytest.approx(EQ * 0.04) and "G_MICRO_TOTAL_CAP" in v.rules


def test_only_our_markets():
    assert check(buy(500_000, market="ETHTMN"), ctx()).rules == ("G_MARKET_NOT_ALLOWED",)
    assert check(sell(500_000, market="ETHTMN"), ctx()).rules == ("G_MARKET_NOT_ALLOWED",)


def test_bad_sizes_blocked():
    assert check(buy(math.nan), ctx()).rules == ("G_BAD_SIZE",)
    assert check(sell(-5), ctx()).rules == ("G_BAD_SIZE",)


# ---- cushion rule ---------------------------------------------------------
def test_cushion_inactive_below_threshold():
    assert "G_CUSHION" not in check(buy(EQ * 0.10, market="BTCTMN"), ctx(gain=0.0079)).rules


def test_cushion_limits_new_core_risk_when_in_profit():
    # gain 1.2%: budget = 1.2 − 0.3 − 0.20×(2.5+0.6) = 0.28% → core room = 0.0028/0.031 ≈ 9.0% of equity
    c = ctx({("USDTTMN", "core"): 0.20}, gain=0.012)
    v = check(buy(EQ * 0.10, market="BTCTMN"), c)
    assert v.action == "resize" and "G_CUSHION" in v.rules
    assert v.size_tmn == pytest.approx(EQ * (0.012 - 0.003 - 0.20 * 0.031) / 0.031, rel=1e-6)


def test_cushion_with_a_tactical_order_uses_the_tactical_stop():
    c = ctx({("USDTTMN", "core"): 0.20}, gain=0.010)
    v = check(buy(EQ * 0.15, sleeve="tactical", market="BTCTMN"), c)
    assert v.size_tmn == pytest.approx(EQ * (0.010 - 0.003 - 0.20 * 0.031) / (0.015 + 0.006), rel=1e-6)


def test_cushion_counts_pending_buys():
    c = ctx({("USDTTMN", "core"): 0.20}, gain=0.012, pending_buy={("BTCTMN", "core"): EQ * 0.05})
    v = check(buy(EQ * 0.10, market="BTCTMN"), c)
    assert v.size_tmn == pytest.approx(EQ * (0.012 - 0.003 - 0.25 * 0.031) / 0.031, rel=1e-6)   # ≈ 4%, not 9%


# ---- sells: always allowed, dust-safe, no double-selling -----------------------
def test_sells_pass_even_when_everything_is_on_fire():
    c = ctx({("USDTTMN", "core"): 0.2}, halted=True, gain=-0.05, daily_pnl=-0.05, flatten_phase="market",
            data_age_s={}, spread_ratio={"USDTTMN": 10}, equity=math.nan)
    v = check(sell(EQ * 0.2, kind="stop"), c)
    assert v.ok and v.size_tmn == pytest.approx(EQ * 0.2)


def test_sell_capped_to_what_the_sleeve_holds():
    v = check(sell(2_000_000), ctx({("USDTTMN", "core"): 0.2}))
    assert v.size_tmn == 1_000_000 and "G_SELL_CAPPED_TO_HELD" in v.rules


def test_pending_sell_reduces_what_can_be_sold():
    c = ctx({("USDTTMN", "core"): 0.2}, pending_sell={("USDTTMN", "core"): 1_000_000})
    assert check(sell(500_000), c).rules == ("G_NOTHING_TO_SELL",)


def test_sell_never_leaves_dust():
    v = check(sell(950_000), ctx({("USDTTMN", "core"): 0.2}))
    assert v.size_tmn == 1_000_000 and "G_DUST_CLOSE_ALL" in v.rules
    v60 = check(sell(940_000), ctx({("USDTTMN", "core"): 0.2}))         # leaves exactly 60k → allowed
    assert v60.size_tmn == 940_000


def test_sell_of_unsellable_dust_is_blocked_and_named():
    v = check(sell(30_000), ctx({("USDTTMN", "core"): 30_000 / EQ}))
    assert v.action == "block" and "G_BELOW_EXCHANGE_MIN" in v.rules


def test_cannot_sell_other_sleeves_coins():
    v = check(sell(500_000, sleeve="tactical"), ctx({("USDTTMN", "core"): 0.2}))
    assert v.action == "block" and "G_NOTHING_TO_SELL" in v.rules


def test_direction_must_match_kind():
    bad_buy = replace(sell(500_000, kind="exit"), side="BUY")
    assert check(bad_buy, ctx({("USDTTMN", "core"): 0.2})).action == "block"
    bad_sell = replace(buy(500_000), side="SELL")
    assert check(bad_sell, ctx()).action == "block"


def test_gate_is_deterministic():
    c, p = ctx({("USDTTMN", "core"): 0.2}, gain=0.01), buy(EQ * 0.1, market="BTCTMN")
    assert check(p, c) == check(p, c)
