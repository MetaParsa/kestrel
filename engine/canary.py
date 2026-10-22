"""Canary: one tiny real order that can't fill, to confirm Wallex's order format before trading.

Places a minimum-size LIMIT BUY on USDTTMN at 10% below the bid (it won't fill), reads it
back, cancels it, reads it back again, and saves every raw reply to run/canary.json so the
assumed field names (and tests/fixtures) can be checked against reality.

It IS a real order, so it needs both live locks, like the engine:
    WALLEX_LIVE_ARMED=yes-real-money uv run python -m engine.canary
Run it only at go-live, with the user's OK.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

from data.poll import Poller
from engine.live import LiveBroker
from wallex.client import WallexClient
from wallex.trading import TradingClient, env_armed

MARKET = "USDTTMN"


def _tmn(reader) -> dict:
    b = reader.balances().get("TMN")
    return {"free": str(b.free), "locked": str(b.locked)} if b else {}


def main() -> None:
    rest_s = float(sys.argv[sys.argv.index("--rest") + 1]) if "--rest" in sys.argv else 1.5
    if not env_armed():
        raise SystemExit("refusing: WALLEX_LIVE_ARMED is not set")
    reader = WallexClient.from_env()
    trader = TradingClient.from_env(armed=True)
    notes: list[dict] = []
    broker = LiveBroker(reader, trader, on_note=notes.append)
    snaps = Poller(reader, [MARKET]).poll_once()
    broker.feed(snaps)
    bid = snaps[MARKET].bid
    price = math.floor(bid * 0.90)
    r = broker.rules(MARKET)
    qty = math.ceil(r.min_notional * 1.05 / price * 10**r.step_decimals) / 10**r.step_decimals
    cid = f"d-canary{int(time.time()) % 10**8:08d}"
    out: dict = {"market": MARKET, "bid": bid, "price": price, "qty": qty, "client_id": cid,
                 "tmn_before": _tmn(reader)}
    st = broker.place(cid, MARKET, "BUY", "LIMIT", qty, price)
    out["after_place"] = st.__dict__
    print(json.dumps({"resting": cid, "for_s": rest_s, "placed_at": time.time()}), flush=True)
    time.sleep(rest_s)                   # while it rests: check which account shows the TMN as locked
    out["tmn_while_resting"] = _tmn(reader)
    o = reader.order(cid)
    out["read_back_raw"] = o.raw if o else None
    out["after_cancel"] = (broker.cancel(cid) or st).__dict__
    time.sleep(1.5)
    o2 = reader.order(cid)
    out["final_raw"] = o2.raw if o2 else None
    out["open_orders_after"] = [x.client_id for x in reader.open_orders()]
    out["tmn_after"] = _tmn(reader)
    out["notes"] = notes
    Path("run").mkdir(exist_ok=True)
    Path("run/canary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    ok = out["after_cancel"]["status"] == "CANCELED" and cid not in out["open_orders_after"]
    print(json.dumps({"ok": ok, "client_id": cid, "tmn": [out["tmn_before"], out["tmn_while_resting"], out["tmn_after"]], "status_after_cancel": out["after_cancel"]["status"],
                      "still_open": cid in out["open_orders_after"], "notes": notes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
