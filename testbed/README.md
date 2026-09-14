# `testbed/` — a two-host fibre cut you can trigger while watching it

A lab. Two simulated GPON OLTs with **different addresses** send genuine SNMPv2c trap PDUs at an
unconfigured NetCoreNOC, you cut the fibre between them with one command, and you watch the appliance
collapse the storm into one situation and name it. Then you repair it and watch it clear.

Nothing here is configured into the appliance. It starts knowing nothing — no inventory, no topology,
no vendor table — and the lab is where you check that the claim survives contact.

## One command

Without Docker — the faster loop, and the one this release actually ran:

```sh
pip install -e ".[dev]"
python testbed/run_local.py                 # appliance + two NEs, then leaves them running
```

Then, in another shell, while watching <http://127.0.0.1:8080/>:

```sh
python testbed/control.py cut               # the span goes down; the ONUs follow
python testbed/control.py repair            # the splice is done
python testbed/control.py status            # what phase is the lab in
```

Or all of it unattended, which is what CI runs:

```sh
python testbed/run_local.py --demo --cycles 3 --hold-s 18
```

With Docker:

```sh
cd testbed && docker compose up --build     # console on http://127.0.0.1:8088/
```

## What was actually run

**Read this before trusting the compose file.** v0.17.0 was built in an environment with a working
Docker daemon and **no reachable registry** — `registry-1.docker.io` answers 403 to the egress proxy,
an organisation policy denial — so no base image could be pulled and **`docker compose up` was never
executed**. The compose file is written and validated (`docker compose config` passes); it is not
proven.

Everything this release claims about the lab working was measured through `run_local.py`, repeatedly,
and every number below came from a database query rather than a log line:

| Claim | Measured |
|---|---|
| `/healthz` answers from a clean state | **1.0 s** (no image build; the appliance is one process) |
| three cut/repair cycles, start to finish | **128 s** |
| two hosts, two sources | `SELECT DISTINCT ip FROM device` → `127.0.0.2`, `127.0.0.3` |
| the cut correlates across both | one situation, **24 members**, spanning both devices |
| the appliance names it itself | `derived_name = "Storm -> 127.0.0.2 and 1 more"` |
| the repair clears it | **25 of 25 alarms cleared**, all five situations `resolved` |
| the span clears with no learning | bundled `linkDown`→`linkUp` pair, first repair |
| the ONU pairing is learned | 3 `edge` rows at `kind='clear_pair'` after 2 cycles, incl. the vendor arc |
| severity is honestly unplaced | **mid-cut**: `severity_census` → `active: 25, unplaced: 25, placed: {}` |
| …and the census is not faking a quiet network | **at rest, after the repair**: all five counts `0`, because every alarm really did clear |

The last two rows are one measurement taken twice, deliberately. `unplaced: 25` beside `active: 25`
is the number that matters — the appliance has 25 alarms and cannot place any of their severities,
and says so. Read at rest it is `0` everywhere, which is a quiet network rather than a placed one, and
quoting *that* as evidence of honest severity reporting would be quoting an empty database.

## The two hosts, and why the ONUs have no address

```
  olt-a  127.0.0.2   GPON OLT, west end      6 ONUs behind it
  olt-b  127.0.0.3   GPON OLT, east end      4 ONUs behind it
```

ONUs have no address of their own, exactly as in a real GPON network: the OLT reports on their
behalf and the ONU identity travels in a varbind. So the appliance has to work out that ten alarms
from two addresses are about fourteen different things — which is the entity inference it exists to
do, and it does it with nothing configured.

Under compose the addresses are the containers' own on the lab network (172.31.7.21 / .22) and the
agents are told theirs with `--address`. Either way an agent **binds its source address before
sending a single trap and exits non-zero if it cannot** — because `trap_replay.Sender` suppresses
that failure by design, and a silent fallback turns two hosts into one with nothing saying so.

## The scenario format

`testbed/scenarios/*.json`. Hosts, then **phases** rather than one flat timeline, because a phase is
something an operator triggers:

```json
{
  "hosts":  [{"id": "olt-a", "address": "127.0.0.2", "enterprise": 2011, "onus": ["1/1/1", "…"]}],
  "phases": {
    "steady": {"repeat_every_s": 12.0, "events": [ … ]},
    "cut":    {"events": [{"at_s": 2.2, "host": "olt-a", "per_onu": true, "spread_s": 3.5,
                           "trap_oid": "…", "varbinds": [{"oid": "…", "value": "{onu}"}]}]},
    "repair": {"events": [ … ]}
  }
}
```

* `at_s` is the offset from the moment the phase starts.
* `per_onu` fans one event out over the host's ONUs across `spread_s`, substituting `{onu}`. That is
  what makes a storm a storm rather than one trap.
