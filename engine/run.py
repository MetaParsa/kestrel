"""Run the engine.

    # live market data, simulated fills, playbooks from the laptop (what runs before go-live)
    uv run python -m engine.run --mode shadow --run-dir run/shadow
    # shadow baseline: same data, rule-based manager, no Claude, no Clef (A/B evidence during the contest)
    uv run python -m engine.run --mode shadow --manager rules --run-dir run/baseline
    # live: real orders. Refuses unless WALLEX_LIVE_ARMED=yes-real-money is set — only at the user's "go live"
    uv run python -m engine.run --mode live --run-dir run/main

Errors inside a tick are logged and alerted; the loop keeps running.
"""
from __future__ import annotations

import argparse
import threading
import time
import traceback
from pathlib import Path

import pandas as pd

import config
from alerts.bale import Alerter
from data import bars, fetch
from data.poll import Poller
from engine.live import LiveBroker, live_startup
from engine.logs import git_sha
from engine.loop import Engine, EngineCfg
from engine.modes import ShadowBroker
from wallex.client import WallexClient, WallexError
from wallex.trading import TradingClient, env_armed

REPO = Path(__file__).resolve().parent.parent


class LiveBars:
    """1-minute grids per market: the parquet store at start, refreshed from Wallex every few minutes."""

    def __init__(self, client: WallexClient, markets: list[str], refresh_s: float = config.LOOP.bars_refresh_s):
        self.client, self.markets, self.refresh_s = client, markets, refresh_s
        self.raw = {m: fetch.load(m) for m in markets}
        self.grids: dict[str, pd.DataFrame] = {}
        self.last_refresh = -1e18
        self._lock = threading.Lock()

    def refresh(self, now: float) -> None:
        if now - self.last_refresh < self.refresh_s:
            return
        self.last_refresh = now
        end = int(now) // 60 * 60
        for m in self.markets:
            # catch up from the last bar we have (≤ 2 days back), one day per request (> 1440 bars = no_data)
            last = int(self.raw[m]["t"].max()) if len(self.raw[m]) else end - 2 * 86400
            t0 = max(last - 3600, end - 2 * 86400)
            frames = []
            while t0 < end:
                t1 = min(t0 + 86400 - 60, end)
                try:
                    cols = self.client.candles(m, "1", t0, t1)
                except WallexError:
                    break
                if cols["t"]:
                    frames.append(pd.DataFrame(cols)[fetch.COLUMNS])
                t0 = t1 + 60
            if frames:
                df = fetch._merge(self.raw[m], pd.concat(frames, ignore_index=True))
                self.raw[m] = df[df["t"] >= end - 31 * 86400]
        with self._lock:
            self.grids = {m: bars.to_minute_grid(self.raw[m]) for m in self.markets if len(self.raw[m])}

    def __call__(self, market: str):
        with self._lock:
            return self.grids.get(market)


def archive_shadow_state(run_dir: Path) -> Path | None:
    """Simulated shadow positions must never be read as real holdings: move them aside."""
    state, marker = run_dir / "state.json", run_dir / "state.mode"
    mode = marker.read_text(encoding="utf-8").strip() if marker.exists() else ("shadow" if state.exists() else "")
    if state.exists() and mode != "live":
        dest = run_dir / f"state.{mode or 'unknown'}-{int(time.time())}.json"
        state.replace(dest)
        return dest
    return None


def start_live(cfg: EngineCfg, client: WallexClient, poller: Poller, live_bars: LiveBars, alerter: Alerter) -> Engine:
    """Go-live path: both locks open, then cancel our stale orders and reconcile before trading."""
    if not env_armed():
        raise SystemExit("refusing live mode: WALLEX_LIVE_ARMED is not set (the user arms it at go-live)")
    if not client.has_key:
        raise SystemExit("refusing live mode: no Wallex key (WALLEX_KEY_FILE / WALLEX_API_KEY)")
    archive_shadow_state(cfg.run_dir)
    (cfg.run_dir / "state.mode").write_text("live", encoding="utf-8")   # from now on state.json is real
    notes: list[dict] = []
    broker = LiveBroker(client, TradingClient.from_env(armed=True), on_note=notes.append)
    snaps = poller.poll_once()
    if not all(m in snaps for m in config.MARKETS):
        raise SystemExit("refusing live mode: no prices for every market")
    broker.feed(snaps)
    live_bars.refresh(time.time())
    pf, rep = live_startup(client, broker, cfg.run_dir / "state.json", {m: s.bid for m, s in snaps.items()})
    engine = Engine(cfg, broker=broker, market=poller.snapshot, bars=live_bars, alerter=alerter,
                    portfolio=pf, account=client)
    broker.on_note = lambda e: engine._event(time.time(), e.pop("event"), **e)
    summary = (f"🔴 حالت واقعی روشن شد · سفارش‌های لغوشده‌ی قبلی: {len(rep.cancelled)} · "
               f"سفارش‌های خارجی: {len(rep.foreign_orders)} · "
               + ("شروع تازه از موجودی حساب" if rep.fresh_start else "ادامه از وضعیت ذخیره‌شده")
               + (f" · اصلاح دفتر از روی صرافی: {', '.join(rep.adopted)}" if rep.adopted else "")
               + f" · {rep.reconcile.summary_fa()}")
    engine._event(time.time(), "live_startup", cancelled=rep.cancelled, foreign=rep.foreign_orders,
                  adopted=rep.adopted, fresh_start=rep.fresh_start, notes=notes, alert=summary, critical=True)
    return engine


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["shadow", "live"], default="shadow")
    ap.add_argument("--manager", choices=["remote", "rules"], default="remote")
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--ticks", type=int, default=0, help="stop after N ticks (0 = forever)")
    ap.add_argument("--clear-halt", action="store_true",
                    help="the user's restart after a −3%% hard flatten: clear the latch in state.json and exit")
    a = ap.parse_args(argv)

    if a.clear_halt:
        from engine.portfolio import Portfolio
        path = a.run_dir / "state.json"
        pf = Portfolio.load(path, time.time())
        print(f"halted={pf.halted} reason={pf.halt_reason!r} → cleared")
        pf.halted, pf.halt_reason = False, ""
        pf.save(path)
        return

    if a.mode == "live" and a.manager != "remote":
        raise SystemExit("refusing: the rule-based baseline never trades live")
    try:
        refuse_mode_mismatch(a.run_dir, a.mode)
        with single_instance(a.run_dir):
            run_engine(a)
    except (SystemExit, Exception) as e:
        if not isinstance(e, KeyboardInterrupt):
            alert_start_failure(a.run_dir, e)
        raise


