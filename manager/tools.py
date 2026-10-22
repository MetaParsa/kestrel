"""Read-only tools for the manager. They only read the context bundle the server sent,
so every input Claude saw is in the bundle file and the transcript (exact replay).
No tool can place, cancel or change anything.
"""
from __future__ import annotations

import json

import config


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required if required is not None else list(props),
            "additionalProperties": False}


MARKET = {"type": "string", "enum": list(config.MARKETS)}

TOOLS = [
    {"name": "market_brief", "strict": True,
     "description": "Current quote, spread, order-book imbalance and return/volatility stats for one market.",
     "input_schema": _obj({"market": MARKET})},
    {"name": "candles", "strict": True,
     "description": "Recent OHLCV bars [t_unix, open, high, low, close, volume], oldest first. "
                    "tf '1h' has up to 48 bars, '5m' up to 24.",
     "input_schema": _obj({"market": MARKET, "tf": {"type": "string", "enum": ["1h", "5m"]},
                           "n": {"type": "integer"}})},
    {"name": "portfolio", "strict": True,
     "description": "Equity, return vs start, today's P&L, exposure per market and sleeve, positions, risk mode.",
     "input_schema": _obj({})},
    {"name": "recent_decisions", "strict": True,
     "description": "The engine's latest decisions with owner (decided_by), gate verdict and outcome.",
     "input_schema": _obj({"n": {"type": "integer"}})},
    {"name": "previous_playbooks", "strict": True,
     "description": "Your previous playbooks (newest first): regime, targets, rationale.",
     "input_schema": _obj({"n": {"type": "integer"}})},
    {"name": "forecast_scorecard", "strict": True,
     "description": "How your past 4 h forecasts scored against what happened, vs a naive persistence baseline.",
     "input_schema": _obj({})},
    {"name": "tactical_status", "strict": True,
     "description": "Whether the tactical sleeve is live, its open trades and today's entries.",
     "input_schema": _obj({})},
    {"name": "calendar", "strict": True,
     "description": "Tehran time, weekday, hours to the contest end and to the start of the final sell-off.",
     "input_schema": _obj({})},
]
TOOL_NAMES = {t["name"] for t in TOOLS}


def run_tool(name: str, args: dict, bundle: dict) -> str:
    """Returns JSON text. Unknown tools or bad args return an error string (is_error set by caller)."""
    if name == "market_brief":
        mk = bundle["markets"][args["market"]]
        return _j({"market": args["market"], "snapshot": mk["snapshot"], "stats": mk["stats"]})
    if name == "candles":
        key = "bars_1h" if args["tf"] == "1h" else "bars_5m"
        n = max(1, min(int(args["n"]), len(bundle["markets"][args["market"]][key])))
        return _j(bundle["markets"][args["market"]][key][-n:])
    if name == "portfolio":
        return _j(bundle["portfolio"])
    if name == "recent_decisions":
        return _j(bundle["recent_decisions"][: max(1, min(int(args["n"]), 20))])
    if name == "previous_playbooks":
        return _j(bundle["previous_playbooks"][: max(1, min(int(args["n"]), 5))])
    if name == "forecast_scorecard":
        return _j(bundle["forecast_scorecard"])
    if name == "tactical_status":
        return _j(bundle["tactical"])
    if name == "calendar":
        return _j(bundle["calendar"])
    raise KeyError(name)


def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))
