"""Phone alerts through a Bale bot (domestic, keeps working during international cuts).

Setup (user): create a bot with @botfather in Bale, send it /start from your phone,
save the token on the server with deploy/set_secret.sh BALE_TOKEN, then
    uv run python -m alerts.bale --whoami
prints your chat id (saved as BALE_CHAT_ID).

Sending never blocks the trading loop: messages go to a queue and a background thread
delivers them. Critical messages (force=True) are retried for up to 6 hours; others a few
times. Deduplication is per key (default 30 min). The token is only ever part of the
request URL; it is never logged. `sync=True` (watchdog, tests) sends inline instead.
"""
from __future__ import annotations

import argparse
import os
import queue
import threading
import time
from typing import Callable

import httpx

API = "https://tapi.bale.ai/bot{token}/{method}"
CRITICAL_RETRY_FOR_S = 6 * 3600
NORMAL_TRIES = 3


class Alerter:
    def __init__(self, token: str | None, chat_id: str | None, *, transport: httpx.BaseTransport | None = None,
                 dedupe_s: float = 1800.0, clock: Callable[[], float] = time.time,
                 on_error: Callable[[str], None] = lambda e: None, sync: bool = False,
                 sleep: Callable[[float], None] = time.sleep):
        self._token, self.chat_id = token, chat_id
        self._http = httpx.Client(timeout=10.0, transport=transport)
        self.dedupe_s, self.clock, self.on_error, self.sync, self._sleep = dedupe_s, clock, on_error, sync, sleep
        self._last: dict[str, float] = {}
        self.sent: list[str] = []
        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._token and self.chat_id)

    @classmethod
    def from_env(cls, **kw) -> "Alerter":
        return cls(os.environ.get("BALE_TOKEN"), os.environ.get("BALE_CHAT_ID"), **kw)

    def send(self, text: str, *, key: str | None = None, force: bool = False) -> bool:
        """Sync mode: True if delivered. Queue mode: True if queued (delivery happens in the background)."""
        key = key or text
        now = self.clock()
        if not force and now - self._last.get(key, -1e18) < self.dedupe_s:
            return False
        if not self.enabled:
            return False
        if self.sync:
            ok = self._deliver(text)
            if ok:
                self._last[key] = now
            return ok
        self._last[key] = now                       # queued: the worker keeps trying
        self._q.put((text, force, now))
        self._ensure_worker()
        return True

    def flush(self, timeout: float = 5.0) -> None:
        """Wait until the queue is empty (tests, shutdown)."""
        end = time.time() + timeout
        while self._q.unfinished_tasks and time.time() < end:
            time.sleep(0.01)

    # ---- delivery ------------------------------------------------------------------------
    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name="bale-alerts", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while True:
            text, critical, queued_at = self._q.get()
            tries = 0
            while True:
                tries += 1
                ok = self._deliver(text)
                if ok:
                    break
                if ok is None:                          # permanent (bad token/chat, 4xx): retrying can't help
                    self.on_error("bale alert dropped: permanent error")
                    break
                if (not critical and tries >= NORMAL_TRIES) or self.clock() - queued_at > CRITICAL_RETRY_FOR_S:
                    self.on_error("bale alert dropped after retries")
                    break
                self._sleep(min(60.0, 5.0 * tries))
            self._q.task_done()

    def _deliver(self, text: str) -> bool | None:
        """True delivered · False worth retrying (network, 429, 5xx) · None permanent failure."""
        try:
            r = self._http.post(API.format(token=self._token, method="sendMessage"),
                                json={"chat_id": self.chat_id, "text": text[:3500]})
            body = r.json() if r.status_code == 200 else None
            ok = isinstance(body, dict) and bool(body.get("ok", False))
        except Exception as e:                      # never let an odd reply kill the worker
            self.on_error(f"bale send failed: {type(e).__name__}")
            return False
        if ok:
            self.sent.append(text)
            return True
        self.on_error(f"bale send failed: HTTP {r.status_code}")
        return False if r.status_code == 429 or r.status_code >= 500 or r.status_code == 200 else None

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
    al = Alerter.from_env(sync=True)
    if a.whoami:
        for c in al.whoami():
            print(c)
    if a.test:
        print("sent" if al.send("✅ پیام آزمایشی ربات معامله‌گر والکس", force=True) else "not sent")


if __name__ == "__main__":
    main()
