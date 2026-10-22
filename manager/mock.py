"""Deterministic rule-based manager: same bundle → same playbook, no API calls.

Used for `--mock-llm` runs (judges re-running without keys), for the shadow
baseline (spec v2 §9: rule-based regime + regime defaults), and in tests.

Rule: look at USDTTMN and BTCTMN 24 h / 4 h returns and 24 h volatility from the bundle.
"""
from __future__ import annotations

from manager.schema import Forecast, PerMarketBool, PerMarketFloat, PerMarketForecast, PlaybookOut
import config


def _stats(bundle: dict, market: str) -> dict:
    return bundle["markets"][market]["stats"]


def mock_playbook(bundle: dict) -> PlaybookOut:
    u, b = _stats(bundle, "USDTTMN"), _stats(bundle, "BTCTMN")
    shock = max(abs(u["ret_1h"]) / max(u["vol_1h_30d"], 1e-6), abs(b["ret_1h"]) / max(b["vol_1h_30d"], 1e-6)) > 3
    if shock:
        regime = "event_shock"
    elif u["ret_24h"] < -0.01 and b["ret_24h"] < -0.02:
        regime = "risk_off"
    elif u["ret_24h"] > 0.002 and u["ret_4h"] >= 0:
        regime = "devaluation_drift"
    elif b["ret_24h"] > 0.01 and b["ret_4h"] > 0:
        regime = "trend_up"
    else:
        regime = "range"
    rule = config.REGIMES[regime]
    targets = dict(rule.default)
    if regime == "event_shock":       # hold what we have, add nothing (engine enforces it too)
        held = bundle.get("portfolio", {}).get("core_exposure", {})
        targets = {m: min(float(held.get(m, 0.0)), rule.bounds[m][1]) for m in config.MARKETS}

    def fc(s: dict) -> Forecast:
        d = "up" if s["ret_4h"] > 0.002 else "down" if s["ret_4h"] < -0.002 else "flat"   # persistence
        ratio = s["vol_24h"] / max(s["vol_1h_30d"], 1e-9)
        v = "high" if ratio > 1.3 else "low" if ratio < 0.7 else "normal"
        return Forecast(direction=d, vol=v)

    return PlaybookOut(
        regime=regime,
        core_targets=PerMarketFloat(**targets),
        tactical_enabled=PerMarketBool(USDTTMN=rule.tactical, BTCTMN=rule.tactical),
        risk_multiplier=1.0, valid_minutes=60,
        forecast_4h=PerMarketForecast(USDTTMN=fc(u), BTCTMN=fc(b)),
        text_inputs_used=[], signals_cited=[f"USDTTMN ret_24h={u['ret_24h']:+.4f}", f"BTCTMN ret_24h={b['ret_24h']:+.4f}"],
        rationale_fa=f"حالت «{regime}» با قاعده‌ی ثابت (بدون مدل زبانی): بازده ۲۴ ساعته‌ی تتر "
                     f"{u['ret_24h']:+.2%} و بیت‌کوین {b['ret_24h']:+.2%}.",
    )
