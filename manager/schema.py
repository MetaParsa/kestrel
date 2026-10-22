"""The Playbook: what Claude (or the mock manager) decides, and how code clamps it.

`PlaybookOut` is what the model returns (structured output). It deliberately has no
numeric bounds in the JSON schema — the server clamps every number to `config`
limits in `finalize()` and logs each clamp, so a bad value can never loosen a limit.
`Playbook` is the validated, server-side version the engine acts on.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

import config

Regime = Literal["devaluation_drift", "trend_up", "range", "risk_off", "event_shock"]
Direction = Literal["up", "flat", "down"]
VolBucket = Literal["low", "normal", "high"]
MAX_VALID_MIN = 90


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PerMarketFloat(_Strict):
    USDTTMN: float
    BTCTMN: float


class PerMarketBool(_Strict):
    USDTTMN: bool
    BTCTMN: bool


class Forecast(_Strict):
    direction: Direction
    vol: VolBucket


class PerMarketForecast(_Strict):
    USDTTMN: Forecast
    BTCTMN: Forecast


class PlaybookOut(_Strict):
    regime: Regime
    core_targets: PerMarketFloat            # fraction of equity, e.g. 0.20
    tactical_enabled: PerMarketBool
    risk_multiplier: float                  # 0–1, can only cut risk
    valid_minutes: int                      # ≤ 90
    forecast_4h: PerMarketForecast
    text_inputs_used: list[str]
    signals_cited: list[str]
    rationale_fa: str


class Playbook(_Strict):
    playbook_id: str
    created_at: float
    valid_until: float
    bundle_hash: str
    source: Literal["claude", "mock", "default"]
    model: str
    regime: Regime
    core_targets: dict[str, float]
    tactical_enabled: dict[str, bool]
    risk_multiplier: float
    forecast_4h: dict[str, Forecast]
    text_inputs_used: list[str]
    signals_cited: list[str]
    rationale_fa: str
    clamps: list[str]
    cost_usd: float = 0.0


def output_json_schema() -> dict:
    """JSON schema for `output_config.format` (additionalProperties: false everywhere)."""
    return PlaybookOut.model_json_schema()


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if not math.isfinite(x) else min(hi, max(lo, x))      # NaN/inf → the safe low end


def finalize(out: PlaybookOut, *, bundle_hash: str, now: float, source: str, model: str,
             cost_usd: float = 0.0) -> Playbook:
    clamps: list[str] = []
    rule = config.REGIMES[out.regime]
    targets = {}
    for m in config.MARKETS:
        want = getattr(out.core_targets, m)
        lo, hi = rule.bounds[m]
        t = _clamp(want, lo, hi)
        if not (abs(t - want) <= 1e-12):
            clamps.append(f"core_targets.{m} {want}→{t:.3f} (regime {out.regime} bounds)")
        targets[m] = t
    mult = _clamp(out.risk_multiplier, 0.0, 1.0)
    if not (mult == out.risk_multiplier):
        clamps.append(f"risk_multiplier {out.risk_multiplier}→{mult}")
    tactical = {m: bool(getattr(out.tactical_enabled, m)) and rule.tactical for m in config.MARKETS}
    for m in config.MARKETS:
        if getattr(out.tactical_enabled, m) and not rule.tactical:
            clamps.append(f"tactical_enabled.{m} true→false (regime {out.regime})")
    minutes = min(MAX_VALID_MIN, max(15, out.valid_minutes))
    if minutes != out.valid_minutes:
        clamps.append(f"valid_minutes {out.valid_minutes}→{minutes}")
    stamp = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(config.TEHRAN).strftime("%Y%m%d-%H%M")
    return Playbook(
        playbook_id=f"pb-{stamp}-{bundle_hash[:6]}", created_at=now, valid_until=now + minutes * 60,
        bundle_hash=bundle_hash, source=source, model=model, regime=out.regime, core_targets=targets,
        tactical_enabled=tactical, risk_multiplier=mult,
        forecast_4h={m: getattr(out.forecast_4h, m) for m in config.MARKETS},
        text_inputs_used=out.text_inputs_used[:10], signals_cited=out.signals_cited[:15],
        rationale_fa=out.rationale_fa[:600], clamps=clamps, cost_usd=cost_usd,
    )


def server_validate(pb: Playbook, *, accepted_at: float) -> tuple[Playbook, list[str]]:
    """Re-apply every bound on the SERVER: the laptop's clamps are never trusted.

    - exactly our markets as keys, all numbers finite;
    - targets inside the regime bounds, multiplier in [0, 1], tactical off where the regime forbids it;
    - validity re-based on the server's clock: valid_until = accepted_at + min(90 min, claimed duration),
      so a laptop clock that runs fast or slow can't stretch or kill a playbook.
    Raises ValueError for structural problems (missing/extra markets).
    """
    markets = set(config.MARKETS)
    for name in ("core_targets", "tactical_enabled", "forecast_4h"):
        if set(getattr(pb, name)) != markets:
            raise ValueError(f"{name} must have exactly {sorted(markets)}")
    rule = config.REGIMES[pb.regime]
    clamps = []
    targets = {}
    for m in config.MARKETS:
        lo, hi = rule.bounds[m]
        t = _clamp(pb.core_targets[m], lo, hi)
        if not (abs(t - pb.core_targets[m]) <= 1e-12):
            clamps.append(f"server: core_targets.{m} {pb.core_targets[m]}→{t}")
        targets[m] = t
    mult = _clamp(pb.risk_multiplier, 0.0, 1.0)
    if not (mult == pb.risk_multiplier):
        clamps.append(f"server: risk_multiplier {pb.risk_multiplier}→{mult}")
    tactical = {m: bool(pb.tactical_enabled[m]) and rule.tactical for m in config.MARKETS}
    if tactical != pb.tactical_enabled:
        clamps.append("server: tactical_enabled forced off by regime")
    claimed = pb.valid_until - pb.created_at
    minutes = MAX_VALID_MIN if not math.isfinite(claimed) else min(MAX_VALID_MIN, max(15.0, claimed / 60))
    fixed = pb.model_copy(update={"core_targets": targets, "risk_multiplier": mult, "tactical_enabled": tactical,
                                  "valid_until": accepted_at + minutes * 60,
                                  "clamps": pb.clamps + clamps})
    return fixed, clamps


def canonical_json(obj) -> str:
    """Stable serialisation (sorted keys, no whitespace drift) — used for hashes and prompt caching."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
