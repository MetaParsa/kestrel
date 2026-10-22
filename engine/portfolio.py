"""Positions per sleeve, equity in TMN, drawdown, the day's counters, and persisted state.

Both sleeves hold the same coins on the exchange; the split is our own bookkeeping,
attributed by the sleeve each order was placed for. `reconcile()` (C4) checks the
total against real balances.

Fee model (assumption, verify on the first real fill): Wallex takes the fee from
the asset you receive — base asset on a BUY, TMN on a SELL.

Valuation: callers pass BID prices so equity and gain are conservative (what we could
sell for), not mid.

Counters change in exactly one place, `note_accept()`, called by the executor when an
order is accepted. State survives restarts via `save()` / `load()` (day-start equity,
counters, cooldowns, the hard-flatten latch, positions).
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import config
from engine.types import RISK_INCREASING, Fill, Proposal, Sleeve


@dataclass
class Position:
    market: str
    sleeve: Sleeve
    qty: float = 0.0
    cost_tmn: float = 0.0            # TMN spent on what we still hold (fees included via smaller qty)
    opened_at: float | None = None
    peak_price: float = 0.0          # highest price seen since entry (tactical trailing)

    @property
    def avg_price(self) -> float:
        return self.cost_tmn / self.qty if self.qty > 0 else 0.0

    def value(self, price: float) -> float:
        return self.qty * price


def tehran_date(ts: float) -> date:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(config.TEHRAN).date()


class Portfolio:
    def __init__(self, cash_tmn: float, now: float, start_equity: float = config.START_EQUITY_TMN,
                 day_start_equity: float | None = None):
        self.cash = cash_tmn
        self.start_equity = start_equity
        self.positions: dict[tuple[str, str], Position] = {}
        self.realized_pnl = 0.0
        self.fees_tmn = 0.0
        self.day = tehran_date(now)
        self.day_start_equity = day_start_equity if day_start_equity is not None else cash_tmn
        self.peak_equity = max(self.day_start_equity, start_equity)
        self.max_drawdown = 0.0
        self.halted = False                       # latched by a hard flatten; only the user clears it
        self.halt_reason = ""
        self.core_stopped_at: dict[str, float] = {}
        self._reset_counters()

    def _reset_counters(self) -> None:
        self.tactical_entries: dict[str, int] = defaultdict(int)
        self.core_increases: dict[str, int] = defaultdict(int)
        self.entries_today = 0        # filled risk-increasing decisions (micro mode limit)
        self.activity_today = 0       # filled decisions of any kind (activity floor)
        self.fills_today = 0
        self.counted_decisions: set[str] = set()

    # ---- positions ----------------------------------------------------------
    def pos(self, market: str, sleeve: Sleeve) -> Position:
        key = (market, sleeve)
        if key not in self.positions:
            self.positions[key] = Position(market, sleeve)
        return self.positions[key]

    def qty(self, market: str) -> float:
        return sum(p.qty for (m, _), p in self.positions.items() if m == market)

    def sleeve_values(self, prices: dict[str, float]) -> dict[tuple[str, str], float]:
        return {k: p.value(prices[k[0]]) for k, p in self.positions.items() if p.qty > 0}

    def sell_qty(self, market: str, sleeve: Sleeve, size_tmn: float, price: float, *, close_all: bool) -> float:
        """Base qty for a sell: the exact held qty when closing (no leftovers from price rounding)."""
        held = self.pos(market, sleeve).qty
        if close_all or size_tmn >= held * price * 0.999:
            return held
        return min(held, size_tmn / price)

    def apply_fill(self, f: Fill) -> None:
        p = self.pos(f.market, f.sleeve)
        notional = f.qty * f.price
        if f.side == "BUY":
            received = max(0.0, f.qty - (f.fee if f.fee_asset != "TMN" else 0.0))
            self.cash -= notional + (f.fee if f.fee_asset == "TMN" else 0.0)
            if p.qty == 0:
                p.opened_at, p.peak_price = f.ts, f.price
            p.qty += received
            p.cost_tmn += notional
            self.fees_tmn += f.fee * (f.price if f.fee_asset != "TMN" else 1.0)
        else:
            sold = min(f.qty, p.qty)
            fee_tmn = f.fee if f.fee_asset == "TMN" else f.fee * f.price
            proceeds = sold * f.price - fee_tmn
            cost_out = p.avg_price * sold
            self.cash += proceeds
            self.realized_pnl += proceeds - cost_out
            self.fees_tmn += fee_tmn
            p.qty -= sold
            p.cost_tmn -= cost_out
            if p.qty <= 1e-12:
                p.qty, p.cost_tmn, p.opened_at, p.peak_price = 0.0, 0.0, None, 0.0
        self.fills_today += 1

    def note_accept(self, p: Proposal, now: float) -> None:
        """An order was accepted by the exchange. Only the stop cooldown starts here; the day's
        counters change when the decision actually FILLS (note_fill), so an unfilled, cancelled
        buy doesn't use up the day's allowance and can be retried."""
        if p.kind == "stop" and p.sleeve == "core":
            self.core_stopped_at[p.market] = now

    def note_fill(self, f: Fill) -> None:
        """First fill of a decision: count it once (entries, per-sleeve increases, activity)."""
        if not f.decision_id or f.decision_id in self.counted_decisions:
            return
        self.counted_decisions.add(f.decision_id)
        self.activity_today += 1                     # any filled trade is activity
        if f.kind in RISK_INCREASING:
            self.entries_today += 1
            if f.sleeve == "tactical":
                self.tactical_entries[f.market] += 1
            else:
                self.core_increases[f.market] += 1

    def latch_halt(self, reason: str) -> None:
        self.halted, self.halt_reason = True, reason

    def mark(self, prices: dict[str, float]) -> None:
        """Update trailing peaks and drawdown bookkeeping with the latest prices."""
        for (m, _), p in self.positions.items():
            if p.qty > 0 and m in prices:
                p.peak_price = max(p.peak_price, prices[m])
        eq = self.equity(prices)
        self.peak_equity = max(self.peak_equity, eq)
        self.max_drawdown = min(self.max_drawdown, eq / self.peak_equity - 1)

    # ---- valuation ----------------------------------------------------------
    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.value(prices[m]) for (m, _), p in self.positions.items() if p.qty > 0)

    def exposure(self, market: str, prices: dict[str, float], sleeve: Sleeve | None = None) -> float:
        eq = self.equity(prices)
        v = sum(p.value(prices[m]) for (m, s), p in self.positions.items()
                if m == market and (sleeve is None or s == sleeve) and p.qty > 0)
        return v / eq if eq > 0 else 0.0

    def gain(self, prices: dict[str, float]) -> float:
        """Return vs the contest's start capital (the scored number)."""
        return self.equity(prices) / self.start_equity - 1

    def daily_pnl(self, prices: dict[str, float]) -> float:
        return self.equity(prices) / self.day_start_equity - 1

    # ---- day roll -------------------------------------------------------------
    def roll_day(self, now: float, prices: dict[str, float]) -> bool:
        d = tehran_date(now)
        if d == self.day:
            return False
        self.day = d
        self.day_start_equity = self.equity(prices)
        self._reset_counters()
        return True

    # ---- persistence ----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "cash": self.cash, "start_equity": self.start_equity, "realized_pnl": self.realized_pnl,
            "fees_tmn": self.fees_tmn, "day": self.day.isoformat(), "day_start_equity": self.day_start_equity,
            "peak_equity": self.peak_equity, "max_drawdown": self.max_drawdown, "halted": self.halted,
            "halt_reason": self.halt_reason, "core_stopped_at": self.core_stopped_at,
            "tactical_entries": dict(self.tactical_entries), "core_increases": dict(self.core_increases),
            "entries_today": self.entries_today, "activity_today": self.activity_today,
            "fills_today": self.fills_today, "counted_decisions": sorted(self.counted_decisions),
            "positions": [asdict(p) for p in self.positions.values() if p.qty > 0],
        }

    @classmethod
    def from_dict(cls, d: dict, now: float) -> "Portfolio":
        pf = cls(d["cash"], now, start_equity=d["start_equity"], day_start_equity=d["day_start_equity"])
        pf.realized_pnl, pf.fees_tmn = d["realized_pnl"], d["fees_tmn"]
        pf.day = date.fromisoformat(d["day"])
        pf.peak_equity, pf.max_drawdown = d["peak_equity"], d["max_drawdown"]
        pf.halted, pf.halt_reason = d["halted"], d["halt_reason"]
        pf.core_stopped_at = dict(d["core_stopped_at"])
        pf.tactical_entries.update(d["tactical_entries"])
        pf.core_increases.update(d["core_increases"])
        pf.entries_today, pf.activity_today, pf.fills_today = d["entries_today"], d["activity_today"], d["fills_today"]
        pf.counted_decisions = set(d.get("counted_decisions", []))
        for p in d["positions"]:
            pf.positions[(p["market"], p["sleeve"])] = Position(**p)
        return pf

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        os.replace(tmp, path)                       # atomic on the same filesystem

    @classmethod
    def load(cls, path: Path, now: float) -> "Portfolio":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")), now)
