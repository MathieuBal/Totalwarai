#!/usr/bin/env python3
"""Lance une bataille simulee sans installer le paquet.

Equivalent de `totalwar-ai simulate`, utile pour un depot fraichement clone :

    python scripts/run_simulation.py --scenario ranged_defense --seed 7
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from totalwar_ai.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["simulate", *sys.argv[1:]]))
