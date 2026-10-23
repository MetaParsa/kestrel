"""Run the engine with human oversight switched on (off by default; not what traded in the contest).

    uv run python -m oversight.run --mode shadow --run-dir run/shadow     # same flags as engine.run
    uv run python -m oversight.bot --run-dir run/shadow                   # the Bale command channel, separately

Identical to `python -m engine.run` except that the engine class is `OverseenEngine`, which reads the
oversight state file every tick. engine/run.py is reused as is (its `Engine` name is swapped here)
so the deployed entry point and its safety checks stay exactly the code that traded.
"""
from __future__ import annotations

import engine.run as engine_run
from oversight.engine import OverseenEngine


def main(argv=None):
    engine_run.Engine = OverseenEngine          # start_live() and run_engine() build the engine by this name
    engine_run.main(argv)


if __name__ == "__main__":
    main()
