"""Phone alerts through a Bale bot (domestic, keeps working during international cuts).

Setup (user): create a bot with @botfather in Bale, send it /start from your phone,
put BALE_TOKEN in the server env file, then run
    uv run python -m alerts.bale --whoami
to print your chat id, and add BALE_CHAT_ID to the env file.

Alerts are deduplicated per key (default 30 min) and never raise into the engine.
The token is only ever part of the request URL; it is never logged.
"""
from __future__ import annotations

import argparse
import os
import time
from typing import Callable

import httpx

API = "https://tapi.bale.ai/bot{token}/{method}"


class Alerter:
    def __init__(self, token: str | None, chat_id: str | None, *, transport: httpx.BaseTransport | None = None,
                 dedupe_s: float = 1800.0, clock: Callable[[], float] = time.time,
                 on_error: Callable[[str], None] = lambda e: None):
        self._token, self.chat_id = token, chat_id
        self._http = httpx.Client(timeout=10.0, transport=transport)
        self.dedupe_s, self.clock, self.on_error = dedupe_s, clock, on_error
        self._last: dict[str, float] = {}
        self.sent: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self._token and self.chat_id)

    @classmethod
    def from_env(cls, **kw) -> "Alerter":
        return cls(os.environ.get("BALE_TOKEN"), os.environ.get("BALE_CHAT_ID"), **kw)

    def send(self, text: str, *, key: str | None = None, force: bool = False) -> bool:
        key = key or text
        now = self.clock()
        if not force and now - self._last.get(key, -1e18) < self.dedupe_s:
            return False
        self._last[key] = now
        if not self.enabled:
            return False
        try:
            r = self._http.post(API.format(token=self._token, method="sendMessage"),
                                json={"chat_id": self.chat_id, "text": text[:3500]})
            ok = r.status_code == 200 and r.json().get("ok", False)
        except (httpx.HTTPError, ValueError) as e:
            self.on_error(f"bale send failed: {type(e).__name__}")
            return False
        if ok:
            self.sent.append(text)
        else:
            self.on_error(f"bale send failed: HTTP {r.status_code}")
        return ok

    def whoami(self) -> list[dict]:
        r = self._http.get(API.format(token=self._token, method="getUpdates"))
        chats = {}
        for u in r.json().get("result", []):
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("id"):
                chats[chat["id"]] = {"chat_id": chat["id"], "name": chat.get("first_name") or chat.get("title")}
        return list(chats.values())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--whoami", action="store_true", help="list chat ids that messaged the bot")
    ap.add_argument("--test", action="store_true", help="send a test message")
    a = ap.parse_args(argv)
    al = Alerter.from_env()
    if a.whoami:
        for c in al.whoami():
            print(c)
    if a.test:
        print("sent" if al.send("✅ پیام آزمایشی ربات معامله‌گر والکس", force=True) else "not sent")


if __name__ == "__main__":
    main()
