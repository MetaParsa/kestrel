"""Append-only JSONL logs — the decision log is a contest deliverable (spec v2 §5).

Every line carries the git SHA and the mode, so a judge can tell exactly which code
produced it. Files: decisions, orders, fills, playbooks, events (one per kind).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def git_sha(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=repo, capture_output=True,
                             text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo,
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "") if out.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


class Logs:
    KINDS = ("decisions", "orders", "fills", "playbooks", "events")

    def __init__(self, log_dir: Path, *, sha: str, mode: str):
        self.dir, self.sha, self.mode = log_dir, sha, mode
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, record: dict) -> None:
        assert kind in self.KINDS, kind
        line = json.dumps({"git_sha": self.sha, "mode": self.mode, **record}, ensure_ascii=False, default=str)
        with (self.dir / f"{kind}.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def tail(self, kind: str, n: int) -> list[dict]:
        p = self.dir / f"{kind}.jsonl"
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
