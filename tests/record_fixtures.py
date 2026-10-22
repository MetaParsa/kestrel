"""Record real PUBLIC Wallex responses into tests/fixtures/ (trimmed, no secrets).

Run once from the repo root:  uv run python -m tests.record_fixtures
Private-endpoint fixtures are written by hand from read-only MCP output (no keys involved).
"""
import json
import time
from pathlib import Path

import httpx

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)
KEEP = ["USDTTMN", "BTCTMN", "BTCUSDT"]


def save(name, body):
    (OUT / name).write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", name)


def main():
    with httpx.Client(base_url="https://api.wallex.ir", timeout=30) as h:
        m = h.get("/v1/markets").json()
        m["result"]["symbols"] = {k: v for k, v in m["result"]["symbols"].items() if k in KEEP}
        save("markets.json", m)
        time.sleep(0.3)
        d = h.get("/v1/depth", params={"symbol": "USDTTMN"}).json()
        d["result"]["bid"] = d["result"]["bid"][:10]
        d["result"]["ask"] = d["result"]["ask"][:10]
        save("depth_USDTTMN.json", d)
        time.sleep(0.3)
        t = h.get("/v1/trades", params={"symbol": "USDTTMN"}).json()
        t["result"]["latestTrades"] = t["result"]["latestTrades"][:5]
        save("trades_USDTTMN.json", t)
        time.sleep(0.3)
        now = int(time.time()) // 60 * 60
        save("udf_USDTTMN_1m.json",
             h.get("/v1/udf/history", params={"symbol": "USDTTMN", "resolution": "1",
                                              "from": now - 30 * 60, "to": now}).json())
        time.sleep(0.3)
        save("udf_no_data.json",
             h.get("/v1/udf/history", params={"symbol": "USDTTMN", "resolution": "1",
                                              "from": now - 2 * 86400, "to": now}).json())


if __name__ == "__main__":
    main()
