"""Heartbeat watchdog — run every minute by a systemd timer, independent of the engine.

    uv run python -m alerts.watchdog --run-dir /opt/wallex-agent/run/shadow

If the engine's heartbeat is older than 2 minutes, send one Bale alert (and one
"recovered" alert when it comes back). State lives in run/<dir>/watchdog.json.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import config
from alerts.bale import Alerter


def check(run_dir: Path, alerter: Alerter, now: float | None = None,
          stale_s: float = config.LOOP.heartbeat_stale_s) -> str:
    now = now or time.time()
    state_path = run_dir / "watchdog.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"down": False}
    try:
        beat = float((run_dir / "heartbeat").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        beat = None
    down = beat is None or now - beat > stale_s
    result, sent = "ok", True
    if down and not state["down"]:
        age = "نامعلوم" if beat is None else f"{(now - beat) / 60:.0f} دقیقه"
        sent = alerter.send(f"🚨 قلب ربات ({run_dir.name}) نمی‌تپد — آخرین ضربان {age} پیش. "
                            f"سرویس را بررسی کنید: sudo systemctl status wallex-agent", force=True)
        result = "alerted_down" if sent else "alert_failed"
    elif not down and state["down"]:
        sent = alerter.send(f"✅ ربات ({run_dir.name}) دوباره کار می‌کند.", force=True)
        result = "alerted_up" if sent else "alert_failed"
    if sent or not alerter.enabled:           # a failed send keeps the old state, so the next run retries
        state_path.write_text(json.dumps({"down": down, "checked_at": now}), encoding="utf-8")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", type=Path, required=True)
    a = ap.parse_args(argv)
    print(check(a.run_dir, Alerter.from_env()))


if __name__ == "__main__":
    main()