* The spread is **even, not random**: a cut whose shape changes between runs is a lab you cannot
  compare two runs of, and `tests/test_testbed.py` asserts two loads are identical.

### It cannot carry ground truth, and that is the point

There is no `truth` key, and the loader **refuses one at any depth** — including inside an event,
which is where `eval/corpus/*.json` keeps it, so a labelled corpus file copied in here is rejected
rather than quietly becoming a source of labels. `tests/test_testbed.py` demonstrates that against
the real `eval/corpus/fiber_cut.json`.

`docs/analysis/PREREGISTRATION-0.10.0.md` §6 is why. **Generated traffic is traffic; only a human
gesture is evidence.** An operator who looks at the console and confirms, denies, merges or splits
what the appliance inferred has created a label, because a person made it. The scenario that emitted
the traps has no standing to say whether the appliance was right, and making truth *unrepresentable*
is a stronger guarantee than checking that it does not leak.

### The severity varbind

Every alarm-raising event carries X.733 perceived severity at RFC 3877's ALARM-MIB arc
(`1.3.6.1.2.1.118.1.2.2.1.4`), with the six tokens that are already exactly
`known_oids.SEVERITY_VOCAB`.

**The appliance still renders every alarm `unplaced`, and that is correct.** `severity.py` refuses a
ranking that observed alarm lifetimes have not confirmed (`SEVERITY_MIN_CLOSED = 50`), and the
console's Overview counts the unplaced band as a first-class number rather than showing four zeros.
A vendor-published default *with a citation* is v0.17.1's job. This release owes it a format that can
carry the field, and this is it.

### The trap OIDs, honestly

* **Standard, real, and load-bearing**: `linkDown` `1.3.6.1.6.3.1.1.5.3` and `linkUp`
  `1.3.6.1.6.3.1.1.5.4` (RFC 3418 / RFC 2863), which the appliance pairs out of the box from
  `known_oids.CLEAR_PAIR_SEEDS`; the ALARM-MIB severity arc above; `ifIndex` `1.3.6.1.2.1.2.2.1.1`.
* **Real enterprise numbers**: 2011 (Huawei) and 1271 (Ciena) are IANA PENs.
* **Illustrative below that**: the sub-arcs are *shaped* like a GPON/transport MIB and are **not read
  from one**. No vendor MIB file is in this repository, in this release or the next — vendor MIBs
  carry the vendor's copyright even when published. v0.17.1 will ship derived rows with cited
  sources instead.

## What you are watching for

1. **Two devices, not one.** Entities → two NEs. If you see one, an agent fell back to the default
   source address, which it is built to refuse.
2. **One situation, not twenty-five.** Situations → a single row whose member count climbs as the
   storm lands. The appliance names it from what it observed: *"Storm -> 127.0.0.2 and 1 more"*.
3. **The root.** The situation's root should be the span `linkDown`, not an ONU. The ONUs are
   consequences and precedence is learned from which arrived first, across cycles.
4. **Unplaced severity, counted.** Overview → the severity band reads 25 active and 25 unplaced. An
   appliance that guessed here would be lying, and the screen says which it is doing.
5. **The clears.** After `repair`, alarms go `cleared` and the situation goes `resolved`. On the
   first cycle only the span clears; from the third, the ONUs do too, because by then the stream has
   taught the appliance the vendor's raise/clear pairing (`CLEAR_CYCLES_TO_LEARN = 2`). Watching that
   change across three cycles is the product's whole thesis in about ninety seconds.

## Layout

```
testbed/
  README.md              this
  docker-compose.yml     the lab recipe; line 1 says what it relaxes and why
  Dockerfile.ne          the simulated NE image — pysnmp only, and no src/ copied in
  control.py             `python testbed/control.py cut` from the repository root
  run_local.py           the no-Docker path: appliance + two agents on loopback
  ne/
    agent.py             one NE: verify the address binds, then watch the phase and send
    control.py           the phase file, atomically written, with a sequence so a re-cut is visible
    scenario.py          the format, and the refusal of ground truth
  scenarios/
    pon_fiber_cut.json   two OLTs, ten ONUs, the cut, the storm, the clears
  state/                 the phase file and the lab database (git-ignored)
  logs/                  what each process said (git-ignored)
```

## What the lab may not do

Asserted by `tests/test_testbed.py`, each demonstrated red by an injection:

* `src/netcorenoc/` **never imports the lab** — checked over the AST, because the layer guard in
  `test_layers.py` reasons about `netcorenoc.*` names and cannot see a directory outside the package.
* the lab **cannot publish port 162 or 8080**, and publishes 8088 on loopback only.
* the lab **cannot mount `netcorenoc-data`**, the production volume.
* `testbed/` is pruned from the sdist and the Docker build context, so it cannot ship.
