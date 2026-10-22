"""Read-only account view: what we hold, what is resting, what counts as dust.

`reconcile()` (rebuilding sleeves and cancelling our orphan orders) arrives in C2
with the executor; this module only reads.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from wallex.client import Balance, Order, WallexClient

HOME = "TMN"
OUR_ORDER_PREFIX = "d-"   # every engine order's client_id starts with this (spec v2 §2.10)


@dataclass(frozen=True)
class AccountSnapshot:
    balances: dict[str, Balance]          # non-zero only
    open_orders: list[Order]

    @property
    def tmn(self) -> Balance:
        return self.balances.get(HOME, Balance(HOME, Decimal(0), Decimal(0)))

    @property
    def our_open_orders(self) -> list[Order]:
        return [o for o in self.open_orders if o.client_id.startswith(OUR_ORDER_PREFIX)]

    @property
    def foreign_open_orders(self) -> list[Order]:
        """Open orders we did not place. Should always be empty: their presence means a manual
        order exists on the account, which the engine must report, never touch."""
        return [o for o in self.open_orders if not o.client_id.startswith(OUR_ORDER_PREFIX)]

    def non_tmn(self) -> dict[str, Balance]:
        return {a: b for a, b in self.balances.items() if a != HOME}

    def equity_tmn(self, prices_tmn: dict[str, float]) -> Decimal:
        """Total value in TMN. prices_tmn maps asset -> TMN price (e.g. mid of USDTTMN)."""
        total = self.tmn.value
        for asset, b in self.non_tmn().items():
            if asset not in prices_tmn:
                raise KeyError(f"no TMN price for {asset}")
            total += b.value * Decimal(str(prices_tmn[asset]))
        return total


def snapshot(client: WallexClient) -> AccountSnapshot:
    bals = {a: b for a, b in client.balances().items() if b.value != 0 or b.locked != 0}
    return AccountSnapshot(balances=bals, open_orders=client.open_orders())


def dust_assets(snap: AccountSnapshot, prices_tmn: dict[str, float],
                min_notional_tmn: Decimal = Decimal(50_000)) -> dict[str, Decimal]:
    """Non-TMN holdings too small to sell on their own (below Wallex's minNotional)."""
    out = {}
    for asset, b in snap.non_tmn().items():
        value = b.value * Decimal(str(prices_tmn.get(asset, 0)))
        if 0 < value < min_notional_tmn:
            out[asset] = value
    return out
