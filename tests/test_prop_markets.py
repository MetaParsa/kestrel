"""The prop account only accepts BTCTMN orders: with the real default (no WALLEX_TRADABLE),
USDTTMN must be read-only everywhere — regime bounds, core targets, the gate and the manager."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

CHECK = r"""
import json, config
from engine.core import core_targets
from engine.gate import check
from engine.types import Proposal
out = {"tradable": list(config.TRADABLE),
       "usdt_bounds": [list(r.bounds["USDTTMN"]) for r in config.REGIMES.values()],
       "targets": core_targets("devaluation_drift", {}, overrides={"USDTTMN": 0.25, "BTCTMN": 0.2}),
       "gate": check(Proposal("USDTTMN", "core", "BUY", 500_000, "rebalance_up", "manager", "x"), None).rules}
print(json.dumps(out))
"""


def test_default_trades_btctmn_only():
    env = {k: v for k, v in os.environ.items() if k != "WALLEX_TRADABLE"}
    r = subprocess.run([sys.executable, "-c", CHECK], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["tradable"] == ["BTCTMN"]
    assert all(b == [0.0, 0.0] for b in out["usdt_bounds"])
    assert out["targets"]["USDTTMN"] == 0.0 and out["targets"]["BTCTMN"] == 0.2
    assert out["gate"] == ["G_MARKET_NOT_ALLOWED"]
