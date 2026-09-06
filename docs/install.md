# Installing NetCoreNOC

Four routes, and a fifth way to try it with no equipment at all. All of them give you the same
thing: **one process, one SQLite file, one static web console**, with five Python dependencies and
no build step. Pick by what you already run.

Python **3.12 or newer** is required everywhere.

| Route | Use it when | Hardened by default |
|---|---|---|
| [Docker Compose](#docker-compose) | you want the shortest path to a running appliance | yes |
| [Docker](#docker) | you already have an orchestrator | yes, with the flags below |
| [pip](#pip) | you are developing, or packaging it yourself | no — see [`security.md`](security.md) |
| [Nix](#nix) | you run NixOS or use flakes | no |

Then read [`operate.md`](operate.md) — the first-boot password is printed once and there is one
detail about signing in that will otherwise look like a bug.

## Docker Compose

```sh
docker compose up --build
```

That is the whole setup. It runs with a read-only root filesystem, every Linux capability dropped
except `CAP_NET_BIND_SERVICE` (needed to bind privileged UDP 162), no privilege escalation, and the
database on a named volume so it survives `docker compose down`.

Optional configuration lives in `.env`:

```sh
cp .env.example .env    # then edit; .env is git-ignored and is the only place secrets belong
```

Point your equipment's SNMP trap destination (v2c or v1) at the host, open `http://<host>:8080/`,
and go to [`operate.md`](operate.md).

### Resource limits, and what happens when one is hit

`docker-compose.yml` sets `deploy.resources`: **1.0 CPU** and **512 MiB**, with 0.25 CPU and
128 MiB reserved. Both are deliberately generous — the appliance is one asyncio process with one
SQLite file and a bounded in-memory queue — and the two limits fail in **completely different
ways**, which is the reason to state them rather than only set them.

| limit | what happens when it is reached | what it costs you |
|---|---|---|
| **memory** | the kernel OOM-kills the process; `restart: unless-stopped` brings it back and the WAL means the database survives | **every trap in flight is lost and no ingest gap is recorded** — the gap counter lives in the process that died. This is the one failure the appliance cannot account for afterwards. |
| **cpus** | the process is throttled, not killed | correlation falls behind the wire. `queue_depth` climbs in the health control, and if the queue fills, overflow **is** counted as an ingest gap. Visible, and recoverable. |

So if you are unsure, **raise the memory limit before the CPU limit**: one costs latency you can
see, the other costs traps you cannot.

`tests/test_perf.py::burst` drives 100 000 traps in one second through the real ingest path, which
is what 512 MiB is sized about four times over. One CPU is what a single event loop is bounded by
anyway; giving the container more does not make correlation faster.

To change them, edit `deploy.resources` in `docker-compose.yml`. With plain `docker run`, the
equivalents are `--cpus 1.0 --memory 512m`.

## Docker

```sh
docker build -t netcorenoc .
docker run -d --name netcorenoc \
  --read-only --cap-drop ALL --cap-add CAP_NET_BIND_SERVICE \
  --security-opt no-new-privileges --tmpfs /tmp \
  --cpus 1.0 --memory 512m \
  -p 162:162/udp -p 8080:8080 \
  -v netcorenoc-data:/home/netcorenoc \
  netcorenoc
docker logs netcorenoc     # the one-time bootstrap admin password is here
```

**Do not drop the volume flag.** Without it the SQLite file lives in the container's writable layer
and everything learned is lost when the container is replaced.

If you would rather not grant `CAP_NET_BIND_SERVICE`, map a high port instead and tell the appliance
about it:

```sh
docker run -d --cap-drop ALL -e NETCORENOC_TRAP_PORT=1162 -p 1162:1162/udp ... netcorenoc
```

Your equipment then has to be configured to send to 1162, which most vendors support.

## pip

```sh
python3.12 -m venv .venv
.venv/bin/pip install .
.venv/bin/python -m netcorenoc.main
```

Binding UDP 162 needs privileges. Unprivileged, run on a high port:

```sh
NETCORENOC_TRAP_PORT=1162 .venv/bin/python -m netcorenoc.main
```

The database is created as `netcorenoc.db` in the working directory; set `NETCORENOC_DB` to put it
somewhere deliberate.

### As a systemd service

[`deploy/netcorenoc.service`](../deploy/netcorenoc.service) is a hardened unit file — a dedicated
user, `ProtectSystem=strict`, `NoNewPrivileges`, and `AmbientCapabilities=CAP_NET_BIND_SERVICE` so
the process can bind 162 without running as root.

**The unit's `ExecStart` is `/opt/netcorenoc/.venv/bin/python`**, so install there rather than in
the working directory the [pip](#pip) section uses — a unit pointing at a venv that does not exist
fails to start, and `systemd-analyze verify` is what says so:

```sh
sudo python3.12 -m venv /opt/netcorenoc/.venv
sudo /opt/netcorenoc/.venv/bin/pip install .
sudo cp deploy/netcorenoc.service /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/netcorenoc.service   # silence means it will start
sudo systemctl daemon-reload && sudo systemctl enable --now netcorenoc
journalctl -u netcorenoc | grep -i bootstrap     # the one-time admin password
```

Environment variables go in a drop-in (`systemctl edit netcorenoc`), not in the unit file, so an
upgrade does not overwrite them.

## Nix

```sh
nix run github:leonardoSaaads/NetCoreNOC
# or, in a clone:
nix run .
```

The flake pins nixpkgs 24.11 and Python 3.12. `nix develop` gives you the development shell with the
test tooling.

## Trying it without any hardware

See [`operate.md`](operate.md#3-sending-traps) — the bundled scenarios are sent as **real SNMP PDUs
over UDP**, not loaded into the database, so the correlation you see afterwards is the appliance's
own work.

## Upgrading

Stop the process, install the new version, start it again. Schema migrations are **forward-only,
idempotent, and applied at startup** — there is no separate migration step and no downgrade path.
Take a copy of the SQLite file first if the data matters; that copy is the rollback.

[`MIGRATION.md`](../MIGRATION.md) records what each version changed and the two or three upgrades
that need you to know something.

## Verifying what you installed

```sh
python -m netcorenoc audit verify     # walks the audit chain, reports the first broken link
make checksums                        # the vendored asset bytes against their pinned SHA-256
```
