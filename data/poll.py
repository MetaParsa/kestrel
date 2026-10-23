"""Live market snapshots by REST polling (depth + recent trades every ~10 s).

No socket.io in the core: polling is simpler and its failure
mode — a stale snapshot — is exactly what the gate already guards against.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from wallex.client import Depth, WallexClient, WallexError

IMBALANCE_BAND = 0.003   # ±0.3% around mid


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    bid: float
    ask: float
    fetched_at: float                 # unix seconds when depth was read
    imbalance: float                  # (bid qty − ask qty) / total within ±0.3% of mid, in [−1, 1]
    last_trade_price: float | None
    last_trade_at: float | None       # unix seconds

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 1e4


def depth_imbalance(depth: Depth, band: float = IMBALANCE_BAND) -> float:
    if not depth.bids or not depth.asks:
        return 0.0
    mid = (depth.best_bid + depth.best_ask) / 2
    bq = sum(q for p, q in depth.bids if p >= mid * (1 - band))
    aq = sum(q for p, q in depth.asks if p <= mid * (1 + band))
    return 0.0 if bq + aq == 0 else (bq - aq) / (bq + aq)


def snapshot_from(symbol: str, depth: Depth, trades: list[dict]) -> MarketSnapshot:
    if depth.best_bid is None or depth.best_ask is None:
        raise WallexError("empty order book", path=f"/v1/depth?symbol={symbol}")
    if depth.best_bid <= 0 or depth.best_ask <= 0 or depth.best_bid >= depth.best_ask:
        raise WallexError(f"unusable order book (bid {depth.best_bid}, ask {depth.best_ask})",
                          path=f"/v1/depth?symbol={symbol}")
    last_p = last_t = None
    if trades:
        newest = max(trades, key=lambda x: x["timestamp"])
        last_p = float(newest["price"])
        last_t = datetime.fromisoformat(newest["timestamp"].replace("Z", "+00:00")).timestamp()
    return MarketSnapshot(symbol=symbol, bid=depth.best_bid, ask=depth.best_ask, fetched_at=depth.fetched_at,
                          imbalance=depth_imbalance(depth), last_trade_price=last_p, last_trade_at=last_t)


class Poller:
    """Keeps the latest snapshot per symbol; errors are counted, never raised into the engine."""

    def __init__(self, client: WallexClient, symbols: list[str], interval_s: float = 10.0,
                 on_error: Callable[[str, Exception], None] | None = None):
        self.client, self.symbols, self.interval_s = client, symbols, interval_s
        self.latest: dict[str, MarketSnapshot] = {}
        self.errors: deque[tuple[float, str, str]] = deque(maxlen=2000)   # (unix time, symbol, error class)
        self._on_error = on_error
        self._lock = threading.Lock()

    def poll_once(self) -> dict[str, MarketSnapshot]:
        for s in self.symbols:
            try:
                snap = snapshot_from(s, self.client.depth(s), self.client.trades(s))
            except Exception as e:                  # any failure on one market leaves the others usable
                self.errors.append((time.time(), s, type(e).__name__))
                if self._on_error:
                    self._on_error(s, e)
                continue
            with self._lock:
                self.latest[s] = snap
        return self.snapshot()

    def snapshot(self) -> dict[str, MarketSnapshot]:
        """Thread-safe copy of the latest snapshots (the engine reads this; the poller thread writes)."""
        with self._lock:
            return dict(self.latest)

    def errors_in_window(self, window_s: float, now: float | None = None) -> int:
        now = now or time.time()
        return sum(1 for t, _, _ in self.errors if now - t <= window_s)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            started = time.monotonic()
            self.poll_once()
            stop.wait(max(0.0, self.interval_s - (time.monotonic() - started)))
