"""Where the lab's live state lives, and why it is a file (v0.17.0, DECISIONS #330).

The maintainer's ask is *"cut the fibre now"* and *"repair it now"* — a gesture made while watching
the console, not a scenario replayed from the top. So the phase is **state**, and this is where it
lives:

    testbed/state/phase        one line: steady | cut | repair
    testbed/state/phase.seq    a counter, bumped on every transition

**Why a file rather than a socket, a queue or an HTTP endpoint.** The NE agents are separate
processes — separate *containers* under compose — and they need to see the same transition
within a second of each other. A file on a shared volume does that with no dependency, no port,
no protocol and no new runtime requirement (directive 6). It is also the thing an operator can
inspect with `cat` when the lab misbehaves, which a socket is not.

**Why the sequence number exists.** Without it an agent cannot tell *"still cut"* from *"cut
again"*. Re-cutting the fibre is a thing an operator will do — the fastest way to watch a situation
form twice — and a phase file alone makes the second cut invisible. The agent watches the pair.

The file is written atomically (write a sibling, then `os.replace`) so an agent polling mid-write
reads either the old value or the new one, never half of one.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_STATE_DIR = HERE.parent / "state"

#: The phases an operator may switch to. `steady` is the resting state the lab starts in.
PHASES = ("steady", "cut", "repair")


@dataclass(frozen=True)
class Phase:
    """What the lab is doing, and how many transitions have happened."""

    name: str
    seq: int


def state_dir() -> Path:
    """The state directory, overridable so a test never writes into the working tree."""
    return Path(os.environ.get("NETCORENOC_TESTBED_STATE", DEFAULT_STATE_DIR))


def read(directory: Path | None = None) -> Phase:
    """The current phase. A missing or unreadable file means `steady` — the lab's resting state.

    Deliberately forgiving in this one direction: an agent that crashed because the control file
    had not been created yet would make the lab's first second its most fragile.
    """
    directory = directory or state_dir()
    try:
        name = (directory / "phase").read_text(encoding="utf-8").strip()
        seq = int((directory / "phase.seq").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return Phase("steady", 0)
    return Phase(name if name in PHASES else "steady", seq)


def write(name: str, directory: Path | None = None) -> Phase:
    """Move the lab to `name`, bumping the sequence.

    Atomic, so a poller never reads half of a write.
    """
    if name not in PHASES:
        raise ValueError(f"unknown phase {name!r}; the lab has {', '.join(PHASES)}")
    directory = directory or state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    nxt = read(directory).seq + 1
    for filename, value in (("phase", name), ("phase.seq", str(nxt))):
        tmp = directory / f".{filename}.tmp"
        tmp.write_text(value + "\n", encoding="utf-8")
        os.replace(tmp, directory / filename)
    return Phase(name, nxt)


def wait_for_change(previous: Phase, poll_s: float = 0.25, timeout_s: float = 0.0) -> Phase:
    """Block until the phase or the sequence moves. `timeout_s = 0` waits forever."""
    deadline = time.monotonic() + timeout_s if timeout_s else None
    while True:
        current = read()
        if (current.name, current.seq) != (previous.name, previous.seq):
            return current
        if deadline is not None and time.monotonic() >= deadline:
            return current
        time.sleep(poll_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive the NetCoreNOC testbed's live phase.")
    parser.add_argument("phase", nargs="?", choices=[*PHASES, "status"], default="status")
    args = parser.parse_args(argv)

    if args.phase == "status":
        current = read()
        print(f"phase={current.name} seq={current.seq} dir={state_dir()}")  # noqa: T201
        return 0
    moved = write(args.phase)
    print(f"the lab is now {moved.name!r} (transition {moved.seq})")  # noqa: T201
    if moved.name == "cut":
        print("watch it form: http://127.0.0.1:8080/  ->  Situations")  # noqa: T201
        print("then repair it: python testbed/control.py repair")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
