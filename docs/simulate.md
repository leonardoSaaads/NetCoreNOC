# Simulating traps without equipment

No switch, OLT or router is needed to try NetCoreNOC, develop it or reproduce a problem. The tools
in this repository send **real SNMP traps over UDP** to a running appliance, exactly as a device
would. Nothing is written to the database directly, so every device, alarm, situation and clear
you see afterwards is the appliance's own work.

| Method | What it sends | Use it to |
|---|---|---|
| [Bundled scenarios](#3-send-traps-terminal-2) (`tools/trap_replay.py`) | Thirteen labelled incidents: fibre cut, OLT storm, PON dying gasp, card failure, a staged DWDM fibre cut, flapping, noise… | See correlation on realistic traffic |
| [DSL scenarios](#3-send-traps-terminal-2) (`tools/trap_sim.py`) | Short declarative patterns: BGP flap, chassis card, login burst | Reproduce one alarm pattern |
| [Your own scenario](#write-your-own-scenario) | Any trap you describe in a JSON file | Test a specific device's traps, a clear, a repeat |
| [Synthetic load](#3-send-traps-terminal-2) (`trap_replay.py --synthetic`) | Random traps at a fixed rate | Check throughput and queueing |
| [`snmptrap`](#one-trap-at-a-time-with-snmptrap) (net-snmp) | One trap per command | Poke the appliance by hand, or from another machine |
| [The lab](#the-lab-a-fibre-cut-you-control) (`make lab`) | Two simulated OLTs; you cut and repair the fibre | Watch a storm form and clear, interactively |

Every command below was run against this release on Linux.

## Linux

### 1. Prepare (once)

Python 3.12 or newer and Git. On Debian or Ubuntu: `sudo apt install python3.12-venv git make`.

```sh
git clone https://github.com/leonardoSaaads/NetCoreNOC.git
cd NetCoreNOC
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### 2. Start the appliance (terminal 1)

```sh
NETCORENOC_DB=sim.db NETCORENOC_TRAP_PORT=1162 NETCORENOC_HTTP_PORT=8081 \
  .venv/bin/python -m netcorenoc.main
```

* Port **1162** needs no root (162 does).
* `sim.db` keeps test traffic apart from any real database. Delete `sim.db*` to start over.
* The first start prints a one-time `admin` password. Open <http://localhost:8081/>, sign in as
  `admin` and choose a new password.

Leave it running. Stop it with `Ctrl+C`.

### 3. Send traps (terminal 2)

A bundled scenario, with `make`:

```sh
make replay-list                     # every scenario, both kinds
make replay SCENARIO=fiber_cut       # two devices, one fibre cut
make replay SCENARIO=olt_storm       # a larger storm
```

The same without `make`:

```sh
.venv/bin/python tools/trap_replay.py eval/corpus/fiber_cut.json --port 1162
.venv/bin/python tools/trap_replay.py eval/corpus/olt_storm.json --port 1162 --time-scale 0.1
```

`--time-scale` multiplies the gaps between traps: `0.1` is ten times faster, `0` sends at full
speed.

A DSL scenario:

```sh
.venv/bin/python tools/trap_sim.py --list
.venv/bin/python tools/trap_sim.py login_burst --send --port 1162
```

Synthetic load (20 devices, 10 trap types, 200 traps/s for 10 s):

```sh
.venv/bin/python tools/trap_replay.py --synthetic 20 --classes 10 --rate 200 --duration 10 --port 1162
```

### 4. Check what arrived

In the console: **Situations** for what was grouped, the health control in the top bar for the
counters. From the terminal:

```sh
curl -c cookies -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<your password>"}' http://localhost:8081/api/login
curl -b cookies http://localhost:8081/api/stats
```

| Field | Expect |
|---|---|
| `receiver.received` | every datagram that arrived |
| `devices`, `classes` | one per simulated source address, one per trap type |
| `active_alarms` | rises on a fault, falls when its clear arrives |
| `receiver.quarantined` | 0 — otherwise open **Quarantine** to see why a packet was refused |

## Windows

The appliance runs on Linux. On Windows, use **WSL 2** (Windows Subsystem for Linux): the appliance
and the tools then run exactly as on Linux, and the browser on Windows reaches the console.

1. Open **PowerShell as Administrator** and install Ubuntu (restart if asked):

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

2. Open **Ubuntu** from the Start menu, create the Linux user it asks for, then install the
   prerequisites:

   ```sh
   sudo apt update && sudo apt install -y python3.12-venv git make
   ```

3. Clone **inside the Linux file system** (your home directory, not `/mnt/c/...`: it is much faster
   and keeps Linux file permissions), then follow [Linux](#linux) steps 1 to 4 unchanged:

   ```sh
   cd ~
   git clone https://github.com/leonardoSaaads/NetCoreNOC.git
   cd NetCoreNOC
   python3.12 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   ```

4. Open a **second Ubuntu window** for step 3 (sending traps).
5. Open the console in your Windows browser at <http://localhost:8081/>. WSL 2 forwards the
   console's TCP port to Windows.

Run the sending tools **in WSL as well**, not in Windows: the simulated devices are loopback
addresses (`127.0.0.2`, `127.0.0.3`, …) on the machine that runs the appliance.

**Why not native Windows Python.** The appliance uses POSIX signal handling and `os.statvfs`, which
Windows' Python does not provide, so `python -m netcorenoc.main` does not run there. **Why not
Docker Desktop for simulation.** Docker Desktop rewrites the source address of every packet it
forwards, so all simulated devices arrive as one and cross-device correlation cannot be seen. It is
fine for exploring the console, not for judging correlation.

## Write your own scenario

A scenario is a JSON file. This one takes a switch port down and brings it back 20 seconds later;
save it as `my_port.json` (anywhere outside `eval/corpus/`, which is the evaluation gate's input):

```json
{
  "name": "my_port",
  "description": "One switch port goes down, then comes back.",
  "events": [
    {
      "delay": 0,
      "source": "127.0.0.20",
      "trap_oid": "1.3.6.1.6.3.1.1.5.3",
      "varbinds": [
        {"oid": "1.3.6.1.2.1.2.2.1.1.7", "kind": "int", "value": 7},
        {"oid": "1.3.6.1.2.1.2.2.1.2.7", "kind": "str", "value": "GigabitEthernet0/7"}
      ]
    },
    {
      "delay": 20,
      "source": "127.0.0.20",
      "trap_oid": "1.3.6.1.6.3.1.1.5.4",
      "varbinds": [
        {"oid": "1.3.6.1.2.1.2.2.1.1.7", "kind": "int", "value": 7},
        {"oid": "1.3.6.1.2.1.2.2.1.2.7", "kind": "str", "value": "GigabitEthernet0/7"}
      ]
    }
  ]
}
```

```sh
.venv/bin/python tools/trap_replay.py my_port.json --port 1162
```

The port's alarm appears as active, then **cleared** when the `linkUp` arrives.

| Field | Meaning |
|---|---|
| `delay` | Seconds from the start of the scenario (not from the previous event). |
| `source` | The device's address. Addresses in `127.0.0.0/8` are used as they are; any other address is sent from `127.x.y.z` (same last three octets) and the rewrite is printed. |
| `trap_oid` | The notification's OID (`snmpTrapOID.0`). |
| `varbinds` | Objects carried by the trap. `kind` is one of `int`, `str`, `oid`, `ticks`, `gauge`, `counter`. |

Useful standard trap OIDs:

| Trap | OID | Notes |
|---|---|---|
| `coldStart` | `1.3.6.1.6.3.1.1.5.1` | device rebooted |
| `linkDown` | `1.3.6.1.6.3.1.1.5.3` | carry `ifIndex` (`1.3.6.1.2.1.2.2.1.1.<n>`) so each port is its own alarm |
| `linkUp` | `1.3.6.1.6.3.1.1.5.4` | clears the `linkDown` with the same `ifIndex` |
| `authenticationFailure` | `1.3.6.1.6.3.1.1.5.5` | |

Vendor traps work the same way: the built-in trap pack names more than ten thousand of them (for
example Huawei `hwBoardFail`, `1.3.6.1.4.1.2011.5.25.219.2.2.3`). An X.733 perceived severity
sent as a `str` varbind (for example at `1.3.6.1.2.1.118.1.2.2.1.4`) is read as the alarm's
severity: `critical`, `major`, `minor`, `warning` or `indeterminate`. The word `cleared` ends the
alarm, like a `linkUp`.

**Patterns worth trying:** the same `linkDown` repeated with no `linkUp` (a repeat stays in its
situation, see [`operate.md`](operate.md#4-how-alarms-repeat-clear-and-come-back)); `linkDown` and
`linkUp` alternating every few seconds (a flapping port); several sources failing within a second
(a storm).

## One trap at a time with `snmptrap`

The net-snmp command-line tools send single traps. Install them in Linux or WSL with
`sudo apt install snmp`.

```sh
# linkDown on port 7, SNMPv2c ('' lets snmptrap fill in sysUpTime)
snmptrap -v 2c -c public 127.0.0.1:1162 '' 1.3.6.1.6.3.1.1.5.3 \
  1.3.6.1.2.1.2.2.1.1.7 i 7  1.3.6.1.2.1.2.2.1.2.7 s "GigabitEthernet0/7"

# its clear
snmptrap -v 2c -c public 127.0.0.1:1162 '' 1.3.6.1.6.3.1.1.5.4 \
  1.3.6.1.2.1.2.2.1.1.7 i 7  1.3.6.1.2.1.2.2.1.2.7 s "GigabitEthernet0/7"

# the same trap from a second simulated device
snmptrap --clientaddr=127.0.0.5 -v 2c -c public 127.0.0.1:1162 '' 1.3.6.1.6.3.1.1.5.3 \
  1.3.6.1.2.1.2.2.1.1.3 i 3

# SNMPv1 (generic trap 2 = linkDown)
snmptrap -v 1 -c public 127.0.0.1:1162 1.3.6.1.4.1.8072.2.3 127.0.0.1 2 0 '' \
  1.3.6.1.2.1.2.2.1.1.9 i 9
```

`snmptrap` is also how to reach an appliance on **another machine**: replace `127.0.0.1:1162` with
its address and port. Every trap then comes from your machine's own address, so it appears as one
device.

An **SNMPv3** trap needs the user configured on **Settings → SNMP** first (its form prints this
command with your choices), then:

```sh
snmptrap -v3 -l authPriv -u noc -a SHA -A 'auth-passphrase' -x AES -X 'priv-passphrase' \
  127.0.0.1:1162 '' 1.3.6.1.6.3.1.1.5.3 1.3.6.1.2.1.2.2.1.1.3 i 3
```

**The device is the trap's UDP source, never an address inside it** — `snmpTrapAddress` and any
"node" varbind included, since anyone can write those. A script that simulates two nodes must send
each node's traps from its own address (`--clientaddr=127.0.0.101`, `--clientaddr=127.0.0.102` on
the appliance's machine); otherwise every trap is one device. An address the trap *names* (a peer,
a far end) is read too: it is how the appliance relates two elements it knows nothing else about.
`make replay SCENARIO=dwdm_staged_fibre_cut` sends a two-node staged DWDM fibre cut that way.

## The lab: a fibre cut you control

Two simulated GPON OLTs at different addresses, an appliance, and two commands: cut and repair.

```sh
make lab            # starts everything and leaves it running
make lab-cut        # in a second terminal: the fibre is cut, the storm arrives
make lab-repair     # the splice is done, the alarms clear
make lab-demo       # unattended: three cut/repair cycles, then exit
```

Details, including what to watch for: [`tests/lab/README.md`](../tests/lab/README.md).

## Limits

* **Same machine.** `trap_replay.py` and `trap_sim.py` simulate devices by sending from loopback
  addresses, so they must run on the machine (or the WSL instance) that runs the appliance. For a
  remote appliance, use `snmptrap`.
* **Docker.** Traps sent from the host into a container pass through Docker's port proxy, which
  rewrites their source: all simulated devices arrive as one. Run the appliance from the virtual
  environment, as above, when simulating.
* **macOS.** Only `127.0.0.1` is bindable by default. When an address is missing, `trap_replay.py`
  stops and prints the `sudo ifconfig lo0 alias …` command that adds it.
* **Not labels.** Simulated traffic is never training data by itself. What you confirm, split or
  merge in the console is recorded as your judgement, like any operator's.
