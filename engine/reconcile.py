"""Compare our bookkeeping with the exchange (read-only).

Used at startup (live), before the T−30 min verify, and on demand:
    uv run python -m engine.reconcile --state run/live/state.json
Differences are reported, never "fixed" by trading: a mismatch is an alert for the
user, and foreign open orders (not placed by the engine) are flagged, never touched.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
from engine.portfolio import Portfolio
from wallex.account import OUR_ORDER_PREFIX
from wallex.client import Balance, Order


@dataclass
class ReconcileReport:
    ok: bool
    asset_diffs: dict[str, dict] = field(default_factory=dict)    # asset → {ours, exchange, diff_tmn}
    foreign_orders: list[str] = field(default_factory=list)
    orphan_orders: list[str] = field(default_factory=list)       # ours on the exchange, unknown to the executor
    other_assets: dict[str, float] = field(default_factory=dict)  # holdings outside our markets

    def summary_fa(self) -> str:
        if self.ok:
            return "تطبیق حساب: همه‌چیز با دفتر ربات یکی است."
        parts = []
        for a, d in self.asset_diffs.items():
            parts.append(f"{a}: دفتر {d['ours']:.8g} / صرافی {d['exchange']:.8g}")
        if self.foreign_orders:
            parts.append(f"{len(self.foreign_orders)} سفارش باز که ربات ثبت نکرده")
        if self.orphan_orders:
            parts.append(f"{len(self.orphan_orders)} سفارش ربات که موتور نمی‌شناسد")
        if self.other_assets:
            parts.append("دارایی خارج از بازارها: " + ", ".join(self.other_assets))
        return "⚠️ تطبیق حساب: " + " · ".join(parts)


def reconcile(pf: Portfolio, balances: dict[str, Balance], open_orders: list[Order], bids: dict[str, float],
              known_client_ids: set[str], *, tol_tmn: float = 5_000.0) -> ReconcileReport:
    rep = ReconcileReport(ok=True)
    cash_diff = float(balances.get("TMN", Balance("TMN", 0, 0)).value) - pf.cash   # type: ignore[arg-type]
    if abs(cash_diff) > tol_tmn:
        rep.asset_diffs["TMN"] = {"ours": pf.cash, "exchange": pf.cash + cash_diff, "diff_tmn": cash_diff}
    for m, base in config.BASE_ASSET.items():
        ex_qty = float(balances[base].value) if base in balances else 0.0
        diff = ex_qty - pf.qty(m)
        if abs(diff) * bids.get(m, 0.0) > tol_tmn:
            rep.asset_diffs[base] = {"ours": pf.qty(m), "exchange": ex_qty, "diff_tmn": diff * bids.get(m, 0.0)}
    ours = set(config.BASE_ASSET.values()) | {"TMN"}
    rep.other_assets = {a: float(b.value) for a, b in balances.items() if a not in ours and b.value != 0}
    for o in open_orders:
        if not o.client_id.startswith(OUR_ORDER_PREFIX):
            rep.foreign_orders.append(o.client_id or "(no client id)")
        elif o.client_id not in known_client_ids:
            rep.orphan_orders.append(o.client_id)
    rep.ok = not (rep.asset_diffs or rep.foreign_orders or rep.orphan_orders or rep.other_assets)
    return rep


def portfolio_from_balances(balances: dict[str, Balance], now: float) -> Portfolio:
    """Fresh start (no state file): the account must hold only TMN, else refuse."""
    non_tmn = {a: b for a, b in balances.items() if a != "TMN" and b.value != 0}
    if non_tmn:
        raise RuntimeError(f"account holds non-TMN assets {sorted(non_tmn)}; refusing a fresh start")
    cash = float(balances["TMN"].value)
    return Portfolio(cash, now, start_equity=config.START_EQUITY_TMN, day_start_equity=cash)


def main(argv=None):
    from data.poll import Poller
    from wallex.client import WallexClient
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", type=Path, help="omit to just list balances and open orders")
    a = ap.parse_args(argv)
    client = WallexClient.from_env()
    if a.state is None:
        bal = {k: str(b.value) for k, b in client.balances().items() if b.value != 0 or b.locked != 0}
        print("balances:", bal)
        print("open orders:", [(o.client_id, o.symbol, o.side, str(o.orig_qty)) for o in client.open_orders()])
        return
    pf = Portfolio.load(a.state, time.time())
    snaps = Poller(client, list(config.MARKETS)).poll_once()
    rep = reconcile(pf, client.balances(), client.open_orders(), {m: s.bid for m, s in snaps.items()}, set())
    print(json.dumps(rep.__dict__, ensure_ascii=False, indent=1))
    print(rep.summary_fa())


if __name__ == "__main__":
    main()
