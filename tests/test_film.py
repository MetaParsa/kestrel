"""The demo film (judge/film.py) must show what it claims: every act is checked on the real engine."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_film_builds_and_every_act_checks_out():
    r = subprocess.run([sys.executable, "-m", "judge.film"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "checks: all passed" in r.stdout
