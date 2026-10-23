"""Run the whole agent on one machine, on live Wallex prices with simulated money (shadow mode).

    uv run python -m judge.try_live                  # free rule-based manager, 10 minutes
    uv run python -m judge.try_live --claude         # real Claude: needs ANTHROPIC_API_KEY and `uv sync --extra manager`
    uv run python -m judge.try_live --minutes 30

Starts the same two processes that ran in the contest, here side by side:
  1. the engine (`engine.run --mode shadow`): polls Wallex's public order book every 5 s, ticks every 10 s,
     fills orders only when real trades cross them; no API key, no real orders (shadow mode cannot place any);
  2. the AI box (`ai_box.loop --local-run-dir`): reads the engine's bundle and writes back a playbook,
     from Claude with --claude (about $0.05–0.11 per plan, $3 cap here) or from the free rule-based manager.
The contest ended on 2026-10-23, so the run gets its own end time 3 days ahead (otherwise the engine would
only sell). Every minute it prints the engine's status; at the end it writes the Persian decision log.
Needs internet access to api.wallex.ir (and to Anthropic with --claude). Output: judge_out/live/.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import config

REPO = Path(__file__).resolve().parent.parent
RUN = REPO / "judge_out" / "live"


def status_line(run: Path) -> str:
    try:
        s = json.loads((run / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "waiting for the first tick…"
    pb = s.get("playbook") or {}
    btc = ((s.get("exposure") or {}).get("BTCTMN") or {}).get("core", 0) or 0
    last = (s.get("recent_decisions") or [{}])[0]
    return (f"level {s.get('level')} · phase {s.get('phase')} · equity {s.get('equity') or 0:,.0f} TMN · "
            f"BTC {btc:.1%} · plan {pb.get('source', '—')}/{pb.get('regime', '—')} · working orders {s.get('working_orders')}"
            + (f" · last decision {last.get('side')} {last.get('kind')} ({last.get('verdict')})" if last else ""))


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):              # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claude", action="store_true", help="real Claude instead of the free rule-based manager")
    ap.add_argument("--minutes", type=float, default=10.0)
    a = ap.parse_args(argv)
    if a.claude:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("--claude needs ANTHROPIC_API_KEY in the environment")
            return 2
        try:
            import anthropic  # noqa: F401
        except ImportError:
            print("--claude needs the Anthropic SDK: run `uv sync --extra manager` first")
            return 2
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WALLEX_", "BALE_"))}   # no key, no alerts
    end = datetime.now(config.TEHRAN) + timedelta(days=3)
    env.update(WALLEX_END_AT=end.isoformat(timespec="seconds"), PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    RUN.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    logs = RUN / "engine.out", RUN / "ai_box.out"
    eng = subprocess.Popen([py, "-m", "engine.run", "--mode", "shadow", "--run-dir", str(RUN)], cwd=REPO, env=env,
                           stdout=open(logs[0], "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    box_cmd = [py, "-m", "ai_box.loop", "--local-run-dir", str(RUN), "--interval", "30"]
    if a.claude:
        box_cmd += ["--paid", "--cap", "3", "--gap-min", "10"]
    print(f"engine and AI box ({'Claude' if a.claude else 'rule-based manager'}) running for {a.minutes:g} min · "
          f"contest end for this run: {end:%Y-%m-%d %H:%M} Tehran · output {RUN}")
    time.sleep(20)                                      # the engine's first tick writes the bundle
    box = subprocess.Popen(box_cmd, cwd=REPO, env=env, stdout=open(logs[1], "w", encoding="utf-8"),
                           stderr=subprocess.STDOUT)
    stop = time.time() + a.minutes * 60
    try:
        while time.time() < stop:
            for p, name, log in ((eng, "engine", logs[0]), (box, "AI box", logs[1])):
                if p.poll() is not None:
                    print(f"{name} stopped (exit {p.returncode}); last lines of {log}:")
                    print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]))
                    return 1
            print(f"[{datetime.now():%H:%M:%S}] {status_line(RUN)}", flush=True)
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (box, eng):
            p.terminate()
    print("fetching the last 3 days of public 1-minute candles for the report…", flush=True)
    subprocess.run([py, "-m", "data.fetch", "--days", "3"], cwd=REPO, env=env, capture_output=True)
    from reports import shadow_report
    os.chdir(RUN)                                       # the report writes reports/out and delivery/ under cwd
    with open(os.devnull, "w") as null:
        out, sys.stdout = sys.stdout, null
        try:
            shadow_report.main(["--logs", str(RUN / "logs"), "--name", "try"])
        finally:
            sys.stdout = out
    print(f"done. Decision log (Persian): {RUN / 'delivery' / 'DECISIONS-try.md'} · raw logs: {RUN / 'logs'} · "
          f"Claude's playbooks and transcripts: {REPO / 'run' / 'ai_box'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
