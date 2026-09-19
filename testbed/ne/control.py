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
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_STATE_DIR = HERE.parent / "state"

#: The phases an operator may switch to. `steady` is the resting state the lab starts in.
PHASES = ("steady", "cut", "repair")

#: Where a running lab describes itself (v0.18.0, F131). Beside the phase file because it is the
#: same kind of thing — live state a separate process needs to read — and because that means one
#: directory locates the whole lab.
LAB_FILE = "lab.json"


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


@dataclass(frozen=True)
class Lab:
    """A running lab, as it describes itself on disk (v0.18.0, F131).

    **Why this exists.** `control.py cut` used to print
    ``watch it form: http://127.0.0.1:8080/`` — a hardcoded port. A lab moved off 8080, which is
    the ordinary case the moment anything else holds that port, therefore told its operator to
    open a URL that was not the lab. The port the lab is *actually* on is known only to the
    process that chose it, so that process writes it down.

    It also removes the environment variable. `NETCORENOC_TESTBED_STATE` still overrides, but an
    operator who ran `make lab` in one terminal and `make lab-cut` in another no longer needs to
    export anything: the default state directory holds the descriptor, and the descriptor holds
    the rest.
    """

    http_port: int
    trap_port: int
    pid: int
    scenario: str
    db: str

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}/"

    def running(self) -> bool:
        """Is the process that wrote this still alive?

        A descriptor outlives an ungracefully killed lab, and a stale one that still prints a
        confident URL is the same defect as the hardcoded port: an operator sent somewhere
        nothing is listening. `signal 0` asks the kernel without touching the process.
        """
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # alive, owned by someone else
        return True


def write_lab(lab: Lab, directory: Path | None = None) -> None:
    """Record how to reach this lab. Atomic, for the reason the phase file is."""
    directory = directory or state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{LAB_FILE}.tmp"
    tmp.write_text(json.dumps(lab.__dict__, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, directory / LAB_FILE)


def read_lab(directory: Path | None = None) -> Lab | None:
    """The running lab, or None when there is no readable descriptor.

    Forgiving in one direction only, like :func:`read`: a missing or malformed file means *"no
    lab"*, which is a true statement an operator can act on, rather than a traceback.
    """
    directory = directory or state_dir()
    try:
        raw = json.loads((directory / LAB_FILE).read_text(encoding="utf-8"))
        return Lab(
            http_port=int(raw["http_port"]),
            trap_port=int(raw["trap_port"]),
            pid=int(raw["pid"]),
            scenario=str(raw["scenario"]),
            db=str(raw["db"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_lab(directory: Path | None = None) -> None:
    """Remove the descriptor when the lab stops, so the next reader is not told a lie."""
    directory = directory or state_dir()
    (directory / LAB_FILE).unlink(missing_ok=True)


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


def _no_lab_message() -> str:
    """What to say when nothing is running, including the command that starts one."""
    return (
        f"No lab is running (no readable {LAB_FILE} in {state_dir()}).\n"
        "Start one with:\n"
        "    make lab\n"
        "If your lab uses a different state directory, point this at it:\n"
        "    NETCORENOC_TESTBED_STATE=<dir> python testbed/control.py status"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive the NetCoreNOC testbed's live phase.")
    parser.add_argument("phase", nargs="?", choices=[*PHASES, "status"], default="status")
    args = parser.parse_args(argv)

    # Read once, up front: every branch below wants to name the lab's real address, and a
    # hardcoded 8080 is what this replaces (F131).
    lab = read_lab()
    if lab is not None and not lab.running():
        print(  # noqa: T201
            f"warning: {state_dir() / LAB_FILE} describes pid {lab.pid}, which is gone. "
            "The lab was killed without unwinding; treating it as stopped.",
            file=sys.stderr,
        )
        lab = None

    if args.phase == "status":
        current = read()
        print(f"phase={current.name} seq={current.seq} dir={state_dir()}")  # noqa: T201
        if lab is None:
            print(_no_lab_message())  # noqa: T201
            return 0
        print(  # noqa: T201
            f"lab pid={lab.pid} scenario={lab.scenario} trap={lab.trap_port}/udp console={lab.url}"
        )
        return 0

    if lab is None:
        print(_no_lab_message(), file=sys.stderr)  # noqa: T201
        return 1

    moved = write(args.phase)
    print(f"the lab is now {moved.name!r} (transition {moved.seq})")  # noqa: T201
    if moved.name == "cut":
        print(f"watch it form: {lab.url}  ->  Situations")  # noqa: T201
        print("then repair it: python testbed/control.py repair")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
