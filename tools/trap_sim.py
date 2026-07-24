"""Trap simulator: run a declarative DSL scenario against a live NetCoreNOC over real UDP, or
write it to a corpus JSON file. **Test/tooling only** — never imported by ``netcorenoc/``, adds no
runtime dependency, and reuses ``tools/trap_replay.py`` for the real-PDU path. Deterministic.

Examples:
    python tools/trap_sim.py --list
    python tools/trap_sim.py login_burst --write /tmp/login_burst.json
    python tools/trap_sim.py chassis_card --send --host 127.0.0.1 --port 1162
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # trap_replay
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))  # scenario_dsl

import scenario_dsl
import trap_replay


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", choices=sorted(scenario_dsl.SCENARIOS))
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--write", type=Path, help="write the scenario as corpus JSON to PATH")
    parser.add_argument("--send", action="store_true", help="replay the scenario over real UDP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1162)
    parser.add_argument("--community", default="public")
    parser.add_argument("--time-scale", type=float, default=1.0)
    args = parser.parse_args()

    if args.list or not args.scenario:
        for name in sorted(scenario_dsl.SCENARIOS):
            print(name)
        return

    doc = scenario_dsl.SCENARIOS[args.scenario]().build()

    if args.write:
        args.write.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"wrote {len(doc['events'])} events to {args.write}")

    if args.send:
        tmp = Path(tempfile.mkdtemp()) / f"{args.scenario}.json"
        tmp.write_text(json.dumps(doc))
        sender = trap_replay.Sender((args.host, args.port))
        try:
            trap_replay.replay_fixture(sender, tmp, args.community, args.time_scale)
            print(f"sent {sender.sent} traps to {args.host}:{args.port}")
        finally:
            sender.close()
            tmp.unlink(missing_ok=True)

    if not args.write and not args.send:
        print(json.dumps(doc, indent=2))


if __name__ == "__main__":
    main()
