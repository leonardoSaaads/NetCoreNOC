"""The chain over the **real surfaces** — UDP in, HTTP out — and the witness that `drive.py` agrees.

`PREREGISTRATION-0.14.0.md` §5.3, literally:

> 1. Boot a real appliance on an **empty** database, **real UDP**, migrations applied at boot.
> 3. **Label through `POST /api/situations/{sid}/feedback`** — the route the console calls, never by
>    writing to the store.

`drive.py` runs the same loop in process and is where the ten-increment census comes from. It is not
a shortcut and it is not a substitute: it calls the same parser on the same wire bytes and the same
`engine.apply_feedback` the route calls. What it does not exercise is the socket, the allowlist, the
community tagging, authentication, RBAC, request validation, scope resolution and the audit row —
**and those are exactly what this module exercises**, on the same increments, so the two censuses
can be compared rather than assumed equal.

Ten increments here would be about half an hour of wall clock, because the receiver stamps a trap on
**arrival**: `spread_beyond_tau` has to reach past `TAU_S` and stay under `WINDOW_S`, so its
95-second gap is 95 seconds of real time and no time scale can compress it without changing the
shape. So this
runs the first increments and the gate reports which number came from which path.

    python eval/simulation/drive_http.py --increments 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess  # nosec B404 - registering an artefact through the CLI is the sanctioned path
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "eval"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from netcorenoc import model_version  # noqa: E402
from simulation.appliance import Appliance, Http, Sender, from_wire  # noqa: E402
from simulation.drive import fit_kind, training_rows  # noqa: E402
from simulation.generator import generate  # noqa: E402
from simulation.labelling import PRINCIPALS, truth_of  # noqa: E402

import trap_replay  # noqa: E402

TRAP_PORT = 11_162
HTTP_PORT = 18_080
DEMO_DB = "/tmp/netcorenoc-demo-http.db"  # nosec B108 - a throwaway demonstration database

# Increments must not reach into each other, and over real UDP that is real time:
# `correlate.WINDOW_S` is 120 s and the receiver stamps on arrival.
INCREMENT_GAP_S = 125.0
# How long to wait for the engine to drain an increment's traps before labelling.
SETTLE_S = 6.0


def send_increment(sender: Sender, increment: int) -> int:
    """One increment over the socket, in real time. Returns the number of datagrams sent.

    The delays are the generator's and are honoured at 1x. `trap_replay.encode_trap` builds the same
    PDU `tools/trap_sim.py --send` builds, so the bytes on the wire are the bytes the project's own
    simulator emits — there is one encoder and this is it.
    """
    document = generate(increment)
    events = sorted(document["events"], key=lambda e: float(e["delay"]))
    start = time.monotonic()
    for event in events:
        due = float(event["delay"]) - (time.monotonic() - start)
        if due > 0:
            time.sleep(due)
        payload = trap_replay.encode_trap(
            event["trap_oid"],
            event["varbinds"],
            "public",
            uptime_ticks=int((time.monotonic() - start) * 100),
        )
        sender.send(event["source"], payload)
    return len(events)


def label_situation(
    client: Http, situation: dict[str, Any], truth: dict[tuple[str, str], str]
) -> tuple[str, int]:
    """**The simulated operator, over the route.** Returns `(verdict, members marked)`.

    The rule is `drive.label_increment`'s, unchanged and unchangeable: §5.4 fixes the labelling rule
    and this ran after the shape was registered and before any verdict was seen. Every member shares
    one truth key -> `confirm`; two or more keys -> `split`, marking the **minority** key's members.

    The member list comes from `GET /api/situations/{sid}`, so the ids sent as `excluded_ids` are
    ids the *console* would have rendered — which is the whole point of labelling through the route.
    The server reconciles them against its own membership and writes `excluded_reconciled`; F46 is
    the finding that made that the server's number rather than the client's.
    """
    by_key: dict[str, list[int]] = defaultdict(list)
    for alarm in situation["alarms"]:
        device = from_wire(str(alarm["device_ip"]))
        key = truth.get((device, str(alarm["class_oid"])))
        by_key[key or f"unknown-{device}"].append(int(alarm["id"]))
    member_ids = sorted(int(alarm["id"]) for alarm in situation["alarms"])
    if len(by_key) > 1:
        minority = min(by_key.values(), key=lambda ids: (len(ids), ids[0]))
        client.post(
            f"/api/situations/{situation['id']}/feedback",
            {"verdict": "split", "member_ids": member_ids, "excluded_ids": sorted(minority)},
        )
        return "split", len(minority)
    client.post(
        f"/api/situations/{situation['id']}/feedback",
        {"verdict": "confirm", "member_ids": member_ids},
    )
    return "confirm", 0


def label_increment(
    clients: list[Http],
    reader: Http,
    truth: dict[tuple[str, str], str],
    start: int,
    done: set[int],
) -> dict[str, int]:
    """Every unmerged, not-yet-labelled situation, one verdict each, round-robin over three.

    Which situations are already labelled is tracked **here** rather than asked of the server,
    because no read route publishes it — `GET /api/situations` returns the situation, not the
    operator's opinion of it. Re-posting would not corrupt anything (F36 made a repeat verdict a
    no-op that still answers 200) but it would inflate this loop's own bag count, and a driver that
    miscounts its own work is not a witness to anything.
    """
    counts = {"confirm": 0, "split": 0, "marked": 0, "bags": 0}
    listed = reader.get("/api/situations?limit=500")
    for index, row in enumerate(sorted(listed, key=lambda r: int(r["id"]))):
        sid = int(row["id"])
        if sid in done or str(row.get("status")) == "merged":
            continue
        detail = reader.get(f"/api/situations/{sid}")
        if not detail.get("alarms"):
            continue
        client = clients[(start + index) % len(clients)]
        verdict, marked = label_situation(client, detail, truth)
        done.add(sid)
        counts[verdict] += 1
        counts["marked"] += marked
        counts["bags"] += 1
    return counts


def register_kind(kind: str, params: dict[str, Any]) -> int:
    """Register a fitted kind as an artefact, through the **CLI the maintainer would use.**

    `netcorenoc promotion register` runs `model_version.validate_document` — the same validator the
    load path runs — and prints, every time, that *"this is an ARTEFACT, not a promotion"*. There is
    no HTTP route that creates a model version, and that is a design decision rather than a gap: the
    thing that could put a new model in front of traffic is not reachable from the network.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "netcorenoc",
            "promotion",
            "register",
            "--kind",
            kind,
            "--params",
            # `canonical_object`, never `json.dumps` with my own separators: it is the
            # serialisation the store holds and the one `params_hash` is taken over, so any other
            # rendering of the same numbers would register a document nobody could reproduce.
            model_version.canonical_object(params),
        ],
        env={**os.environ, "NETCORENOC_DB": DEMO_DB, "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("registered model version"):
            return int(line.split()[3])
    raise RuntimeError(f"the CLI registered nothing:\n{result.stdout}\n{result.stderr}")


def propose(admin: Http, model_version_id: int) -> dict[str, Any]:
    """**Step 5: run the census, the judge and the promotion gate.** The server decides everything.

    `POST /api/promotion` names a model version and nothing else — no verdict field, no metrics
    field, no `floors_met` field, because a request that could assert any of them would be a request
    that decides its own outcome. What comes back is the server's derivation, and a refusal is
    written to the `promotion` table exactly as an application is.
    """
    return dict(admin.post("/api/promotion", {"model_version_id": model_version_id}))


def run(increments: int, kinds: tuple[str, ...], verbose: bool = True) -> dict[str, Any]:
    """Boot, replay, label, fit, register, propose — every step over a socket, TCP or the CLI."""
    history: list[dict[str, Any]] = []
    decisions: dict[str, Any] = {}
    with Appliance(DEMO_DB, TRAP_PORT, HTTP_PORT) as appliance:
        admin = appliance.sign_in()
        clients = [Appliance.mint(admin, name, "editor") for name in PRINCIPALS]
        sender = Sender(("127.0.0.1", TRAP_PORT))
        done: set[int] = set()
        try:
            for increment in range(increments):
                if increment:
                    time.sleep(INCREMENT_GAP_S)
                sent = send_increment(sender, increment)
                time.sleep(SETTLE_S)
                labelled = label_increment(
                    clients, admin, truth_of(increment), increment * 20, done
                )
                history.append({"increment": increment, "datagrams": sent, "labelled": labelled})
                if verbose:
                    print(  # noqa: T201
                        f"  increment {increment:>2}: {sent:>4} datagrams  "
                        f"bags {labelled['bags']:>3} "
                        f"(confirm {labelled['confirm']}, split {labelled['split']}, "
                        f"marked {labelled['marked']})"
                    )
        finally:
            sender.close()
        stats = admin.get("/api/stats")

        # Steps 4-6. The rows are the store's, derived through `training.derive` exactly as
        # `drive.py` derives them, and the fit is the same `fit_document` the console would call.
        rows = asyncio.run(_rows(DEMO_DB))
        for kind in kinds:
            params = asyncio.run(fit_kind(kind, rows))
            version = register_kind(kind, params)
            decisions[kind] = {"model_version_id": version, **propose(admin, version)}
            if verbose:
                decision = decisions[kind]
                print(  # noqa: T201
                    f"\n  {kind}: model version {version} -> {decision['status']} / "
                    f"{decision['verdict']}  (seal queries {decision['seal_query_count']})"
                )
                print(f"      triggers: {', '.join(decision['triggers'])}")  # noqa: T201
                for line in str(decision.get("reason") or "").splitlines():
                    print(f"      {line}")  # noqa: T201
                for note in decision.get("unavailable", []):
                    print(f"      unavailable: {note}")  # noqa: T201
    return {
        "increments": increments,
        "history": history,
        "receiver": stats.get("receiver", {}),
        "training_rows": len(rows),
        "decisions": decisions,
    }


async def _rows(db_path: str) -> list[Any]:
    """The labelled corpus as training rows, read from the demonstration database."""
    from netcorenoc.store import Store

    store = Store(db_path)
    await store.open()
    try:
        return await training_rows(store)
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--increments", type=int, default=2)
    parser.add_argument("--kinds", default="tree,forest,gradient_boosting")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    print(  # noqa: T201
        f"===== the chain over REAL UDP and the REAL route, empty database at {DEMO_DB} ====="
    )
    report = run(args.increments, tuple(args.kinds.split(",")))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))  # noqa: T201
    receiver = report["receiver"]
    print(  # noqa: T201
        f"\n  receiver: {receiver.get('received', '?')} received, "
        f"{receiver.get('parsed', '?')} parsed, "
        f"{receiver.get('dropped', '?')} dropped, "
        f"{receiver.get('rejected', '?')} rejected"
    )
    print(f"  training rows derived: {report['training_rows']}")  # noqa: T201
    verdicts = {kind: d["verdict"] for kind, d in report["decisions"].items()}
    print(f"  verdicts: {verdicts}")  # noqa: T201
    seals = {d["seal_query_count"] for d in report["decisions"].values()}
    print(f"  seal query count on the demonstration database: {sorted(seals)}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
