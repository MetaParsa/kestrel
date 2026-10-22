"""Brokers: one engine, three ways to get fills (spec v2 §9).

- ShadowBroker: live quotes from the poller, fills simulated by SimExchange.
- BacktestBroker: historical 1-minute bars, fills simulated by SimExchange.
- LiveBroker (engine/live.py): real Wallex orders — only with `--mode live` AND
  WALLEX_LIVE_ARMED=yes-real-money, set by the user at go-live.
"""
from __future__ import annotations

import config
from data.poll import MarketSnapshot
from engine.exchange import SimExchange


class ShadowBroker(SimExchange):
    """Feed it every poller snapshot; resting orders fill when quotes cross them."""

    def feed(self, snaps: dict[str, MarketSnapshot]) -> None:
        for m, s in snaps.items():
            if m in config.MARKETS:
                self.set_quote(m, s.bid, s.ask, s.fetched_at)


class BacktestBroker(SimExchange):
    """Feed it one 1-minute bar per market per step.

    The quote is the bar's close ± a typical half-spread (FINDINGS §1). Before the
    quote moves, resting limits get a chance to fill if the bar traded through them.
    A minute without trades (v == 0) moves the quote but fills nothing passively.
    """

    def __init__(self, *a, half_spread: dict[str, float] | None = None, **kw):
        super().__init__(*a, **kw)
        self.half_spread = half_spread or config.SIM_HALF_SPREAD

    def feed_bar(self, market: str, ts: float, o: float, h: float, l: float, c: float, v: float) -> None:
        if v > 0 and market in self._quotes:
            self.trade_through(market, l, h)
        hs = self.half_spread[market]
        self.set_quote(market, c * (1 - hs), c * (1 + hs), ts)
