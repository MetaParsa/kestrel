"""WALLEX_FLATTEN_H shortens the sell-off window; unset keeps the defaults; bad values refuse to start."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CHECK = r"""
import json, config
from datetime import datetime
from engine.flatten import phase
end = config.END_AT.timestamp()
f = config.FLATTEN
print(json.dumps({"cfg": [f.no_new_h, f.aggressive_h, f.market_h, f.alert_h],
                  "phases": [phase(end - h * 3600) for h in (3.0, 2.0, 1.2, 0.8, 0.4, -0.1)]}))
"""


def run(value):
    env = {k: v for k, v in os.environ.items() if k != "WALLEX_FLATTEN_H"}
    if value is not None:
        env["WALLEX_FLATTEN_H"] = value
    return subprocess.run([sys.executable, "-c", CHECK], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


def test_default_window_is_unchanged():
    out = json.loads(run(None).stdout.strip().splitlines()[-1])
    assert out["cfg"][:3] == [8.0, 3.0, 1.0]
    assert out["phases"][0] == "aggressive"                  # 3 h before the end: already selling


def test_shorter_window_keeps_buying_until_its_start():
    out = json.loads(run("2.5,1.5,1.0").stdout.strip().splitlines()[-1])
    assert out["cfg"] == [2.5, 1.5, 1.0, 0.75]
    assert out["phases"] == ["normal", "no_new", "aggressive", "market", "verify", "ended"]


def test_bad_window_refuses_to_start():
    r = run("1.0,2.0,0.5")
    assert r.returncode != 0 and "decreasing" in r.stderr
