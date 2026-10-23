"""WALLEX_MANAGER_EVERY_MIN asks the manager more often; unset keeps the hourly default."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def every_s(value):
    env = {k: v for k, v in os.environ.items() if k != "WALLEX_MANAGER_EVERY_MIN"}
    if value is not None:
        env["WALLEX_MANAGER_EVERY_MIN"] = value
    r = subprocess.run([sys.executable, "-c", "import config; print(config.LOOP.manager_every_s)"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    return float(r.stdout.strip().splitlines()[-1])


def test_default_is_hourly():
    assert every_s(None) == 3600.0


def test_env_shortens_the_cadence():
    assert every_s("15") == 900.0
