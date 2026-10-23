"""The engine with a human seatbelt: `Engine` from engine/loop.py plus the oversight state file.

Built as a subclass so the code that traded (tags live-v1, live-v2) stays byte-identical; it only
runs when started through `python -m oversight.run`. Each tick it re-reads <run_dir>/oversight/state.json
(written by the Bale bot) and:

  paused      → adds not allowed: the core sleeve, the activity floor and the gate refuse every buy
  risk_cap    → Claude's risk multiplier is capped (min), so targets can only shrink
  flatten_at  → the end-of-contest sell-off starts now: end_at moves to flatten_at + 1 h, so the normal
                schedule runs its 'market' phase (protected limits, ≤ 2 % through the book), 'verify', then stops

Working buys are cancelled whenever a human lowers risk. Nothing here creates an order of its own:
every sell still comes from engine/flatten.py or engine/core.py and passes the same risk gate.
A file that can't be read keeps the last good state (and is logged); a missing file means no limits.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import config
from engine.loop import Engine
from engine.types import Proposal
from oversight import state as ov

FLATTEN_LEAD_S = config.FLATTEN.market_h * 3600      # 'market' phase from the first tick


class OverseenEngine(Engine):
    def __init__(self, cfg, **kw):
        self.real_end_at: datetime = cfg.end_at
        self.oversight = ov.OversightState()
        self._ov_path = ov.state_path(cfg.run_dir)
        self._ov_mtime: int | None = None
        super().__init__(cfg, **kw)
        self._read_oversight(self.clock())

    # ---- reading the file ---------------------------------------------------------------------
    def _read_oversight(self, now: float) -> None:
        try:
            mtime = self._ov_path.stat().st_mtime_ns if self._ov_path.exists() else None
            if mtime == self._ov_mtime:
                return
            st = ov.load(self._ov_path)
            self._ov_mtime = mtime
        except (OSError, ValueError, KeyError, TypeError) as e:
            self._throttled(now, "oversight_state_invalid", error=f"{type(e).__name__}: {e}"[:300])
            return                                     # keep the last good state
        old, self.oversight = self.oversight, st
        flatten_end = None
        if st.flatten_at is not None:
            flatten_end = datetime.fromtimestamp(st.flatten_at + FLATTEN_LEAD_S, tz=config.TEHRAN)
        self.cfg.end_at = min(self.real_end_at, flatten_end) if flatten_end else self.real_end_at
        if st.seq != old.seq:
            self._event(now, "oversight_applied", seq=st.seq, paused=st.paused, risk_cap=st.risk_cap,
                        flatten_at=st.flatten_at, end_at=self.cfg.end_at.isoformat(), by=st.last)
            lowered = (st.paused and not old.paused) or st.risk_cap < old.risk_cap or (
                st.flatten_at is not None and old.flatten_at is None)
            if lowered and any(w.side == "BUY" for w in self.ex.working.values()):
                n = self.ex.cancel_buys(now, "human_oversight")
                self._event(now, "buys_cancelled", count=n, reason="human_oversight", seq=st.seq)

    @property
    def human_flatten(self) -> bool:
        return self.oversight.flatten_at is not None

    # ---- hooks into the engine --------------------------------------------------------------
    def tick(self) -> None:
        self._read_oversight(self.clock())
        super().tick()

    def _effective(self, level: int) -> tuple[float, bool]:
        mult, adds = super()._effective(level)
        return min(mult, self.oversight.risk_cap), adds and not self.oversight.paused

    def _log_decision(self, p: Proposal, v, cid, now: float) -> None:
        if self.human_flatten and p.kind == "flatten":
            p = replace(p, decided_by=f"flatten (human #{self.oversight.seq})")   # log only; routing is done
        super()._log_decision(p, v, cid, now)

    def _alerts(self, now: float, gmode: str) -> None:
        shown, self.cfg.end_at = self.cfg.end_at, self.real_end_at     # 'T−2 h' means the real end
        try:
            super()._alerts(now, gmode)
        finally:
            self.cfg.end_at = shown

    def _reconcile(self, now: float, final: bool = False) -> None:
        super()._reconcile(now, final=final and not self.human_flatten)   # not the contest's final check
