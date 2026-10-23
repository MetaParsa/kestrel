"""Human oversight: what a person may do, as pure functions over one small state file.

Wallex's rules: every evaluated trade must be placed by the algorithm; manual trades can disqualify.
So a human here can only make the engine MORE careful, never place, approve or size a trade:

  /pause        no new buys (sells, stops and the sell-off keep working)
  /risk 0.5     cap Claude's risk multiplier at 0.5 (0 ≤ x ≤ 1; 1 removes the cap; it can't go above Claude's)
  /flatten      start the normal end-of-contest sell-off now (same code path, protected limit orders)
  /resume       hand control back to Claude + the risk gate (clears pause, cap and flatten)
  /status       read-only summary          /review <note>   the daily human review note (logged only)

The bot (`oversight/bot.py`) is the only writer of the state file; the engine
(`oversight/engine.py`) only reads it. Everything a human does is logged with who / when / why.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789.")
COMMANDS = ("pause", "resume", "risk", "flatten", "status", "review", "help", "confirm")
NEEDS_CONFIRM = ("flatten", "resume")       # the two big switches take a second message
LOWERS_RISK = ("pause", "risk", "flatten")


@dataclass(frozen=True)
class Command:
    name: str
    value: float | None = None
    reason: str = ""
    code: str = ""                           # /confirm <code>


@dataclass(frozen=True)
class OversightState:
    paused: bool = False
    risk_cap: float = 1.0                    # effective multiplier = min(Claude's, risk_cap)
    flatten_at: float | None = None          # unix time a human started the sell-off
    seq: int = 0                             # bumps on every change; the engine logs each new seq once
    updated_at: float = 0.0
    last: dict = field(default_factory=dict)  # who / when / why of the latest change

    @property
    def ai_in_control(self) -> bool:
        return not self.paused and self.risk_cap >= 1.0 and self.flatten_at is None

    def summary_fa(self) -> str:
        if self.ai_in_control:
            return "کنترل کامل با Claude و دروازه‌ی ریسک (بدون محدودیت انسانی)"
        parts = []
        if self.flatten_at is not None:
            parts.append("فروش همه‌چیز به تومان (دستور انسانی)")
        if self.paused:
            parts.append("خرید جدید متوقف")
        if self.risk_cap < 1.0:
            parts.append(f"سقف ضریب ریسک {self.risk_cap:g}")
        return " · ".join(parts)


class CommandError(ValueError):
    """A message that is not a valid command; the text is shown to the human (Persian)."""


def parse(text: str) -> Command | None:
    """'/risk ۰٫۵ spread is wide' → Command('risk', 0.5, 'spread is wide'). None if not a command."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    parts = text.split(None, 1)                        # any whitespace, including a newline
    head, rest = parts[0], (parts[1].strip() if len(parts) > 1 else "")
    name = head[1:].split("@", 1)[0].lower()          # '/pause@MyBot' in groups
    if name not in COMMANDS:
        raise CommandError(f"دستور ناشناخته: /{name} — /help را بفرستید.")
    if name == "risk":
        arg, _, reason = " ".join(rest.split()).partition(" ")
        try:
            value = float(arg.translate(FA_DIGITS))
        except ValueError:
            raise CommandError("مقدار ریسک را بنویسید، مثلاً: /risk 0.5 دلیل")
        if not 0.0 <= value <= 1.0:                      # also false for NaN
            raise CommandError("ضریب ریسک باید بین ۰ و ۱ باشد؛ انسان فقط می‌تواند ریسک را کم کند.")
        return Command("risk", value, reason.strip())
    if name == "confirm":
        code = (rest.split(None, 1) or [""])[0].translate(FA_DIGITS)
        if not re.fullmatch(r"[0-9]{4}", code):
            raise CommandError("کد تأیید چهار رقمی است: /confirm 1234")
        return Command("confirm", code=code)
    return Command(name, reason=rest)


def apply(state: OversightState, cmd: Command, *, now: float, who: dict) -> OversightState:
    """The state after a (confirmed) command. Read-only commands return the state unchanged."""
    last = {"command": cmd.name, "value": cmd.value, "reason": cmd.reason or "(بدون دلیل)", "who": who, "at": now}
    if cmd.name == "pause":
        new = replace(state, paused=True)
    elif cmd.name == "risk":
        new = replace(state, risk_cap=float(cmd.value))
    elif cmd.name == "flatten":
        new = replace(state, flatten_at=state.flatten_at or now)
    elif cmd.name == "resume":
        new = replace(state, paused=False, risk_cap=1.0, flatten_at=None)
    else:
        return state
    return replace(new, seq=state.seq + 1, updated_at=now, last=last)


# ---- the file -------------------------------------------------------------------------------
def state_path(run_dir: Path) -> Path:
    return run_dir / "oversight" / "state.json"


def load(path: Path) -> OversightState:
    """Missing file = no human limits. A malformed file raises (the engine keeps its last good state)."""
    if not path.exists():
        return OversightState()
    d = json.loads(path.read_text(encoding="utf-8"))
    st = OversightState(paused=bool(d["paused"]), risk_cap=float(d["risk_cap"]),
                        flatten_at=None if d.get("flatten_at") is None else float(d["flatten_at"]),
                        seq=int(d["seq"]), updated_at=float(d.get("updated_at", 0.0)), last=dict(d.get("last") or {}))
    if not 0.0 <= st.risk_cap <= 1.0:                  # also false for NaN
        raise ValueError(f"risk_cap {st.risk_cap} outside 0..1")
    if st.flatten_at is not None and not (math.isfinite(st.flatten_at) and 0 <= st.flatten_at < 1e10):
        raise ValueError(f"flatten_at {st.flatten_at} is not a plausible unix time")
    return st


def load_or_backup(path: Path) -> OversightState:
    """The state file, or the copy saved before the last change if the file itself is unreadable."""
    try:
        return load(path)
    except (OSError, ValueError, KeyError, TypeError):
        return load(path.with_suffix(".bak"))      # raises too if both are broken


def save(path: Path, st: OversightState) -> None:
    """Atomic and durable: write + fsync a temp file, keep the previous state as .bak, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(asdict(st), ensure_ascii=False, indent=1))
        f.flush()
        os.fsync(f.fileno())
    if path.exists():
        try:
            load(path)                                  # only a readable state becomes the backup
            os.replace(path, path.with_suffix(".bak"))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    os.replace(tmp, path)


@contextmanager
def locked(path: Path, wait_s: float = 5.0, stale_s: float = 30.0):
    """One writer at a time (the bot and a terminal command): a lock file created exclusively."""
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + wait_s
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_s:
                    lock.unlink()                       # left behind by a killed process
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise TimeoutError("oversight state is locked by another writer")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def log(run_dir: Path, record: dict, sha: str = "oversight") -> None:
    """Append to <run_dir>/logs/oversight.jsonl, the human part of the decision log."""
    p = run_dir / "logs" / "oversight.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"git_sha": sha, "mode": "oversight", **record}, ensure_ascii=False, default=str) + "\n")
