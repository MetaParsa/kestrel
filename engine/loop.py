"""The engine: one `tick()` every 10 s wires everything together (spec v2 §5, §7.5, §8, §9).

tick:
  market snapshots → feed the broker → day roll / marks
  → accept playbooks (inbox, or the local rule-based manager in the shadow baseline)
  → degrade level (0 ok · 2 playbook expired ≤ 2 h: hold, no adds · 3 no new entries)
  → cancel working buys if risk must not grow (level 3, flatten window, hard flatten)
  → proposals: hard flatten | exits + flatten schedule + core sleeve (+ activity floor)
  → each proposal through the risk gate → executor → decision log (one owner per trade)
  → executor tick (fills → portfolio, state saved on every fill)
  → state, status, heartbeat  (always, before anything optional)
  → alerts and the bundle/manager request (each isolated: their errors can't stop trading)

Nothing here talks to an LLM: the manager's playbook arrives as a validated file.
Stops and the hard flatten trigger on the HIGHER of the last two bid samples, so a single
glitch quote can't fire them (a real move shows up in two polls 10 s apart).
"""
from __future__ import annotations

import json
import os
import statistics
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

import config
from alerts.bale import Alerter
from bridge.inbox import Inbox
from bridge.outbox import build_bundle, market_section, portfolio_summary, write_bundle
from data import bars as bars_mod
from data.poll import MarketSnapshot
from engine import core, exits, flatten
from engine.exchange import Exchange
from engine.executor import Executor
from engine.gate import GateContext, Verdict, check
from engine.gate import mode as gate_mode
from engine.logs import Logs
from engine.portfolio import Portfolio
from engine.reconcile import reconcile
from engine.live import adopt_balances
from engine.types import URGENCY_RANK, Fill, Proposal
from manager import scorecard
from manager.mock import mock_playbook
from manager.schema import Playbook, finalize, server_validate

MARKET_FA = {"USDTTMN": "تتر/تومان", "BTCTMN": "بیت‌کوین/تومان"}
KIND_FA = {"rebalance_up": "افزایش سهم پایه", "rebalance_down": "کاهش سهم پایه", "floor": "معامله‌ی حداقل فعالیت روزانه",
           "stop": "حد ضرر", "take_profit": "حد سود", "time_stop": "پایان مهلت ۳۶ ساعته", "entry": "ورود تاکتیکی",
           "add": "افزودن به موقعیت", "exit": "خروج", "flatten": "تبدیل همه‌چیز به تومان پیش از پایان مسابقه"}
LEVEL_FA = {0: "عادی", 2: "نقشه‌ی مدیر منقضی شده — نگه‌داری، بدون خرید جدید", 3: "توقف خرید (فقط خروج‌ها فعال)"}
EARLY_REQUEST_S = 20 * 60          # ask for the next playbook this long before the current one expires
START_ALERT_EVERY_S = 10 * 60      # a crash-restart loop must not spam the phone
EVENT_AFTER_PLAYBOOK_S = 15 * 60   # no off-cycle request within this long after a playbook arrived
RECONCILE_EVERY_S = 30 * 60        # live: compare our books with the exchange this often


@dataclass
class EngineCfg:
    run_dir: Path
    mode: str = "shadow"              # shadow | live | backtest
    manager: str = "remote"           # remote (inbox from the laptop) | rules (local rule-based manager)
    sha: str = "unknown"
    end_at: datetime = config.END_AT
    loop: config.LoopCfg = field(default_factory=lambda: config.LOOP)
    start_cash: float = config.START_EQUITY_TMN