START_ALERT_EVERY_S = 30 * 60


def alert_start_failure(run_dir: Path, e: BaseException, alerter: Alerter | None = None) -> None:
    """systemd restarts us every 5 s: alert once per 30 min, and wait for the send before exiting."""
    marker = run_dir / "start_failed"
    try:
        if marker.exists() and time.time() - marker.stat().st_mtime < START_ALERT_EVERY_S:
            return
        run_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{type(e).__name__}: {e}"[:500], encoding="utf-8")
        alerter = alerter or Alerter.from_env(sync=True)
        alerter.send(f"🚨 موتور {run_dir.name} شروع نشد: {type(e).__name__}: {str(e)[:300]}", force=True)
    except Exception:
        pass                                         # never mask the original error


def refuse_mode_mismatch(run_dir: Path, mode: str) -> None:
    """A shadow engine must never pick up REAL live state (e.g. after a redeploy reset the unit file):
    it would manage real coins as if they were simulated."""
    marker = run_dir / "state.mode"
    if mode == "shadow" and marker.exists() and marker.read_text(encoding="utf-8").strip() == "live" \
            and (run_dir / "state.json").exists():
        raise SystemExit("refusing shadow mode: this run dir holds LIVE state. Set WALLEX_MODE=live "
                         "(or move run/main/state.json aside on purpose).")


class single_instance:
    """One engine per run dir (an fcntl lock on Linux; a best-effort pid file elsewhere)."""

    def __init__(self, run_dir: Path):
        self.path = run_dir / "engine.lock"
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass                                    # Windows dev machine: no lock
        except OSError:
            raise SystemExit(f"another engine is already running on {self.path.parent}")
        return self

    def __exit__(self, *exc):
        self.fh.close()


def run_engine(a) -> None:
    client = WallexClient.from_env()
    poller = Poller(client, list(config.MARKETS), interval_s=POLL_EVERY_S)
    live_bars = LiveBars(client, list(config.MARKETS))
    alerter = Alerter.from_env()
    cfg = EngineCfg(run_dir=a.run_dir, mode=a.mode, manager=a.manager, sha=git_sha(REPO))
    if a.mode == "live":
        engine = start_live(cfg, client, poller, live_bars, alerter)
    else:
        poller.poll_once()
        live_bars.refresh(time.time())
        engine = Engine(cfg, broker=ShadowBroker(), market=poller.snapshot, bars=live_bars, alerter=alerter)
    # Network reads run in their own threads, so a slow Wallex can never stall stops or the heartbeat.
    stop = threading.Event()
    threading.Thread(target=poller.run, args=(stop,), name="poller", daemon=True).start()
    threading.Thread(target=_bars_loop, args=(live_bars, stop), name="bars", daemon=True).start()
    n, failures = 0, 0
    while True:
        started = time.time()
        try:
            engine.tick()
            failures = 0
        except Exception as e:
            failures += 1
            try:
                engine._event(time.time(), "tick_error", error=f"{type(e).__name__}: {e}"[:500],
                              trace=traceback.format_exc()[-2000:], key="tick_error",
                              alert=f"❗ خطای موتور: {type(e).__name__} — ادامه می‌دهد" if failures in (1, 10) else None)
            except Exception:
                pass                                # e.g. disk full: keep the loop alive anyway
        n += 1
        if a.ticks and n >= a.ticks:
            stop.set()
            alerter.flush()
            break
        time.sleep(max(1.0, config.LOOP.tick_s - (time.time() - started)) if failures < 5 else 30.0)


POLL_EVERY_S = 5.0


def _bars_loop(live_bars: "LiveBars", stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            live_bars.refresh(time.time())
        except Exception:
            pass                                    # the bundle step flags stale bars itself
        stop.wait(60.0)


if __name__ == "__main__":
    main()
