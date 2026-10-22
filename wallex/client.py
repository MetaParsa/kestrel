"""Thin Wallex REST client: public market data + read-only account calls.

Order placement and cancellation are intentionally NOT here yet (build step C2
adds them behind the risk gate). Nothing in this module can change account state.

Secrets: the API key is only ever placed in the X-API-Key header. It is never
logged, never included in exception messages, and never stored on disk.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import httpx

BASE_URL = "https://api.wallex.ir"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class WallexError(Exception):
    """API returned an error we should not retry (4xx other than 429, success=false)."""

    def __init__(self, message: str, *, status: int | None = None, path: str = ""):
        super().__init__(f"{message} (status={status}, path={path})")
        self.status = status
        self.path = path


class WallexUnavailable(WallexError):
    """Retries exhausted on 429/5xx/network errors."""


@dataclass(frozen=True)
class MarketRules:
    symbol: str
    base: str
    quote: str
    step_decimals: int      # Wallex "stepSize" is a count of decimals, not an increment
    tick_decimals: int      # same for "tickSize"
    min_qty: Decimal
    min_notional: Decimal
    market_orders_enabled: bool


@dataclass(frozen=True)
class Depth:
    bids: list[tuple[float, float]]   # (price, quantity), best first
    asks: list[tuple[float, float]]
    fetched_at: float                 # unix seconds, local clock

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None


@dataclass(frozen=True)
class Balance:
    asset: str
    value: Decimal      # total
    locked: Decimal

    @property
    def free(self) -> Decimal:
        return self.value - self.locked


@dataclass(frozen=True)
class Order:
    client_id: str
    symbol: str
    side: str
    type: str
    status: str
    active: bool
    price: Decimal | None
    orig_qty: Decimal
    executed_qty: Decimal
    executed_sum: Decimal
    executed_price: Decimal | None
    raw: dict = field(repr=False, compare=False, default_factory=dict)


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def _load_key() -> str | None:
    key = os.environ.get("WALLEX_API_KEY")
    if key:
        return key.strip()
    path = os.environ.get("WALLEX_KEY_FILE")
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip() or None
    return None


class WallexClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
        min_interval: float = 0.25,     # <= 4 requests/s, well under Wallex's 100/s
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._key = api_key
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                  headers={"User-Agent": "wallex-agent/0.1"})
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_request = -1e9
        self.requests_made = 0

    @classmethod
    def from_env(cls, **kwargs) -> "WallexClient":
        return cls(api_key=_load_key(), **kwargs)

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def close(self) -> None:
        self._http.close()

    # ---- transport ---------------------------------------------------------

    def _throttle(self) -> None:
        wait = self._min_interval - (self._clock() - self._last_request)
        if wait > 0:
            self._sleep(wait)
        self._last_request = self._clock()

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 private: bool = False, allow_404: bool = False) -> dict | None:
        headers = {}
        if private:
            if not self._key:
                raise WallexError("no API key configured", path=path)
            headers["X-API-Key"] = self._key
        last_problem = ""
        for attempt in range(self._max_retries + 1):
            self._throttle()
            self.requests_made += 1
            try:
                r = self._http.request(method, path, params=params, headers=headers)
            except httpx.TransportError as e:
                last_problem = type(e).__name__
            else:
                if r.status_code == 404 and allow_404:
                    return None
                if r.status_code in RETRY_STATUSES:
                    last_problem = f"HTTP {r.status_code}"
                    retry_after = r.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit() and attempt < self._max_retries:
                        self._sleep(min(float(retry_after), 30.0))
                        continue
                elif r.status_code >= 400:
                    raise WallexError(_error_message(r), status=r.status_code, path=path)
                else:
                    body = r.json()
                    if isinstance(body, dict) and body.get("success") is False:
                        raise WallexError(str(body.get("message", "success=false")),
                                          status=r.status_code, path=path)
                    return body
            if attempt < self._max_retries:
                self._sleep(min(0.5 * 2 ** attempt, 8.0))
        raise WallexUnavailable(f"gave up after {self._max_retries + 1} tries: {last_problem}", path=path)

    # ---- public market data ------------------------------------------------

    def markets(self) -> dict[str, dict]:
        body = self._request("GET", "/v1/markets")
        return body["result"]["symbols"]

    def market_rules(self, symbol: str, markets: dict[str, dict] | None = None) -> MarketRules:
        m = (markets or self.markets())[symbol]
        return MarketRules(
            symbol=symbol, base=m["baseAsset"], quote=m["quoteAsset"],
            step_decimals=int(m["stepSize"]), tick_decimals=int(m["tickSize"]),
            min_qty=Decimal(str(m["minQty"])), min_notional=Decimal(str(m["minNotional"])),
            market_orders_enabled=bool(m.get("isMarketTypeEnable", False)),
        )

    def depth(self, symbol: str) -> Depth:
        body = self._request("GET", "/v1/depth", params={"symbol": symbol})
        res = body["result"]
        side = lambda rows: [(float(x["price"]), float(x["quantity"])) for x in rows or []]
        bids = sorted(side(res.get("bid")), key=lambda x: -x[0])
        asks = sorted(side(res.get("ask")), key=lambda x: x[0])
        return Depth(bids=bids, asks=asks, fetched_at=time.time())

    def trades(self, symbol: str) -> list[dict]:
        body = self._request("GET", "/v1/trades", params={"symbol": symbol})
        return body["result"]["latestTrades"]

    def candles(self, symbol: str, resolution: str, frm: int, to: int) -> dict[str, list[float]]:
        """UDF history. Returns columns t,o,h,l,c,v (floats; t as int seconds).

        Quirks (research/wallex/FINDINGS.md §7): > 1440 one-minute bars -> s="no_data";
        page 1 day at a time for resolution "1".
        """
        body = self._request("GET", "/v1/udf/history",
                             params={"symbol": symbol, "resolution": resolution, "from": frm, "to": to})
        if body.get("s") != "ok" or not body.get("t"):
            return {k: [] for k in "tohlcv"}
        out = {k: [float(x) for x in body[k]] for k in "ohlcv"}
        out["t"] = [int(float(x)) for x in body["t"]]
        return out

    # ---- private, read-only ------------------------------------------------

    def balances(self) -> dict[str, Balance]:
        body = self._request("GET", "/v1/account/balances", private=True)
        return {a: Balance(asset=a, value=Decimal(str(b["value"])), locked=Decimal(str(b["locked"])))
                for a, b in body["result"]["balances"].items()}

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        params = {"symbol": symbol} if symbol else None
        body = self._request("GET", "/v1/account/openOrders", params=params, private=True)
        orders = [_parse_order(o) for o in body["result"].get("orders", [])]
        return [o for o in orders if symbol is None or o.symbol == symbol]

    def order(self, client_id: str) -> Order | None:
        """Look an order up by our client id. None if Wallex doesn't know it.

        Used before any retry of a timed-out POST so we never place a duplicate.
        """
        body = self._request("GET", f"/v1/account/orders/{client_id}", private=True, allow_404=True)
        if body is None or not body.get("result"):
            return None
        return _parse_order(body["result"])

    def fees(self) -> dict[str, tuple[Decimal, Decimal]]:
        """{symbol: (maker_rate, taker_rate)} as fractions (0.0025 = 0.25%)."""
        body = self._request("GET", "/v1/account/fee", private=True)
        return {s: (Decimal(v["makerFeeRate"]), Decimal(v["takerFeeRate"]))
                for s, v in body["result"].items() if isinstance(v, dict) and "makerFeeRate" in v}


def _parse_order(o: dict) -> Order:
    return Order(
        client_id=str(o.get("clientOrderId") or o.get("client_id") or ""),
        symbol=o["symbol"], side=str(o["side"]).upper(), type=str(o["type"]).upper(),
        status=str(o.get("status", "")).upper(), active=bool(o.get("active", False)),
        price=_dec(o.get("price")), orig_qty=_dec(o.get("origQty")) or Decimal(0),
        executed_qty=_dec(o.get("executedQty")) or Decimal(0),
        executed_sum=_dec(o.get("executedSum")) or Decimal(0),
        executed_price=_dec(o.get("executedPrice")), raw=o,
    )


def _error_message(r: httpx.Response) -> str:
    try:
        body = r.json()
        if isinstance(body, dict):
            return str(body.get("message") or body.get("result") or "error")[:300]
    except ValueError:
        pass
    return r.text[:300] or "error"
