"""Server side: accept playbooks the laptop pushed into run/inbox/.

A playbook is accepted only if it parses, answers a bundle we issued less than 15
minutes before it arrives (the bundle's hash is consumed: one playbook per bundle),
passes `server_validate` (every bound re-applied here — the laptop's clamps are not
trusted), and is newer than the active one. Validity is re-based on the server clock,
so laptop clock skew is logged, not fatal. Every file is moved out of the inbox once.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from manager.schema import Playbook, server_validate

MAX_BUNDLE_AGE_S = 15 * 60


class Inbox:
    def __init__(self, inbox_dir: Path, keep: int = 200):
        self.dir = inbox_dir
        self.issued: dict[str, float] = {}          # bundle hash → bundle created_at (server clock)
        self.keep = keep
        self.active: Playbook | None = None
        self.active_accepted_at = 0.0

    def note_issued(self, bundle_hash: str, created_at: float) -> None:
        self.issued.setdefault(bundle_hash, created_at)
        while len(self.issued) > self.keep:
            self.issued.pop(next(iter(self.issued)))

    def poll(self, now: float) -> tuple[Playbook | None, list[dict]]:
        """Returns (newly accepted playbook or None, events)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        events, accepted = [], None
        for f in sorted(self.dir.glob("playbook-*.json"), key=lambda p: p.name):
            reason, pb, extra = self._check(f, now)
            if pb is not None:
                self.active, self.active_accepted_at, accepted = pb, now, pb
                events.append({"ts": now, "event": "playbook_accepted", "playbook_id": pb.playbook_id,
                               "source": pb.source, "regime": pb.regime, "clamps": pb.clamps, **extra})
                self._move(f, "processed")
            else:
                events.append({"ts": now, "event": "playbook_rejected", "file": f.name, "reason": reason})
                self._move(f, "rejected")
        return accepted, events

    def _check(self, f: Path, now: float) -> tuple[str, Playbook | None, dict]:
        try:
            pb = Playbook.model_validate(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, ValidationError) as e:
            return f"invalid: {type(e).__name__}", None, {}
        issued_at = self.issued.get(pb.bundle_hash)
        if issued_at is None:
            return "unknown_or_used_bundle_hash", None, {}
        if now - issued_at > MAX_BUNDLE_AGE_S:
            return "bundle_too_old", None, {}
        if self.active is not None and pb.created_at <= self.active.created_at:
            return "older_than_active", None, {}
        try:
            pb, _ = server_validate(pb, accepted_at=now)
        except ValueError as e:
            return f"invalid: {e}", None, {}
        del self.issued[pb.bundle_hash]               # one playbook per bundle
        return "", pb, {"clock_skew_s": round(pb.created_at - now, 1)}

    def _move(self, f: Path, sub: str) -> None:
        d = self.dir / sub
        d.mkdir(exist_ok=True)
        target = d / f.name
        n = 1
        while target.exists():                       # never fail on a name clash (Windows rename rules)
            target = d / f"{f.stem}.{n}{f.suffix}"
            n += 1
        os.replace(f, target)
