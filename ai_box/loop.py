"""Laptop AI box: every ~60 s pull the server's bundle, decide, push a playbook back.

    # free, deterministic rule-based manager (default)
    uv run python -m ai_box.loop --host trader@SERVER --remote-dir /opt/wallex-agent/run
    # real Claude — costs money; only after the user has said yes in chat
    uv run python -m ai_box.loop --host trader@SERVER --remote-dir /opt/wallex-agent/run --paid

No tunnel: every pull/push is a fresh short `scp`, so a VPN drop just means a missed
cycle, which the server's degrade ladder already handles. The Anthropic key is read
from ANTHROPIC_API_KEY on this laptop only and never leaves it.

Cost safety:
- One decision per server request (`manager_request_id`), not per bundle: the server
  rebuilds the bundle every minute, the request id only changes when a new playbook is due.
- A failed push retries the push only — never a second paid call for the same request.
- Bundles older than 10 minutes are ignored; paid decisions are at least `min_gap_s` apart
  unless the server flags an event.
- At the spend cap the box falls back to the free rule-based manager (logged), so the
  engine keeps getting fresh playbooks.
- Any error is logged and the loop keeps running.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ai_box.spend import SpendCapReached, SpendLedger
from manager.manager import run_manager
from manager.mock import mock_playbook
from manager.schema import Playbook, canonical_json, content_hash, finalize

HERE = Path(__file__).resolve().parent.parent
LOCAL = HERE / "run" / "ai_box"
SPEND_CAP_USD = 15.0
MAX_BUNDLE_AGE_S = 600
MAX_ATTEMPTS_PER_REQUEST = 2


class Transport(Protocol):
    def pull_bundle(self, dest: Path) -> bool: ...
    def push_playbook(self, src: Path) -> bool: ...


@dataclass
class LocalTransport:
    """Server and AI box on one machine (tests, single-host runs)."""
    run_dir: Path

    def pull_bundle(self, dest: Path) -> bool:
        src = self.run_dir / "outbox" / "bundle.json"
        if not src.exists():
            return False
        shutil.copyfile(src, dest)
        return True

    def push_playbook(self, src: Path) -> bool:
        inbox = self.run_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        tmp = inbox / f".{src.name}.tmp"
        shutil.copyfile(src, tmp)
        tmp.replace(inbox / src.name)
        return True


@dataclass
class ScpTransport:
    host: str                 # user@server
    remote_run_dir: str       # e.g. /opt/wallex-agent/run
    timeout_s: int = 25

    @property
    def _opts(self) -> list[str]:
        # ControlMaster off: Windows OpenSSH can't share connections ("getsockname failed: Not a socket").
        return ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.timeout_s}",
                "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
                "-o", "ControlMaster=no", "-o", "ControlPath=none"]

    def _run(self, cmd: list[str], cwd: Path) -> bool:
        try:
            return subprocess.run(cmd, cwd=cwd, timeout=self.timeout_s + 15, capture_output=True).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    # Local paths are passed relative to `cwd`, so a Windows "C:\..." is never read as a host.
    def pull_bundle(self, dest: Path) -> bool:
        return self._run(["scp", "-q", *self._opts, f"{self.host}:{self.remote_run_dir}/outbox/bundle.json",
                          dest.name], cwd=dest.parent)

    def push_playbook(self, src: Path) -> bool:
        tmp_remote = f"{self.remote_run_dir}/inbox/.{src.name}.tmp"     # never half a file in the inbox
        if not self._run(["scp", "-q", *self._opts, src.name, f"{self.host}:{tmp_remote}"], cwd=src.parent):
            return False
        return self._run(["ssh", *self._opts, self.host,
                          f"mv -f '{tmp_remote}' '{self.remote_run_dir}/inbox/{src.name}'"], cwd=src.parent)


Decider = Callable[[dict], tuple[Playbook, list[dict]]]


def mock_decider(bundle: dict) -> tuple[Playbook, list[dict]]:
    pb = finalize(mock_playbook(bundle), bundle_hash=bundle["hash"], now=time.time(), source="mock", model="rules-v1")
    return pb, []


def claude_decider(client, ledger: SpendLedger) -> Decider:
    def decide(bundle: dict) -> tuple[Playbook, list[dict]]:
        r = run_manager(bundle, client, ledger)
        pb = finalize(r.out, bundle_hash=bundle["hash"], now=time.time(), source="claude", model=r.model,
                      cost_usd=r.cost_usd)
        return pb, r.transcript
    return decide


class AiBox:
    def __init__(self, transport: Transport, decide: Decider, local_dir: Path = LOCAL, *,
                 fallback: Decider | None = mock_decider, min_gap_s: float = 0.0,
                 clock: Callable[[], float] = time.time,
                 log: Callable[[dict], None] = lambda e: print(json.dumps(e, ensure_ascii=False))):
        self.t, self.decide, self.dir, self.fallback = transport, decide, local_dir, fallback
        self.min_gap_s, self.clock, self.log = min_gap_s, clock, log
        self.dir.mkdir(parents=True, exist_ok=True)
        self.done_requests: set[str] = set()
        self.attempts: dict[str, int] = {}
        self.unpushed: Path | None = None        # finished playbook whose push failed
        self.last_decision_at = -1e18

    def cycle(self) -> Playbook | None:
        try:
            return self._cycle()
        except Exception as e:                    # never let one bad cycle kill the box
            self.log({"event": "cycle_error", "error": f"{type(e).__name__}: {e}"[:300]})
            return None

    def _cycle(self) -> Playbook | None:
        if self.unpushed is not None:              # retry the push only — no new decision
            if self.t.push_playbook(self.unpushed):
                self.log({"event": "playbook_pushed_on_retry", "file": self.unpushed.name})
                self.unpushed = None
            return None
        bundle_path = self.dir / "bundle.json"
        if not self.t.pull_bundle(bundle_path):
            self.log({"event": "pull_failed"})
            return None
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        except ValueError:
            bundle = None
        if not isinstance(bundle, dict) or "hash" not in bundle:
            self.log({"event": "bundle_unreadable"})
            return None
        if content_hash({k: v for k, v in bundle.items() if k != "hash"}) != bundle["hash"]:
            self.log({"event": "bundle_hash_mismatch"})
            return None
        req = bundle.get("manager_request_id")
        if not bundle.get("manager_due") or not req or req in self.done_requests:
            return None
        now = self.clock()
        if now - bundle.get("created_at", 0) > MAX_BUNDLE_AGE_S:
            self.log({"event": "bundle_stale", "age_s": round(now - bundle.get("created_at", 0))})
            return None
        is_event = str(bundle.get("reason", "")).startswith("event")
        if not is_event and now - self.last_decision_at < self.min_gap_s:
            return None
        self.attempts[req] = self.attempts.get(req, 0) + 1
        if self.attempts[req] > MAX_ATTEMPTS_PER_REQUEST:
            self.done_requests.add(req)
            self.log({"event": "request_given_up", "request_id": req})
            return None
        self.last_decision_at = now
        try:
            pb, transcript = self.decide(bundle)
        except SpendCapReached as e:
            if self.fallback is None:
                self.log({"event": "spend_cap_reached", "error": str(e)})
                return None
            self.log({"event": "spend_cap_fallback_rules", "error": str(e)})
            pb, transcript = self.fallback(bundle)
        except Exception as e:
            self.log({"event": "manager_failed", "error": f"{type(e).__name__}: {e}"[:300], "request_id": req})
            return None
        self.done_requests.add(req)                # decided: never pay twice for this request
        out = self.dir / f"playbook-{pb.playbook_id}.json"
        out.write_text(canonical_json(pb.model_dump()), encoding="utf-8")
        if transcript:
            (self.dir / f"transcript-{pb.playbook_id}.json").write_text(
                json.dumps(transcript, ensure_ascii=False, default=str), encoding="utf-8")
        if not self.t.push_playbook(out):
            self.unpushed = out
            self.log({"event": "push_failed", "playbook_id": pb.playbook_id})
            return pb
        self.log({"event": "playbook_pushed", "playbook_id": pb.playbook_id, "source": pb.source,
                  "regime": pb.regime, "cost_usd": round(pb.cost_usd, 4), "clamps": pb.clamps})
        return pb


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="user@server for scp; omit with --local-run-dir")
    ap.add_argument("--remote-dir", default="/opt/wallex-agent/run")
    ap.add_argument("--local-run-dir", type=Path, help="server run dir on this machine (single-host mode)")
    ap.add_argument("--paid", action="store_true", help="use real Claude (costs money)")
    ap.add_argument("--cap", type=float, default=SPEND_CAP_USD, help="hard spend cap in USD")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=60.0)
    a = ap.parse_args(argv)
    transport = LocalTransport(a.local_run_dir) if a.local_run_dir else ScpTransport(a.host, a.remote_dir)
    if a.paid:
        import anthropic   # only imported when real calls are allowed
        client = anthropic.Anthropic(max_retries=0, timeout=240.0)   # no silent, unrecorded retries
        decide, gap = claude_decider(client, SpendLedger(LOCAL / "spend.jsonl", a.cap)), 20 * 60
    else:
        decide, gap = mock_decider, 0
    box = AiBox(transport, decide, min_gap_s=gap)
    while True:
        box.cycle()
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
