"""Small shared types for the engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Side = Literal["BUY", "SELL"]
Sleeve = Literal["core", "tactical"]
Urgency = Literal["passive", "aggressive", "market"]
# Why an order exists. Entries/adds/rebalance_up/floor increase risk; the rest reduce it.
Kind = Literal["entry", "add", "rebalance_up", "floor", "rebalance_down", "exit", "stop",
               "time_stop", "take_profit", "flatten"]
RISK_INCREASING = {"entry", "add", "rebalance_up", "floor"}


@dataclass(frozen=True)
class Proposal:
    """A trade some layer wants. The gate decides whether, and how big."""
    market: str
    sleeve: Sleeve
    side: Side
    size_tmn: float
    kind: Kind
    decided_by: str                  # manager | lgbm | clef | gate | flatten | floor
    decision_id: str
    urgency: Urgency = "passive"
    qty: float | None = None         # base qty; required for SELLs (exact held qty when closing)

    @property
    def increases_risk(self) -> bool:
        return self.kind in RISK_INCREASING


URGENCY_RANK = {"passive": 0, "aggressive": 1, "market": 2}


@dataclass(frozen=True)
class Fill:
    client_id: str
    market: str
    sleeve: Sleeve
    side: Side
    qty: float                       # base asset, gross
    price: float                     # TMN per base
    fee: float                       # in fee_asset units
    fee_asset: str                   # the asset received: base on BUY, TMN on SELL
    ts: float                        # unix seconds
    decision_id: str = ""
    kind: str = ""                   # the proposal's kind (counters count FILLED decisions, not accepted ones)
