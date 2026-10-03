# `tests/lab/` — a two-host fibre cut you trigger and watch

Two simulated GPON OLTs, at **different source addresses**, send real SNMPv2c traps to an
unconfigured NetCoreNOC. You cut the fibre between them with one command and watch the appliance
group the storm into one situation; you repair it and watch the alarms clear. Nothing is
configured into the appliance: no inventory, no topology, no vendor table.

The lab is development tooling. It is never shipped (`MANIFEST.in` and `.dockerignore` prune
`tests/`) and the product never imports it.

## Run it

Linux, or Windows inside WSL2 (see [`docs/simulate.md`](../../docs/simulate.md#windows)), after the
development setup in [`CONTRIBUTING.md`](../../CONTRIBUTING.md#development-setup):

```sh
make lab            # appliance + two OLTs on loopback; leaves them running
```

The console address is printed (default <http://127.0.0.1:8080/>; another free port is chosen and
named if 8080 is taken). The one-time `admin` password is in `tests/lab/logs/appliance.log`. Then,
in a second terminal:

```sh
make lab-cut        # the span goes down; the ONUs behind it follow
make lab-repair     # the splice is done; the alarms clear
make lab-status     # which phase the lab is in
```

Unattended, as CI runs it (three cut/repair cycles, then exit):

```sh
make lab-demo
```

Without `make`, the same commands are `python tests/lab/run_local.py [--demo]` and
`python tests/lab/control.py cut|repair|status`. `run_local.py --help` lists the options (ports,
database, cycles, timings).

With Docker, from the repository root:

```sh
cd tests/lab && docker compose up --build     # console on http://127.0.0.1:8088/
```

The compose file publishes the console on loopback only, uses trap port 1162 and its own volume
(`netcorenoc-lab-data`), so it cannot take a production appliance's ports or database.

## What to watch for

1. **Two devices, not one.** Each OLT binds its own source address (`127.0.0.2`, `127.0.0.3`)
   before sending, and exits if it cannot: a silent fallback would turn two hosts into one.
2. **One situation, not dozens.** The cut storm lands as one growing situation spanning both OLTs.
3. **The root.** The span `linkDown` is the root; the ONU alarms are its consequences.
4. **Clears.** After `repair` the span clears at once (standard `linkDown`/`linkUp` pair). The ONU
   alarms use a vendor pair the appliance learns from the stream, so they clear from the third
   cycle onwards.
5. **An unplaced severity.** One cut-phase alarm (a rectifier fault) carries no severity and has no
   clear. It stays active and is counted as **unplaced** on the Overview: the appliance says it
   does not know rather than inventing a severity. Declaring a severity on its class in the console
   places it.

## Layout

```text
tests/lab/
├── README.md             this file
├── test_lab.py           the lab's guards (below)
├── run_local.py          the no-Docker path: appliance + two agents on loopback
├── control.py            cut / repair / status, from the repository root
├── docker-compose.yml    the Docker path; its header lists what it relaxes and why
├── Dockerfile.ne         the simulated network-element image (pysnmp only, no src/)
├── ne/
│   ├── agent.py          one network element: bind the address, follow the phase, send traps
│   ├── control.py        the phase file, written atomically with a sequence number
│   └── scenario.py       the scenario format, and the refusal of ground truth
├── scenarios/
│   └── pon_fiber_cut.json
├── state/                phase file and lab.db (git-ignored)
└── logs/                 one log per process (git-ignored)
```

## Scenario format

`scenarios/*.json` holds hosts and **phases** (an operator triggers a phase; a phase is not a
timeline):

```json
{
  "hosts":  [{"id": "olt-a", "address": "127.0.0.2", "enterprise": 2011, "onus": ["1/1/1"]}],
  "phases": {
    "steady": {"repeat_every_s": 12.0, "events": []},
    "cut":    {"events": [{"at_s": 2.2, "host": "olt-a", "per_onu": true, "spread_s": 3.5,
                           "trap_oid": "1.3.6.1.6.3.1.1.5.3", "varbinds": []}]},
    "repair": {"events": []}
  }
}
```

* `at_s` is the offset from the start of the phase.
* `per_onu` fans one event out over the host's ONUs across `spread_s`, substituting `{onu}` in
  varbind values. The spread is even, not random, so two runs are comparable.
* **No ground truth.** The loader refuses a `truth` key at any depth. Generated traffic is
  traffic; only an operator's judgement in the console is evidence
  ([`PREREGISTRATION-0.10.0.md`](../../docs/analysis/PREREGISTRATION-0.10.0.md) §6).

## What the lab may not do

Asserted by `test_lab.py`:

* `src/netcorenoc/` never imports the lab (checked over the AST);
* the lab never publishes port 162 or 8080, and publishes 8088 on loopback only;
* the lab never mounts `netcorenoc-data`, the production volume;
* a scenario never carries ground truth, and replays identically every time.
