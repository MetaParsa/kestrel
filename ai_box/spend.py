"""Spend ledger for Claude calls: every call's usage and dollar cost, plus a hard cap.

Prices (USD per million tokens, Anthropic first-party, from the claude-api skill's
model table cached 2026-10-06). Cache writes are billed at 2× input (1-hour TTL).
Unknown models (e.g. a server-side fallback) are costed at the most expensive known
rate, so the ledger never under-counts.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

PRICES = {  # model: (input, output, cache_read)
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
}
WORST = max(PRICES.values(), key=lambda p: p[1])
CACHE_WRITE_MULT = 2.0


class SpendCapReached(RuntimeError):
    pass


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


def cost_usd(model: str, u: Usage) -> float:
    pin, pout, pread = PRICES.get(model, WORST)
    return (u.input_tokens * pin + u.cache_creation_input_tokens * pin * CACHE_WRITE_MULT
            + u.cache_read_input_tokens * pread + u.output_tokens * pout) / 1e6


class SpendLedger:
    def __init__(self, path: Path, cap_usd: float):
        self.path, self.cap = path, cap_usd

    def total(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                total += json.loads(line)["cost_usd"]
            except (ValueError, KeyError):
                total += WORST[1] * 16_000 / 1e6        # a torn line: assume a worst-case call happened
        return total

    def check(self, worst_case_usd: float) -> None:
        """Refuse a call that could push total spend past the cap."""
        spent = self.total()
        if spent + worst_case_usd > self.cap:
            raise SpendCapReached(f"spent ${spent:.4f} + worst case ${worst_case_usd:.4f} > cap ${self.cap:.2f}")

    def record(self, *, model: str, usage: Usage, request_id: str, purpose: str,
               charge_usd: float | None = None) -> float:
        """charge_usd overrides the usage-based cost (e.g. a timed-out request costed at worst case)."""
        c = cost_usd(model, usage) if charge_usd is None else charge_usd
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "model": model, "request_id": request_id, "purpose": purpose,
                                "usage": usage.__dict__, "cost_usd": c}) + "\n")
        return c


def worst_case_call(model: str, est_input_tokens: int, max_tokens: int) -> float:
    pin, pout, _ = PRICES.get(model, WORST)
    return (est_input_tokens * pin * CACHE_WRITE_MULT + max_tokens * pout) / 1e6