class Engine:
    def __init__(self, cfg: EngineCfg, *, broker: Exchange, market: Callable[[], dict[str, MarketSnapshot]],
                 bars: Callable[[str], pd.DataFrame | None], clock: Callable[[], float] = time.time,
                 alerter: Alerter | None = None, portfolio: Portfolio | None = None, account=None):
        """`account`: a read-only Wallex client (live mode) used for reconcile; None in shadow."""
        self.cfg, self.L = cfg, cfg.loop
        self.broker, self.market, self.bars, self.clock = broker, market, bars, clock
        self.account = account
        self.last_reconcile = clock()
        self.alerter = alerter or Alerter(None, None)
        rd = cfg.run_dir
        rd.mkdir(parents=True, exist_ok=True)
        self.state_path, self.status_path, self.heartbeat_path = rd / "state.json", rd / "status.json", rd / "heartbeat"
        self.playbook_path, self.outbox_dir = rd / "playbook.json", rd / "outbox"
        now = clock()
        self.logs = Logs(rd / "logs", sha=cfg.sha, mode=cfg.mode)
        (rd / "state.mode").write_text(cfg.mode, encoding="utf-8")   # which mode owns state.json
        if portfolio is not None:
            self.pf = portfolio
        elif self.state_path.exists():
            self.pf = Portfolio.load(self.state_path, now)
        else:
            self.pf = Portfolio(cfg.start_cash, now, day_start_equity=cfg.start_cash)
        self.ex = Executor(broker, on_fill=self._on_fill, on_event=self._on_order_event,
                           on_accept=lambda p: self.pf.note_accept(p, self.clock()), still_valid=self._still_valid,
                           urgency_cap=self._urgency_cap)
        self.inbox = Inbox(rd / "inbox")
        self.playbook: Playbook | None = None
        self.playbook_history: deque[dict] = deque(maxlen=72)
        self._restore_playbook()
        self.spreads: dict[str, deque] = {m: deque(maxlen=8640) for m in config.MARKETS}
        self.snaps: dict[str, MarketSnapshot] = {}
        self.bids: dict[str, float] = {}
        self.prev_bids: dict[str, float] = {}
        self.level: int | None = None
        self.level_changed = False
        self.request_id: str | None = None
        self.request_at = 0.0
        self.request_reason = ""
        self.last_manager_at = self.playbook.created_at if self.playbook else -1e18
        self.last_offcycle = -1e18
        self.last_bundle_at = -1e18
        self.throttle: dict[tuple, float] = {}
        self.recent: deque[dict] = deque(maxlen=20)
        self.errors: deque[float] = deque(maxlen=500)
        self.retry_alerts: list[tuple[str, str, int]] = []
        self.seq = 0
        self.alerted: set[str] = set()
        self.last_phase = None
        self._adopt_due: str | None = None
        self._targets: dict[str, float] = {}
        self._event(now, "engine_start", end_at=cfg.end_at.isoformat(), halted=self.pf.halted,
                    alert=self._start_alert(now))

    def _start_alert(self, now: float) -> str | None:
        marker = self.cfg.run_dir / "last_start_alert"
        try:
            last = float(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = -1e18
        if now - last < START_ALERT_EVERY_S:
            return None
        marker.write_text(str(now), encoding="utf-8")
        end = self.cfg.end_at.astimezone(config.TEHRAN).strftime("%Y-%m-%d %H:%M")
        return f"▶️ موتور معامله ({self.cfg.mode}) روشن شد · نسخه {self.cfg.sha} · پایان مسابقه {end} تهران"

    # ---- the tick -----------------------------------------------------------------------
    def tick(self) -> None:
        now = self.clock()
        self._poll(now)
        if not all(m in self.bids for m in config.MARKETS):
            self._throttled(now, "no_prices", alert="⚠️ قیمت بازارها در دسترس نیست؛ موتور منتظر است.")
            self._save_and_beat(now, None, None, None)
            return
        if self.pf.roll_day(now, self.bids):
            self._event(now, "day_rolled", day_start_equity=self.pf.day_start_equity)
        self.pf.mark(self.bids)
        self._accept_playbooks(now)
        level = self._degrade(now)
        ph = flatten.phase(now, self.cfg.end_at)
        if ph != self.last_phase:
            previous, self.last_phase = self.last_phase, ph
            self._event(now, "flatten_phase", phase=ph, alert=None if ph == "normal" else f"⏳ مرحله‌ی پایانی: {ph}")
            if ph in ("market", "verify") and self.account is not None:
                self._adopt_due = ph                     # done after the executor tick (fills first), retried
            if ph in ("verify", "ended") and previous is not None:
                try:                                     # T−30 min check / final check; never blocks the flatten
                    self._reconcile(now, final=ph == "ended")
                except Exception as e:
                    self._event(now, "reconcile_failed", error=f"{type(e).__name__}: {e}"[:300],
                                alert="⚠️ تطبیق حساب انجام نشد؛ موتور به فروش پایانی ادامه می‌دهد.", critical=True)
        trigger = {m: max(self.bids[m], self.prev_bids.get(m, 0.0)) for m in config.MARKETS}   # glitch guard
        gmode = gate_mode(self._ctx(now, prices=trigger))   # one bad quote can't flip the risk mode either
        if (level >= 3 or ph != "normal" or gmode == "hard_flatten") and any(
                w.side == "BUY" for w in self.ex.working.values()):
            n = self.ex.cancel_buys(now, "risk_must_not_grow")
            self._event(now, "buys_cancelled", count=n, level=level, phase=ph, gate_mode=gmode)
        props: list[Proposal] = []
        if exits.hard_flatten_due(self.pf, trigger):
            if not self.pf.halted:
                self._event(now, "hard_flatten", gain=self.pf.gain(self.bids), critical=True,
                            alert="🛑 افت ۳٪ — همه‌ی دارایی‌ها فروخته می‌شود و ربات تا دستور شما خرید نمی‌کند.")
            props += exits.hard_flatten(self.pf, self.bids, now)
        else:
            props += exits.check_exits(self.pf, trigger, now)
            props += flatten.flatten_proposals(self.pf, self.bids, ph, now)
            if ph == "normal" and level < 3:
                props += self._core_proposals(now, level, gmode)
        try:
            for p in props:
                try:                                     # one bad proposal never blocks the others
                    self._route(p, now)
                except Exception as e:
                    self._throttled(now, f"route_error:{p.market}:{p.kind}", error=f"{type(e).__name__}: {e}"[:300],
                                    alert=f"❗ خطا در ثبت سفارش {p.market} ({p.kind}): {type(e).__name__}")
            try:
                self.ex.tick(now)
            except Exception as e:
                self._throttled(now, "executor_error", error=f"{type(e).__name__}: {e}"[:300],
                                alert=f"❗ خطا در پیگیری سفارش‌ها: {type(e).__name__}")
            if self._adopt_due and self._adopt_real_balances(now, self._adopt_due):
                self._adopt_due = None                   # sell what Wallex really holds, not just our books
        finally:
            self._save_and_beat(now, level, ph, gmode)   # state + heartbeat no matter what
        steps = [lambda: self._alerts(now, gmode), lambda: self._bundle(now), lambda: self._retry_alerts(now)]
        if self.account is not None and now - self.last_reconcile >= RECONCILE_EVERY_S:
            steps.append(lambda: self._reconcile_safe(now))
        for step in steps:
            try:
                step()
            except Exception as e:                    # optional work never stops trading or the heartbeat
                self._throttled(now, "side_task_error", error=f"{type(e).__name__}: {e}"[:300])
        self.prev_bids = dict(self.bids)

    def _save_and_beat(self, now: float, level, ph, gmode) -> None:
        self.pf.save(self.state_path)
        self._write_status(now, level, ph, gmode)

    # ---- market ------------------------------------------------------------------------
    def _poll(self, now: float) -> None:
        try:
            snaps = self.market()
        except Exception as e:                       # the source should catch its own errors; be safe anyway
            snaps = {}
            self._throttled(now, "market_error", error=type(e).__name__)
        for m in config.MARKETS:
            s = snaps.get(m)
            if s is None:
                self.errors.append(now)
                continue
            if m in self.snaps and s.fetched_at <= self.snaps[m].fetched_at:
                continue                              # not refreshed yet: the 30 s staleness check handles it
            self.snaps[m], self.bids[m] = s, s.bid
            self.spreads[m].append((s.fetched_at, s.spread_bps))
        if snaps and hasattr(self.broker, "feed"):
            self.broker.feed(snaps)

    def _data_age(self, now: float) -> dict[str, float]:
        out = {}
        for m in config.MARKETS:
            s = self.snaps.get(m)
            if s is None:
                out[m] = float("inf")
                continue
            age = max(0.0, now - s.fetched_at)
            if s.last_trade_at is not None and now - s.last_trade_at > self.L.max_trade_age_s:
                age = float("inf")                   # book looks fresh but nobody has traded: don't buy into it
            out[m] = age
        return out

    def _spread_ratio(self, now: float) -> dict[str, float]:
        out = {}
        for m in config.MARKETS:
            s = self.snaps.get(m)
            if s is None:
                continue
            hist = [b for t, b in self.spreads[m] if now - t <= 86400]
            ref = statistics.median(hist) if len(hist) >= 180 else config.REF_SPREAD_BPS[m]
            out[m] = s.spread_bps / ref if ref > 0 else float("inf")
        return out

    # ---- playbooks and degrade -----------------------------------------------------------
    def _restore_playbook(self) -> None:
        for rec in self.logs.tail("playbooks", 72):
            try:
                self.playbook_history.append(
                    Playbook.model_validate({k: v for k, v in rec.items() if k not in ("git_sha", "mode")}).model_dump())
            except ValueError:
                continue
        try:                                          # the active playbook survives log rotation via its own file
            self.playbook = Playbook.model_validate_json(self.playbook_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if self.playbook_history:
                self.playbook = Playbook.model_validate(self.playbook_history[-1])

    def _activate(self, pb: Playbook, now: float) -> None:
        self.playbook = pb
        self.playbook_history.append(pb.model_dump())
        self.logs.write("playbooks", pb.model_dump())
        _atomic_write(self.playbook_path, pb.model_dump_json())
        self.request_id, self.last_manager_at = None, now
        self._event(now, "playbook_active", playbook_id=pb.playbook_id, source=pb.source, regime=pb.regime,
                    core_targets=pb.core_targets, risk_multiplier=pb.risk_multiplier)

    def _accept_playbooks(self, now: float) -> None:
        if self.cfg.manager != "remote":
            return
        pb, events = self.inbox.poll(now)
        for e in events:
            self._event(now, e.pop("event"), **{k: v for k, v in e.items() if k != "ts"})
        if pb is not None:
            self._activate(pb, now)

    def _degrade(self, now: float) -> int:
        recent_errors = sum(1 for t in self.errors if now - t <= 600)
        stale = any(now - self.snaps[m].fetched_at > config.LIMITS.stale_after_s for m in config.MARKETS)
        pb = self.playbook
        if recent_errors > self.L.wallex_errors_limit or stale or pb is None:
            level = 3
        elif now <= pb.valid_until:
            level = 0
        elif now <= pb.valid_until + self.L.playbook_grace_s:
            level = 2
        else:
            level = 3
        if level != self.level:
            self.level_changed = self.level is not None and level >= 2     # recovering to 0 is not an event
            alert = None
            if self.level is not None and level >= 2:
                alert = f"⚠️ سطح احتیاط {level}: {LEVEL_FA[level]}"
            elif self.level is not None and level == 0:
                alert = "✅ برگشت به حالت عادی"
            self._event(now, "degrade_level", level=level, previous=self.level, errors_10min=recent_errors,
                        stale=stale, has_playbook=pb is not None, alert=alert, key=f"level{level}")
            self.level = level
        return level

    def _effective(self, level: int) -> tuple[float, bool]:
        """(risk multiplier, adds allowed). Level 2 keeps the targets — so nothing is sold just because the
        laptop is late — but allows no increases until a fresh playbook arrives."""
        pb = self.playbook
        if pb is None or level >= 3:
            return 0.0, False
        if level == 2:
            return pb.risk_multiplier, False
        return pb.risk_multiplier, config.REGIMES[pb.regime].allow_adds

    # ---- gate context ------------------------------------------------------------------
    def _ctx(self, now: float, supersede: tuple[str, str, str] | None = None, exclude_key: str | None = None,
             prices: dict[str, float] | None = None) -> GateContext:
        bids, pf = prices or self.bids, self.pf
        buys, sells = self.ex.pending_tmn(supersede, exclude_key)
        pb = self.playbook
        _, adds = self._effective(self.level if self.level is not None else 3)
        return GateContext(
            now=now, equity=pf.equity(bids), gain=pf.gain(bids), daily_pnl=pf.daily_pnl(bids),
            sleeve_value=pf.sleeve_values(bids), adds_allowed=adds,
            core_cap={m: config.REGIMES[pb.regime].bounds[m][1] for m in config.MARKETS} if pb else {},
            tactical_allowed={m: False for m in config.MARKETS},      # tactical sleeve not live (step T2)
            pending_buy=buys, pending_sell=sells, halted=pf.halted,
            flatten_phase=flatten.phase(now, self.cfg.end_at),
            data_age_s=self._data_age(now), spread_ratio=self._spread_ratio(now),
            tactical_entries_today=dict(pf.tactical_entries), core_increases_today=dict(pf.core_increases),
            entries_today=pf.entries_today, core_stopped_at=dict(pf.core_stopped_at))

    def _still_valid(self, p: Proposal) -> bool:
        """Re-check a working order before crossing the spread — without counting the order itself
        (its own pending size, and its own count if it already part-filled)."""
        ctx = self._ctx(self.clock(), exclude_key=Executor.key_of(p))
        if p.increases_risk and p.decision_id in self.pf.counted_decisions:
            counts = dict(ctx.tactical_entries_today if p.sleeve == "tactical" else ctx.core_increases_today)
            counts[p.market] = max(0, counts.get(p.market, 0) - 1)
            field = "tactical_entries_today" if p.sleeve == "tactical" else "core_increases_today"
            ctx = replace(ctx, **{field: counts, "entries_today": max(0, ctx.entries_today - 1)})
        v = check(p, ctx)
        if v.action == "allow":
            return True
        if v.action != "resize":
            return False
        # A resize is fine only if the cap still covers what is left of the order (rounding, fees).
        w = self.ex.working.get(Executor.key_of(p))
        if w is None:
            return False
        left_tmn = w.remaining * self.ex.ex.quote(p.market)[1]
        return v.size_tmn >= 0.99 * left_tmn

    def _urgency_cap(self, p: Proposal) -> str:
        """How far an order may escalate: take-profits stay maker; core reductions may cross the spread
        but never go to market; the final sell-off follows its phase; stops and hard flatten may go to market."""
        if p.kind == "take_profit":
            return "passive"
        if p.kind == "rebalance_down":
            return "aggressive"
        if p.kind == "flatten" and p.decided_by == "flatten":
            return flatten.URGENCY.get(flatten.phase(self.clock(), self.cfg.end_at), "market")
        if p.increases_risk:
            return "aggressive"
        return "market"

    def _adopt_real_balances(self, now: float, ph: str) -> bool:
        try:
            changes = adopt_balances(self.pf, self.account.balances(), self.bids, now)
        except Exception as e:
            self._throttled(now, "adopt_failed", every_s=60, phase=ph, error=f"{type(e).__name__}: {e}"[:300])
            return False                                 # retried next tick
        moved = {k: v for k, v in changes.items() if k != "TMN" and abs(v["from"] - v["to"]) > 1e-12}
        self._event(now, "balances_adopted", phase=ph, changes=changes,
                    alert=f"ℹ️ دفتر ربات با موجودی واقعی والکس هماهنگ شد ({', '.join(moved)})" if moved else None)
        self.pf.save(self.state_path)
        return True

    # ---- proposals -------------------------------------------------------------------------
    def _did(self, now: float) -> str:
        self.seq = (self.seq + 1) % 100
        return f"{int(now) % 10**8:08d}{self.seq:02d}"

    def _core_proposals(self, now: float, level: int, gmode: str) -> list[Proposal]:
        pb = self.playbook
        if pb is None:
            return []
        mult, adds = self._effective(level)
        eq = self.pf.equity(self.bids)
        current = {m: self.pf.exposure(m, self.bids, "core") for m in config.MARKETS}
        targets = core.core_targets(pb.regime, current, overrides=pb.core_targets, risk_multiplier=mult, mode=gmode)
        self._targets = targets
        out = []
        for m in config.MARKETS:
            if self._working(m, "core"):
                continue
            p = core.plan_rebalance(m, targets[m], current[m], eq, increases_today=self.pf.core_increases[m],
                                    adds_allowed=adds, decision_id=self._did(now),
                                    held_qty=self.pf.pos(m, "core").qty, price=self.bids[m])
            if p:
                out.append(p)
        ctx = self._ctx(now)
        tradable = [p for p in out if check(p, ctx).ok]
        if not tradable and core.floor_due(now, trades_today=self.pf.activity_today, mode=gmode, regime=pb.regime,
                                           daily_stopped=self.pf.daily_pnl(
                                               {m: max(b, self.prev_bids.get(m, 0.0)) for m, b in self.bids.items()})
                                           <= -config.LIMITS.daily_loss):
            # the floor trades the furthest-below-target market that can actually trade right now
            for m in sorted(config.MARKETS, key=lambda k: current[k] - targets[k]):
                fp = core.floor_proposal({m: targets[m]}, current, eq, increases_today=dict(self.pf.core_increases),
                                         adds_allowed=adds, decision_id=self._did(now))
                if fp and not self._working(m, "core") and check(fp, ctx).ok:
                    out.append(fp)
                    break
        return out

    def _working(self, market: str, sleeve: str, side: str | None = None, urgency: str | None = None) -> bool:
        for w in self.ex.working.values():
            if w.market == market and w.p.sleeve == sleeve and (side is None or w.side == side):
                if urgency is None or URGENCY_RANK[w.urgency] >= URGENCY_RANK[urgency]:
                    return True
        return False

    def _route(self, p: Proposal, now: float) -> None:
        if p.side == "SELL" and self._working(p.market, p.sleeve, "SELL", p.urgency):
            return                                     # an equally urgent sell is already working
        sup = (p.market, p.sleeve, p.urgency) if p.side == "SELL" else None
        v = check(p, self._ctx(now, supersede=sup))
        if v.ok and p.side == "SELL":
            held = self.pf.pos(p.market, p.sleeve).qty
            close = "G_DUST_CLOSE_ALL" in v.rules or v.size_tmn >= held * self.bids[p.market] * 0.999
            p = replace(p, qty=held if close else min(p.qty or held, v.size_tmn / self.bids[p.market]))
        cid = self.ex.submit(p, v, now) if v.ok else None
        if cid is None:                                # blocked or refused: log the same thing at most every 15 min
            key = ("decision", p.market, p.sleeve, p.kind, p.side, v.rules, v.ok)
            if now - self.throttle.get(key, -1e18) < self.L.blocked_log_every_s:
                return
            self.throttle[key] = now
        self._log_decision(p, v, cid, now)

    def _log_decision(self, p: Proposal, v: Verdict, cid: str | None, now: float) -> None:
        pb, s = self.playbook, self.snaps.get(p.market)
        target = self._targets.get(p.market)
        cur = self.pf.exposure(p.market, self.bids, p.sleeve)
        if v.ok and cid:
            verdict_fa = "مجاز" if v.action == "allow" else f"با تغییر حجم ({', '.join(v.rules)})"
        elif v.ok:
            verdict_fa = "مجاز، اما سفارش ثبت نشد (سفارش مشابه در جریان است)"
        else:
            verdict_fa = f"رد شد ({', '.join(v.rules)})"
        detail = f"سهم فعلی {cur:.1%}" + (f"، هدف {target:.1%}" if target is not None and p.sleeve == "core" else "")
        reason = (f"{KIND_FA.get(p.kind, p.kind)} در {MARKET_FA[p.market]} ({detail}، "
                  f"حجم {v.size_tmn if v.ok else p.size_tmn:,.0f} تومان) · تصمیم‌گیرنده: {p.decided_by}"
                  + (f" · حالت «{pb.regime}»" if pb else "") + f" · کنترل ریسک: {verdict_fa}")
        rec = {"ts": now, "decision_id": p.decision_id, "market": p.market, "sleeve": p.sleeve, "kind": p.kind,
               "side": p.side, "decided_by": p.decided_by, "urgency": p.urgency,
               "playbook_id": pb.playbook_id if pb else None, "regime": pb.regime if pb else None,
               "snapshot": {"bid": s.bid, "ask": s.ask, "spread_bps": round(s.spread_bps, 2),
                            "imbalance": round(s.imbalance, 3)} if s else None,
               "exposure_sleeve": round(cur, 4), "target": target,
               "proposed_tmn": round(p.size_tmn), "qty": p.qty,
               "gate": {"verdict": v.action, "size_tmn": round(v.size_tmn), "rules": list(v.rules)},
               "order_client_id": cid, "reason_fa": reason}
        self.logs.write("decisions", rec)
        self.recent.appendleft({k: rec[k] for k in ("ts", "decision_id", "market", "kind", "side", "decided_by",
                                                    "order_client_id")} | {"verdict": v.action,
                                                                           "rules": list(v.rules)})

    # ---- executor callbacks ------------------------------------------------------------------
    def _on_fill(self, f: Fill) -> None:
        self.pf.apply_fill(f)
        self.pf.note_fill(f)                           # counters count filled decisions, once each
        self.logs.write("fills", f.__dict__)
        self.pf.save(self.state_path)                  # a crash right after a fill must not lose it

    def _on_order_event(self, e: dict) -> None:
        self.logs.write("orders", e)
        if e["event"] in ("order_rejected", "order_place_failed", "cancel_unconfirmed", "order_missing"):
            self.errors.append(e["ts"])

    # ---- alerts ------------------------------------------------------------------------------
    def _alerts(self, now: float, gmode: str) -> None:
        day = str(self.pf.day)
        guarded = {m: max(b, self.prev_bids.get(m, 0.0)) for m, b in self.bids.items()}   # one glitch print isn't a loss
        if self.pf.daily_pnl(guarded) <= -config.LIMITS.daily_loss:
            self._once(now, f"daily-{day}", "daily_loss_stop", "⚠️ زیان روزانه به ۱٪ رسید؛ تا نیمه‌شب خرید جدیدی انجام نمی‌شود.")
        if gmode == "micro":
            self._once(now, "micro", "micro_mode", "⚠️ افت ۲٪ — حالت حداقلی: حداکثر ۱۰٪ سرمایه و یک معامله در روز.")
        non_tmn = sum(p.value(self.bids[m]) for (m, _), p in self.pf.positions.items() if p.qty > 0)
        if flatten.alert_due(now, non_tmn, self.cfg.end_at):
            self._once(now, "t-2h", "non_tmn_before_end",
                       f"⚠️ دو ساعت به پایان: هنوز {non_tmn:,.0f} تومان دارایی غیرتومانی مانده است.", critical=True)

    def _once(self, now: float, key: str, kind: str, text: str, critical: bool = False) -> None:
        if key not in self.alerted:
            self.alerted.add(key)
            self._event(now, kind, alert=text, key=key, critical=critical)

    def _throttled(self, now: float, kind: str, every_s: float = 600.0, **kw) -> None:
        if now - self.throttle.get(("event", kind), -1e18) >= every_s:
            self.throttle[("event", kind)] = now
            self._event(now, kind, **kw)

    def _event(self, now: float, kind: str, *, alert: str | None = None, key: str | None = None,
               critical: bool = False, **kw) -> None:
        self.logs.write("events", {"ts": now, "event": kind, **kw})
        if alert:
            sent = self.alerter.send(alert, key=key or kind, force=critical)
            if critical and not sent and self.alerter.enabled:
                self.retry_alerts.append((alert, key or kind, 0))

    def _retry_alerts(self, now: float) -> None:
        pending, self.retry_alerts = self.retry_alerts, []
        for text, key, tries in pending:
            if not self.alerter.send(text, key=key, force=True) and tries < 30:
                self.retry_alerts.append((text, key, tries + 1))

    # ---- reconcile (live only, read-only) ----------------------------------------------------
    def _reconcile_safe(self, now: float) -> None:
        try:
            self._reconcile(now)
        except Exception as e:
            self.last_reconcile = now                      # retry next period, not every tick
            self._event(now, "reconcile_failed", error=f"{type(e).__name__}: {e}"[:300], key="reconcile_failed",
                        alert="⚠️ تطبیق دوره‌ای حساب انجام نشد (خطای خواندن از والکس).")

    def _reconcile(self, now: float, final: bool = False) -> None:
        if self.account is None:
            return
        self.last_reconcile = now
        known = {w.client_id for w in self.ex.working.values()}
        rep = reconcile(self.pf, self.account.balances(), self.account.open_orders(), self.bids, known)
        non_tmn = sum(p.value(self.bids[m]) for (m, _), p in self.pf.positions.items() if p.qty > 0)
        self._event(now, "reconcile", ok=rep.ok, final=final, diffs=rep.asset_diffs, foreign=rep.foreign_orders,
                    orphans=rep.orphan_orders, other_assets=rep.other_assets, non_tmn_tmn=round(non_tmn))
        if final:
            text = (f"🏁 پایان مسابقه: دارایی {self.pf.equity(self.bids):,.0f} تومان "
                    f"({self.pf.gain(self.bids):+.2%})، غیرتومانی {non_tmn:,.0f} تومان. {rep.summary_fa()}")
            self.alerter.send(text, key="final", force=True)
        elif not rep.ok or self.last_phase == "verify":
            self.alerter.send(rep.summary_fa(), key=f"reconcile-{rep.ok}", force=self.last_phase == "verify")

    # ---- bundles and manager requests ----------------------------------------------------
    def _bundle(self, now: float) -> None:
        if now - self.last_bundle_at < self.L.bundle_every_s:
            return
        self.last_bundle_at = now
        grids = {m: self.bars(m) for m in config.MARKETS}
        if any(g is None or len(g) < 60 for g in grids.values()):
            self._throttled(now, "bundle_skipped_no_bars")
            return
        markets = {m: market_section(grids[m], self.snaps.get(m), now) for m in config.MARKETS}
        stale = {m: s["stats"]["bars_age_min"] for m, s in markets.items() if s["stats"]["bars_age_min"] > 60}
        if stale:
            self._throttled(now, "bars_stale", every_s=3600, ages_min=stale,
                            alert=f"⚠️ داده‌ی کندل قدیمی است ({stale} دقیقه)؛ مدیر با داده‌ی ناقص تصمیم می‌گیرد.")
        reason = self._manager_due(now, markets)
        gm = gate_mode(self._ctx(now))
        h1 = {m: bars_mod.resample(grids[m], "1h") for m in config.MARKETS}
        prev = [{"playbook_id": p["playbook_id"], "created_at": p["created_at"], "source": p["source"],
                 "regime": p["regime"], "core_targets": p["core_targets"], "risk_multiplier": p["risk_multiplier"],
                 "rationale_fa": p["rationale_fa"][:300]} for p in reversed(self.playbook_history)]
        bundle = build_bundle(
            now=now, markets=markets, portfolio=portfolio_summary(self.pf, self.bids, gm),
            recent_decisions=list(self.recent), previous_playbooks=prev[:5],
            forecast_scorecard=scorecard.score(list(self.playbook_history), h1, now),
            tactical={"live": False, "note": "tactical sleeve not live yet (waiting for its walk-forward test)"},
            manager_due=self.request_id is not None, reason=reason or "none",
            manager_request_id=self.request_id)
        write_bundle(bundle, self.outbox_dir)
        self.inbox.note_issued(bundle["hash"], bundle["created_at"])
        if self.cfg.manager == "rules" and self.request_id is not None:
            pb = finalize(mock_playbook(bundle), bundle_hash=bundle["hash"], now=now, source="mock", model="rules-v1")
            pb, _ = server_validate(pb, accepted_at=now)
            self._activate(pb, now)

    def _manager_due(self, now: float, markets: dict) -> str | None:
        changed, self.level_changed = self.level_changed, False
        if self.request_id is not None:
            if now - self.request_at >= self.L.request_reissue_s:
                self._open_request(now, self.request_reason.split(" (reissued)")[0] + " (reissued)")
            return self.request_reason
        pb, reason = self.playbook, None
        if (pb is None or now - self.last_manager_at >= self.L.manager_every_s
                or pb.valid_until - now <= EARLY_REQUEST_S):
            reason = "scheduled"
        elif (now - self.last_offcycle >= self.L.offcycle_min_gap_s
              and now - self.last_manager_at >= EVENT_AFTER_PLAYBOOK_S):     # a fresh playbook already saw it
            for m, sec in markets.items():
                st = sec["stats"]
                if st["vol_1h_30d"] > 0 and abs(st["ret_1h"]) > self.L.event_move_sigma * st["vol_1h_30d"]:
                    reason = f"event: {m} 1h move {st['ret_1h']:+.2%}"
            if self.pf.daily_pnl(self.bids) <= self.L.event_drawdown:
                reason = reason or f"event: daily P&L {self.pf.daily_pnl(self.bids):+.2%}"
            if changed:
                reason = reason or f"event: degrade level {self.level}"
            if reason:
                self.last_offcycle = now
        if reason:
            self._open_request(now, reason)
        return reason

    def _open_request(self, now: float, reason: str) -> None:
        self.request_id, self.request_at, self.request_reason = f"req-{int(now)}", now, reason
        self._event(now, "manager_request", request_id=self.request_id, reason=reason)

    # ---- status + heartbeat --------------------------------------------------------------------
    def _write_status(self, now: float, level, ph, gmode) -> None:
        bids, pf, pb = self.bids, self.pf, self.playbook
        priced = all(m in bids for m in config.MARKETS)
        status = {
            "ts": now, "mode": self.cfg.mode, "manager": self.cfg.manager, "git_sha": self.cfg.sha,
            "level": level, "phase": ph, "gate_mode": gmode, "halted": pf.halted,
            "equity": round(pf.equity(bids)) if priced else None,
            "gain": round(pf.gain(bids), 5) if priced else None,
            "daily_pnl": round(pf.daily_pnl(bids), 5) if priced else None,
            "max_drawdown": round(pf.max_drawdown, 5), "cash": round(pf.cash),
            "exposure": {m: {s: round(pf.exposure(m, bids, s), 4) for s in ("core", "tactical")}
                         for m in config.MARKETS} if priced else None,
            "prices": {m: {"bid": s.bid, "ask": s.ask, "age_s": round(max(0.0, now - s.fetched_at), 1),
                           "last_trade_age_s": round(now - s.last_trade_at) if s.last_trade_at else None}
                       for m, s in self.snaps.items()},
            "playbook": None if pb is None else {
                "playbook_id": pb.playbook_id, "source": pb.source, "regime": pb.regime,
                "core_targets": pb.core_targets, "risk_multiplier": pb.risk_multiplier,
                "valid_for_min": round((pb.valid_until - now) / 60, 1), "rationale_fa": pb.rationale_fa},
            "working_orders": len(self.ex.working), "activity_today": pf.activity_today,
            "recent_decisions": list(self.recent)[:8], "request_id": self.request_id,
            "end_at": self.cfg.end_at.isoformat(),
        }
        _atomic_write(self.status_path, json.dumps(status, ensure_ascii=False, default=str))
        _atomic_write(self.heartbeat_path, str(now))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
