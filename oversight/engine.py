"""The engine with a human seatbelt: `Engine` from engine/loop.py plus the oversight state file.

Built as a subclass so the code that traded (the `live-*` tags) stays byte-identical; it only runs when
started through `python -m oversight.run`. Each tick it re-reads <run_dir>/oversight/state.json (written
by the Bale bot) and:

  paused      → adds not allowed: the core sleeve, the activity floor and the gate refuse every buy,
                and working buys are cancelled on every tick while paused (not just once)
  risk_cap    → Claude's risk multiplier is capped (min), so targets can only shrink; working buys from
                before the cap are cancelled until none is left
  flatten_at  → the end-of-contest sell-off starts now: end_at moves to flatten_at + 1 h, so the normal
                schedule runs its 'market' phase (protected limits, ≤ 2 % through the book). While anything
                sellable is still held near that deadline (failed sells, a restart after the hour), the
                deadline keeps moving forward, so the engine keeps selling instead of stopping half-way.

Nothing here creates an order of its own: every sell still comes from engine/flatten.py or engine/core.py
and passes the same risk gate. The contest's real end still gets its final check and alert.
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
KEEP_SELLING_S = config.FLATTEN.verify_min * 60       # extend the deadline before 'verify' while coins remain


class OverseenEngine(Engine):
    def __init__(self, cfg, **kw):
        self.real_end_at: datetime = cfg.end_at
        self.oversight = ov.OversightState()
        self._ov_path = ov.state_path(cfg.run_dir)
        self._ov_mtime: int | None = None
        self._flatten_end: float | None = None
        self._buys_must_go = False
        self._real_end_checked = False
        super().__init__(cfg, **kw)
        self._read_oversight(self.clock())

    # ---- reading the file ---------------------------------------------------------------------
    def _read_oversight(self, now: float) -> None:
        try:
            mtime = self._ov_path.stat().st_mtime_ns if self._ov_path.exists() else None
            if mtime == self._ov_mtime:
                return
            st = ov.load(self._ov_path)                # validates ranges, rejects NaN/inf
            flatten_end = None if st.flatten_at is None else st.flatten_at + FLATTEN_LEAD_S
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as e:
            self._throttled(now, "oversight_state_invalid", error=f"{type(e).__name__}: {e}"[:300])
            return                                     # keep the last good state
        old = self.oversight
        self.oversight, self._ov_mtime = st, mtime
        if flatten_end is None:
            self._flatten_end = None
        elif self._flatten_end is None or old.flatten_at != st.flatten_at:
            self._flatten_end = flatten_end
        if st.seq != old.seq:
            self._event(now, "oversight_applied", seq=st.seq, paused=st.paused, risk_cap=st.risk_cap,
                        flatten_at=st.flatten_at, by=st.last)
            if (st.paused and not old.paused) or st.risk_cap < old.risk_cap or (
                    st.flatten_at is not None and old.flatten_at is None):
                self._buys_must_go = True

    @property
    def human_flatten(self) -> bool:
        return self.oversight.flatten_at is not None

    def _non_tmn(self) -> float:
        """Sellable non-TMN value. Before the first price (just restarted) any holding counts: keep selling."""
        total = 0.0
        for (m, _), p in self.pf.positions.items():
            if p.qty <= 0:
                continue
            if m not in self.bids:
                return float("inf")
            if p.value(self.bids[m]) >= config.LIMITS.exchange_min_notional_tmn:
                total += p.value(self.bids[m])
        return total

    def _place_deadline(self, now: float) -> None:
        if self._flatten_end is not None and now >= self._flatten_end - KEEP_SELLING_S and self._non_tmn() > 0:
            self._flatten_end = now + FLATTEN_LEAD_S       # still holding coins: keep the 'market' phase going
        end = self.real_end_at
        if self._flatten_end is not None:
            end = min(end, datetime.fromtimestamp(self._flatten_end, tz=config.TEHRAN))
        self.cfg.end_at = end

    def _cancel_buys_if_needed(self, now: float) -> None:
        o = self.oversight
        if not (o.paused or self.human_flatten or self._buys_must_go):
            return
        if not any(w.side == "BUY" for w in self.ex.working.values()):
            self._buys_must_go = False
            return
        try:
            n = self.ex.cancel_buys(now, "human_oversight")
            self._event(now, "buys_cancelled", count=n, reason="human_oversight", seq=o.seq)
        except Exception as e:                        # retried next tick; never stops the tick
            self._throttled(now, "oversight_cancel_failed", error=f"{type(e).__name__}: {e}"[:300])

    # ---- hooks into the engine --------------------------------------------------------------
    def tick(self) -> None:
        now = self.clock()
        self._read_oversight(now)
        self._place_deadline(now)
        self._cancel_buys_if_needed(now)
        super().tick()
        if self.human_flatten and not self._real_end_checked and now >= self.real_end_at.timestamp() \
                and self.cfg.end_at < self.real_end_at:
            self._real_end_checked = True              # the human window ended earlier; the contest end
            try:                                       # still gets its final check and alert, once
                super()._reconcile(now, final=True)
            except Exception as e:
                self._event(now, "reconcile_failed", error=f"{type(e).__name__}: {e}"[:300], critical=True,
                            alert="⚠️ تطبیق پایانی حساب انجام نشد.")

    def _effective(self, level: int) -> tuple[float, bool]:
        mult, adds = super()._effective(level)
        return min(mult, self.oversight.risk_cap), adds and not self.oversight.paused

    def _still_valid(self, p: Proposal) -> bool:
        if p.increases_risk and (self.oversight.paused or self.human_flatten):
            return False                               # a working buy never crosses the spread while braked
        return super()._still_valid(p)

    def _log_decision(self, p: Proposal, v, cid, now: float) -> None:
        o, pb = self.oversight, self.playbook
        if self.human_flatten and p.kind == "flatten" and self.cfg.end_at < self.real_end_at:
            p = replace(p, decided_by=f"flatten (human #{o.seq})")          # log only; routing is done
        elif p.kind == "rebalance_down" and pb is not None and o.risk_cap < pb.risk_multiplier:
            p = replace(p, decided_by=f"{p.decided_by} (risk capped at {o.risk_cap:g} by human #{o.seq})")
        super()._log_decision(p, v, cid, now)

    def _alerts(self, now: float, gmode: str) -> None:
        shown, self.cfg.end_at = self.cfg.end_at, self.real_end_at     # 'T−2 h' means the real end
        try:
            super()._alerts(now, gmode)
        finally:
            self.cfg.end_at = shown

    def _reconcile(self, now: float, final: bool = False) -> None:
        # 'ended' of the human window is not the contest's final check (that one runs in tick())
        super()._reconcile(now, final=final and self.cfg.end_at >= self.real_end_at)
