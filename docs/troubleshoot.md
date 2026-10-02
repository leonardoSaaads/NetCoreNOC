# Troubleshooting

Symptom first, then what to check. Commands assume Docker Compose. Elsewhere, replace the
`docker compose exec netcorenoc` prefix and point the command at the database — with the bundled
systemd unit: `sudo NETCORENOC_DB=/var/lib/netcorenoc/netcorenoc.db /opt/netcorenoc/.venv/bin/python -m netcorenoc …`.

## Signing in

### I missed the first-run password

```sh
docker compose logs netcorenoc | grep -A4 "bootstrap admin"
```

It is printed as a block; the line you need reads `password: …`. If the logs were rotated away, use
the reset below.

### I forgot my password

Another admin resets it on **People & access**. If you are the only admin:

```sh
docker compose exec netcorenoc python -m netcorenoc admin reset-password <username>
```

It prints a one-time password, signs that account out everywhere, and writes an audit row.

### No admin account is left

Restart the appliance and read the log: whenever no **enabled** admin exists, the start prints a new
bootstrap password, as on first run. It uses the name `admin`, or `recovery-admin` if `admin`
exists but is not an admin. A disabled admin does not count — check `role` and `disabled`. The
appliance refuses to demote, disable or delete the last enabled admin, so this is rare.

### `POST /api/login` returns 200 and sets no cookie

The account must change its password, and the new one has to be in the **same** request
(`new_password`). [`operate.md`](operate.md#1-first-sign-in) shows the request.

### Sign-in works with `curl` but not in the browser, behind a proxy

The console checks `Origin` against `Host`. A reverse proxy that rewrites `Host` or drops `Origin`
makes a correct password look rejected. Make the proxy preserve both.

### A password is refused

The only rule is length: 12 to 128 characters.

## Traps

### `devices` and `classes` stay at zero

```sh
curl -b cookies http://localhost:8080/api/stats | python3 -m json.tool
```

* **`receiver.received` is 0** — nothing reaches the appliance. Check the device's trap destination
  and port, and that firewalls pass **UDP**. The start-up log line `listening for traps on …`
  shows the port in use. In Docker the port must be published as `/udp`; with Compose, set
  `NETCORENOC_TRAP_PORT` in `.env` and both the mapping and the process follow it.
* **`received` climbs but `devices` does not** — the traps are refused: check `denied` (allowlist)
  and `quarantined` (not a valid trap).

### Every device shows the same address

Something between the devices and the appliance rewrites the source address:

* **Docker on the same machine.** Traps sent from the host itself (for example `make replay`) go
  through Docker's port proxy and arrive from the bridge gateway. Replay without Docker, or from
  another machine.
* **Docker Desktop (macOS, Windows)** rewrites every source. Run the appliance on a Linux host.
* **A NAT or trap relay** in the path. Send traps directly, or relay without rewriting the source.

### `denied` keeps climbing

The sender is outside `NETCORENOC_ALLOWLIST` (comma-separated CIDRs). The source is the datagram's
real source address, which behind NAT is not the device's.

### `quarantined` keeps climbing

Open **Quarantine**: each entry says why it was refused. Common causes are SNMPv3 traps (not
supported), truncated packets and oversized values. Reading this list is audited.

### The process will not start and names a variable

Every unreadable setting stops the start with one sentence naming it, for example:

```
NETCORENOC_TRAP_PORT='' is not a whole number of a UDP port. Unset it to use the default (162), …
NETCORENOC_TLS_CERT is set and NETCORENOC_TLS_KEY is not. Built-in TLS needs both; …
```

A blank line in `.env` is the usual cause. `NETCORENOC_API_TOKEN` and any `OPTICORR_*` variable
are refused on purpose: they were removed, and the message names the replacement.

### Permission denied binding UDP 162

Port 162 needs `CAP_NET_BIND_SERVICE` (the Compose file and the systemd unit grant it). Or set
`NETCORENOC_TRAP_PORT=1162` and point the equipment at 1162.

## Situations

### The same alarm opened a new situation

Expected when the alarm comes back after **more than an hour of silence** with no clear, or after
an operator **closed** its situation. The old situation's history says *"its alarm went quiet
without a clear and was raised again"*. To keep repeats in one situation regardless of silence,
set `NETCORENOC_REARM_S=0`. The rules: [`operate.md`](operate.md#4-how-alarms-repeat-clear-and-come-back).

### A situation never resolves

One of its alarms is still active: its device never sent a clear (common for dying-gasp and some
vendor alarms). Clear that alarm by hand on the card, or close the situation. Reboot and
authentication-failure traps end by themselves after 5 minutes.

### A cleared port's situation stays on the board

The port bounced at least twice within the hour, so its situation is kept until the port has been
quiet for an hour — the next bounce would join it instead of opening another situation.

### Alarms that belong together are not grouped

Check which model decides (**Settings → Correlation**, or the start-up log line `grouping decided
by …`) and read *Why these were grouped* on the card. If grouping is consistently wrong for your
network, **Move**, **Merge** and **Split** teach it, and an admin can pin another model. Without a
model (the built-in formula), two alarms on different devices group only once the appliance has
learned they co-occur: [`correlation.md`](correlation.md#cold-start-honestly).

### Everything joined one huge situation

Usually a real storm — during a mass outage everything does co-occur. **Split** it; that is the
signal that teaches the appliance.

## Load and storage

### `queue_depth` grows and does not come back down

The appliance is not keeping up. Traps that do not fit are counted as an ingest gap, never silently
lost. Check `latency_p95_s`; give the container memory before CPU (one event loop uses one core).

### `ingest_gaps` is not empty

The appliance knows it missed traffic, and when. Investigate the network path and the load.

### "batch(es) failed to persist due to a storage error"

The database could not be written — usually a full disk. Free space on the volume holding
`NETCORENOC_DB`; the appliance keeps running and resumes writing by itself.

## The console

### `make dom` says "skipped"

Node 22 or newer is not on `PATH`. The console tests skip rather than fail; install Node.

## When you are stuck

```sh
docker compose exec netcorenoc python -m netcorenoc audit verify   # is the audit log intact?
docker compose logs --tail 200 netcorenoc                          # what the appliance said
make checksums                                                     # are the console files intact?
```

Then open an issue with the output and the steps that led there.
