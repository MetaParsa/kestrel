"""One command for judges: re-run the evidence from committed data, offline, with no API key.

    uv run python -m judge.demo              # ~10 min: self-grading, crash drill, control group, 1 backtest window
    uv run python -m judge.demo --windows 25 # + every 3-day backtest window (runs in parallel, one per CPU core)
    uv run python -m judge.demo --quick      # ~3 min: skip the backtest

What it does
  1. checks the committed market data (`data/sample/`, real Wallex 1-minute candles, 2026-09-22 → 10-23)
     against its SHA-256 manifest;
  2. runs the four report scripts (`reports/`) on that data and on the committed live logs
     (`delivery/server-logs/`), each in its own process, inside a scratch folder (`judge_out/`), so nothing
     committed is overwritten;
  3. prints and saves (`judge_out/RESULTS.md`) the committed number next to the reproduced one.

Nothing here touches the network, Wallex or Claude. The reports drive the same `engine/` code that traded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "data" / "sample"
LOGS = REPO / "delivery" / "server-logs" / "main" / "logs"
COMMITTED = REPO / "reports" / "out"
MARKETS = ("USDTTMN", "BTCTMN", "BTCUSDT")


# ---- step 0: the data ---------------------------------------------------------------------------
def check_data() -> list[str]:
    """Problems with data/sample (empty list = all files present and unchanged)."""
    manifest = json.loads((SAMPLE / "MANIFEST.json").read_text(encoding="utf-8"))
    problems = []
    for name, info in manifest["files"].items():
        p = SAMPLE / name
        if not p.exists():
            problems.append(f"{name} is missing")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != info["sha256"]:
            problems.append(f"{name} differs from the manifest (git LFS or a line-ending filter?)")
    return problems


def use_sample_data() -> None:
    """Point data.fetch at the committed sample. The default store (data/store, git-ignored) is what the
    live engine reads; changing the defaults here keeps the code that traded byte-identical."""
    from data import fetch
    fetch.STORE = SAMPLE
    fetch.path_for.__defaults__ = (SAMPLE,)
    fetch.load.__defaults__ = (SAMPLE,)


# ---- one step = one process, run inside the scratch folder -------------------------------------
def run_step(name: str, work: Path, arg: str = "") -> None:
    """Entry point of a child process: `python -m judge.demo --step NAME --work DIR [--arg X]`."""
    use_sample_data()
    work.mkdir(parents=True, exist_ok=True)
    os.chdir(work)                                   # reports write reports/out, delivery/, run/ under cwd
    if name == "self_grading":
        from reports import self_grading
        Path("delivery").mkdir(exist_ok=True)
        self_grading.main(["--logs", str(LOGS)])
    elif name == "crash_drill":
        from reports import crash_drill
        Path("delivery").mkdir(exist_ok=True)
        crash_drill.main()
    elif name == "ab_replay":
        from reports import ab_replay
        ab_replay.main(["--logs", str(LOGS), "--end", arg])
    elif name == "backtest":
        from reports import backtest
        k, n = arg.split("/")                        # this process runs window k; n windows in total
        backtest.main(["--limit", n, "--shard", f"{k}/{n}", "--out", "run/backtest"])
    else:
        raise SystemExit(f"unknown step {name}")


def spawn(name: str, work: Path, arg: str = "") -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WALLEX_", "BALE_", "ANTHROPIC_"))}
    env.update(PYTHONPATH=str(REPO), PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONHASHSEED="0")
    log = open(work / f"{name}{'-' + arg.replace('/', 'of') if name == 'backtest' else ''}.log", "w", encoding="utf-8")
    cmd = [sys.executable, "-m", "judge.demo", "--step", name, "--work", str(work)] + (["--arg", arg] if arg else [])
    proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    proc.started = time.time()
    return proc


# ---- comparing -----------------------------------------------------------------------------------
def load(p: Path) -> dict | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def row(label: str, committed, reproduced, tol: float = 1e-9) -> dict:
    same = reproduced is not None and committed == reproduced or (isinstance(committed, (int, float)) and isinstance(reproduced, (int, float))
                                       and abs(committed - reproduced) <= tol)
    return {"check": label, "committed": committed, "reproduced": reproduced, "same": same}


def compare(work: Path, steps: list[str], windows: int) -> list[dict]:
    out = []
    if "self_grading" in steps:
        c, r = load(COMMITTED / "self-grading.json"), load(work / "self_grading" / "reports/out/self-grading.json")
        cs, rs = (c or {}).get("summary", {}), (r or {}).get("summary", {})
        for k, label in (("forecasts_resolved", "Self-grading: Claude forecasts scored"),
                         ("direction_hit", "Self-grading: Claude direction hit rate"),
                         ("naive_hit", "Self-grading: naive baseline hit rate"),
                         ("always_flat_hit", "Self-grading: 'always flat' hit rate"),
                         ("playbooks_citing_own_scorecard", "Self-grading: playbooks citing own scorecard")):
            out.append(row(label, cs.get(k), rs.get(k)))
    if "crash_drill" in steps:
        c, r = load(COMMITTED / "crash-drill.json"), load(work / "crash_drill" / "reports/out/crash-drill.json")
        for kind, k, label in (("crash", "with_seatbelt_return_pct", "Seatbelt: −10% crash, with seatbelt (%)"),
                               ("crash", "held_through_return_pct", "Seatbelt: −10% crash, held through (%)"),
                               ("glitch", "with_seatbelt_return_pct", "Seatbelt: −30% glitch print, result (%)"),
                               ("glitch", "sells", "Seatbelt: panic sells on the glitch")):
            cv, rv = ((c or {}).get(kind) or {}).get(k), ((r or {}).get(kind) or {}).get(k)
            if k == "sells":
                cv, rv = (len(cv) if cv is not None else None), (len(rv) if rv is not None else None)
            out.append(row(label, cv, rv))
    if "ab_replay" in steps:
        c, r = load(COMMITTED / "ab-replay-BTCTMN.json"), load(work / "ab_replay" / "reports/out/ab-replay-BTCTMN.json")
        for who, k, label in (("claude", "after_selling_to_tmn_pct", "Control group: Claude, after selling to TMN (%)"),
                              ("rules_twin", "after_selling_to_tmn_pct", "Control group: rule twin, after selling to TMN (%)"),
                              ("claude", "max_drawdown_pct", "Control group: Claude max drawdown (%)"),
                              ("rules_twin", "max_drawdown_pct", "Control group: rule twin max drawdown (%)"),
                              ("claude", "fees_tmn", "Control group: Claude fees (TMN)"),
                              ("rules_twin", "fees_tmn", "Control group: rule twin fees (TMN)")):
            out.append(row(label, ((c or {}).get(who) or {}).get(k), ((r or {}).get(who) or {}).get(k)))
    if "backtest" in steps:
        c = load(COMMITTED / "backtest-summary.json") or {"results": []}
        by_start = {x["start"]: x for x in c["results"]}
        got = []
        for f in sorted((work / "backtest" / "run" / "backtest").glob("shard-*.jsonl")):
            got += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        for g in sorted(got, key=lambda x: x["start"]):
            ref = by_start.get(g["start"], {})
            for k, label in (("return_pct", "return (%)"), ("max_drawdown_pct", "max drawdown (%)"),
                             ("fills", "fills"), ("decisions", "decisions logged"), ("ended_in_tmn", "ended fully in TMN")):
                out.append(row(f"Backtest window {g['start'][:10]}: {label}", ref.get(k), g.get(k)))
        if len(got) < windows:
            out.append(row("Backtest: windows finished", windows, len(got)))
    return out


def ab_end() -> str:
    """The control-group replay ends where the committed one ended, so both see the same minutes."""
    c = load(COMMITTED / "ab-replay-BTCTMN.json")
    return str(datetime.fromisoformat(c["to"]).timestamp())


def write_results(work: Path, rows: list[dict], seconds: float, skipped: list[str] = ()) -> str:
    fmt = lambda v: "—" if v is None else str(v)
    lines = ["# Judge demo — committed vs reproduced", "",
             f"Run {datetime.now().astimezone().isoformat(timespec='minutes')} · {seconds / 60:.1f} min · "
             f"data: `data/sample/` · logs: `delivery/server-logs/main/logs/` · no network, no API key", "",
             "| check | committed | reproduced | |", "|---|---|---|---|"]
    lines += [f"| {r['check']} | {fmt(r['committed'])} | {fmt(r['reproduced'])} | {'✅' if r['same'] else '❌'} |"
              for r in rows]
    same = sum(r["same"] for r in rows)
    lines += ["", f"**{same}/{len(rows)} numbers reproduced exactly.**"
                  + (f" Not run this time: {', '.join(skipped)}." if skipped else "")
                  + " Full outputs: `judge_out/<step>/` (the reports, the Persian pages, and the engine's own "
                  "decision/order/fill logs for each run)."]
    text = "\n".join(lines) + "\n"
    (work / "RESULTS.md").write_text(text, encoding="utf-8")
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--windows", type=int, default=1, help="3-day backtest windows to re-run (25 = all; ~8 min each)")
    ap.add_argument("--quick", action="store_true", help="skip the backtest")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2, help="processes at once")
    ap.add_argument("--work", type=Path, default=REPO / "judge_out")
    ap.add_argument("--step", help=argparse.SUPPRESS)
    ap.add_argument("--arg", default="", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):              # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.step:
        run_step(a.step, a.work / a.step, a.arg)
        return 0

    problems = check_data()
    if problems:
        print("data/sample is not as committed:\n  " + "\n  ".join(problems))
        return 2
    print(f"data/sample OK (SHA-256 manifest) · writing to {a.work}")
    marker = a.work / ".judge_demo"
    if a.work.exists() and any(a.work.iterdir()) and not marker.exists():
        print(f"refusing to empty {a.work}: it is not a folder this demo created (pick another --work)")
        return 2
    shutil.rmtree(a.work, ignore_errors=True)
    a.work.mkdir(parents=True)
    marker.write_text("scratch folder of judge/demo.py; safe to delete", encoding="utf-8")
    jobs = [("self_grading", ""), ("crash_drill", ""), ("ab_replay", ab_end())]
    windows = 0 if a.quick else max(0, min(a.windows, 25))
    jobs = [("backtest", f"{k}/{windows}") for k in range(windows)] + jobs     # slowest first
    t0, running, done = time.time(), [], []
    try:
        _run_all(a, jobs, running, done, t0)
    except KeyboardInterrupt:
        for _, _, proc in running:                   # don't leave orphaned engines running
            proc.kill()
        raise
    steps = sorted({n for n, _, _ in done})
    rows = compare(a.work, steps, windows)
    skipped = [] if windows else ["backtest (--quick)"]
    print()
    print(write_results(a.work, rows, time.time() - t0, skipped))
    failed = [n for n, _, p in done if p.returncode != 0]
    return 1 if failed or not all(r["same"] for r in rows) else 0     # non-zero on any difference


STEP_TIMEOUT_S = 45 * 60


def _run_all(a, jobs: list, running: list, done: list, t0: float) -> None:
    while jobs or running:
        while jobs and len(running) < max(1, a.jobs):
            name, arg = jobs.pop(0)
            (a.work / name).mkdir(exist_ok=True)
            running.append((name, arg, spawn(name, a.work, arg)))
            print(f"  started {name} {arg if name == 'backtest' else ''}".rstrip(), flush=True)
        for item in list(running):
            if item[2].poll() is None and time.time() - item[2].started > STEP_TIMEOUT_S:
                item[2].kill()                       # a hung step fails visibly instead of blocking forever
                item[2].wait()
            if item[2].poll() is not None:
                running.remove(item)
                done.append(item)
                status = "done" if item[2].returncode == 0 else f"FAILED (exit {item[2].returncode}, see {a.work}/*.log)"
                print(f"  {status}: {item[0]} {item[1] if item[0] == 'backtest' else ''}  "
                      f"[{(time.time() - t0) / 60:.1f} min]", flush=True)
        time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(main())
