#!/usr/bin/env python3
"""`python testbed/control.py cut` — the operator's gesture, from the repository root.

A two-line shim so the command an operator types is the one the README prints. The mechanism lives
in `testbed/ne/control.py`, beside the agents that read it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ne.control import main

if __name__ == "__main__":
    sys.exit(main())
