"""State-changing Wallex calls (place / cancel), kept apart from the read-only client.

Two locks must both be open before anything is sent:
  1. the caller passes armed=True (only `engine.run --mode live` does), and
  2. the environment has WALLEX_LIVE_ARMED=yes-real-money (set by the user at go-live).
A disarmed client raises before any network I/O.

No automatic retries: a POST whose outcome is unknown (timeout, 5xx) is reported as
such, and the executor looks the order up by client_id before deciding anything.
"""
from __future__ import annotations

import os

import httpx

from wallex.client import BASE_URL, _error_message, _load_key

ARM_PHRASE = "yes-real-money"


class TradingDisarmed(RuntimeError):
    pass


class TradeTimeout(Exception):
    """Outcome unknown: the request may or may not have reached Wallex."""


class TradeRejected(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(f"{message} (status={status})")
        self.status = status


def env_armed() -> bool:
    return os.environ.get("WALLEX_LIVE_ARMED") == ARM_PHRASE


class TradingClient:
    def __init__(self, api_key: str | None, *, armed: bool, base_url: str = BASE_URL,
                 transport: httpx.BaseTransport | None = None, timeout: float = 10.0):
        self._key = api_key
        self.armed = bool(armed and api_key)
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                  headers={"User-Agent": "wallex-agent/0.1"})

    @classmethod
    def from_env(cls, *, armed: bool, **kw) -> "TradingClient":
        return cls(_load_key(), armed=armed and env_armed(), **kw)

    def _send(self, method: str, path: str, **kw) -> dict:
        if not self.armed:
            raise TradingDisarmed("live trading is not armed (needs --mode live and WALLEX_LIVE_ARMED)")
        try:
            r = self._http.request(method, path, headers={"X-API-Key": self._key}, **kw)
        except httpx.TimeoutException as e:
            raise TradeTimeout(type(e).__name__) from e
        except httpx.TransportError as e:
            raise TradeTimeout(type(e).__name__) from e        # can't tell whether it arrived
        if r.status_code >= 500 or r.status_code == 429:
            raise TradeTimeout(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise TradeRejected(_error_message(r), r.status_code)
        try:
            body = r.json()
        except ValueError as e:                 # the request was sent; we just can't read the answer
            raise TradeTimeout("unreadable reply") from e
        if isinstance(body, dict) and body.get("success") is False:
            raise TradeRejected(str(body.get("message", "success=false")), r.status_code)
        return body

    def place(self, *, symbol: str, side: str, type: str, quantity: str, price: str | None,
              client_id: str) -> dict:
        body = {"symbol": symbol, "side": side, "type": type, "quantity": quantity, "client_id": client_id}
        if price is not None:
            body["price"] = price
        return self._send("POST", "/v1/account/orders", json=body).get("result") or {}

    def cancel(self, client_id: str) -> dict:
        """Documented form first: DELETE /v1/account/orders?clientOrderId=… (docs/api-notes.md).
        Fallback: the id in the path (as the community SDK does). Never a bare DELETE."""
        if not client_id:
            raise ValueError("client_id required")
        try:
            return self._send("DELETE", "/v1/account/orders", params={"clientOrderId": client_id})
        except TradeRejected as e:
            if e.status not in (404, 405, 422):
                raise
            return self._send("DELETE", f"/v1/account/orders/{client_id}")
