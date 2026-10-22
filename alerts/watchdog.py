"""Heartbeat watchdog — run every minute by a systemd timer, independent of the engine.

    uv run python -m alerts.watchdog --run-dir /opt/wallex-agent/run/shadow

If the engine's heartbeat is older than 2 minutes, send one Bale alert (and one
"recovered" alert when it comes back). State lives in run/<dir>/watchdog.json.

Also watches the laptop manager: if the engine's playbook expired more than 5 minutes ago
(the AI box normally renews it 20 minutes before expiry), the laptop is down or offline and
the engine is holding with no new buys → alert, remind every hour, and say when a plan arrives.
State lives in run/<dir>/watchdog-manager.json.
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


MANAGER_LATE_S = 5 * 60
MANAGER_REMIND_S = 3600


def check_manager(run_dir: Path, alerter: Alerter, now: float | None = None) -> str:
    now = now or time.time()
    try:
        valid_until = float(json.loads((run_dir / "playbook.json").read_text(encoding="utf-8"))["valid_until"])
    except (OSError, ValueError, KeyError, TypeError):
        return "no_playbook"                  # baseline / fresh run dir: nothing to watch
    state_path = run_dir / "watchdog-manager.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"late": False, "alerted_at": 0.0}
    late = now - valid_until > MANAGER_LATE_S
    result, sent = "ok", True
    if late and (not state["late"] or now - state.get("alerted_at", 0.0) >= MANAGER_REMIND_S):
        mins = (now - valid_until) / 60
        sent = alerter.send(f"🧠 مدیر هوشمند (لپ‌تاپ) {mins:.0f} دقیقه است برنامه‌ی تازه نفرستاده؛ "
                            f"ربات فقط نگه می‌دارد و خرید تازه نمی‌کند. لپ‌تاپ روشن و وصل باشد و "
                            f"وظیفه‌ی WallexAiBox در حال اجرا باشد.", force=True)
        result = "alerted_late" if sent else "alert_failed"
        if sent:
            state["alerted_at"] = now
    elif not late and state["late"]:
        sent = alerter.send("✅ مدیر هوشمند دوباره برنامه فرستاد؛ ربات به حالت عادی برگشت.", force=True)
        result = "alerted_back" if sent else "alert_failed"
    if sent or not alerter.enabled:
        state["late"] = late
        state_path.write_text(json.dumps({**state, "checked_at": now}), encoding="utf-8")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", type=Path, required=True)
    a = ap.parse_args(argv)
    alerter = Alerter.from_env(sync=True)
    print(check(a.run_dir, alerter), check_manager(a.run_dir, alerter))


if __name__ == "__main__":
    main()
