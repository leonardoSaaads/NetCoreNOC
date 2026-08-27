# Troubleshooting

What breaks, what the symptom looks like, and what to check. Ordered roughly by how often it happens
to someone new.

## Sign-in

### `POST /api/login` returns 200 and sets no cookie

**Not a bug.** The bootstrap admin must supply `new_password` in the *same* request; a 200 with
`{"must_change_password": true}` and no cookie means *the credential is right and this account is
unusable until the password changes.* [`operate.md`](operate.md) has both requests side by side.

### Login works over `curl` but not in the browser, behind a proxy

The session cookie is `SameSite=Strict` and CSRF checks `Origin` against `Host`. A reverse proxy
that rewrites the `Host` header or drops `Origin` breaks the check, and the failure looks like the
appliance rejecting a correct password. Make the proxy preserve both.

### A password is refused with a 400

The policy is **length only**: 12–128 characters. The error names the rule. There are no composition
requirements to satisfy and no expiry to work around.

### Locked out with no admin

There is no recovery path that does not cost you the database: move the SQLite file aside and a new
one gets a new bootstrap admin. Everything learned is in that file. Prevent it with a second admin.

## Traps

### `devices` and `classes` stay at zero

Work outward:

```sh
curl -b cookies http://localhost:8080/api/stats | python -m json.tool
```

* **`receiver.received` is 0** — nothing is reaching the socket. Check that the equipment's trap
  destination is this host and this port, that the firewall passes **UDP**, and that you are not
  sending to 162 while the appliance is on 1162 (or the reverse). `NETCORENOC_TRAP_PORT` is in the
  startup log.
* **`received` climbs but `devices` does not** — the datagrams are arriving and being refused. Check
  `denied` (allowlist) and `quarantined` (parse).
* **Nothing at all, and you are in Docker** — the UDP port has to be published: `-p 162:162/udp`.
  A missing `/udp` publishes TCP, which nothing will ever connect to.

### `denied` is climbing

`NETCORENOC_ALLOWLIST` does not include the sender. It is comma-separated CIDRs and the source is
the datagram's actual source address — which, behind NAT or a relay, is not the device's address.
Denied datagrams are counted, never silently dropped, which is what makes this diagnosable.

### `quarantined` is climbing

The parser refused the datagram. Open the **Quarantine** screen — it shows the reason per entry.
(Reading that list is audited, deliberately: it holds raw refused datagrams.) Common causes are
SNMPv3 traps (unsupported: v3 needs credentials, hence configuration), truncated PDUs from a lossy
path, and oversized varbinds.

### The process will not start, naming a variable

Two settings are hard startup errors rather than warnings:

* `NETCORENOC_API_TOKEN` — removed in v0.3.0. Use service tokens.
* any `OPTICORR_*` variable — the legacy prefix, removed in v0.6.0.

The error names each variable and its replacement. It refuses rather than ignores because an ignored
`OPTICORR_ALLOWLIST` would mean every source is accepted while you believed otherwise.

### Permission denied binding UDP 162

162 is privileged. Either grant `CAP_NET_BIND_SERVICE` (the container and systemd unit both do), or
run on a high port with `NETCORENOC_TRAP_PORT=1162` and point the equipment there.

## Correlation

### Alarms that obviously belong together are not grouped

Check the timestamps first. **On a cold start, two alarms group only if they are on the same network
element and within about 21 seconds** — the class and entity affinities are still zero.
[`correlation.md`](correlation.md#cold-start-honestly) explains why, and it resolves itself as the
appliance sees your traffic.

If they are on different elements and this keeps happening for a pair you know is related, the
entity edge has not reached `MIN_EDGE_N = 5` observations yet.

### Everything is grouped into one enormous situation

Usually a storm: during a mass event everything genuinely does co-occur with everything. Storm
damping (10× above 50 alarms in the window) exists for this, and [F58 and
F61](findings.md#f58--a-storm-defeats-min_edge_n-for-every-ne-in-the-window) record the cases where
it is not enough. **Split** the situation — that is the signal that teaches it.

If it happens outside a storm, the **Link scorer** preview will show you what a higher threshold
would do to your own recent alarms before you change anything.

### Grouping changed and you do not know why

Every situation records the scorer configuration that formed it (`scorer_config_id`), and the
configuration history is immutable and append-only. The **Link scorer** screen shows the history and
rollback is one click — it moves a pointer, it never edits history.

## Load

### `queue_depth` grows and does not come back down

The one number that means the appliance is not keeping up. The trap path never blocks — the queue is
bounded and counts overflow rather than awaiting — so what you lose under sustained overload is
counted, not silent.

Check `latency_p95_s` on the same payload. If ingest is fine and the console is slow, the contention
is the store lock rather than the receiver.

```sh
make loadtest    # 1000 traps/s for 60 s against a running instance
make burst       # 100 000 traps in one second
```

### `ingest_gaps` is non-empty

The appliance is telling you it knows it missed traffic, and when. That is better than the
alternative and worth investigating at the network layer rather than here.

## Reports and the promotion gate

### Every report says `INSUFFICIENT_EVIDENCE`

**Expected, and not a fault.** The floors were registered in advance in
[`analysis/`](analysis/), before any data existed, precisely so that a corpus too small to decide
anything says so instead of producing a number. The report prints how far short you are and roughly
how many months of labelling at your current rate would close the gap.

*"The challenger is not better"* and *"this corpus cannot tell"* are opposite claims, and the report
never conflates them.

### The promotion gate refuses and you want to override it

There is no override, and there is no HTTP route that creates a model version. The gate re-derives
the floors, the power condition, the seal, the metrics and the verdict server-side; the request has
no field that could assert any of them. If you believe a floor is wrong, that is a new
pre-registration, not a setting — see [`findings.md`](findings.md) for where an opinion goes.

### A report's numbers moved and nothing changed

The four CLI reports are byte-frozen against expectations in the test suite, so a genuine drift
turns the suite red. If you are seeing movement in a *live* report, it is your corpus changing.

## The console

### Clicking something shows *"Select something to see its detail here."*

The detail panel is populated by the **Situations** view only. In the other sixteen views clicking
does nothing. It is a known defect with a measurement behind it —
[`plans/v0.15.2-console.md`](plans/v0.15.2-console.md).

### The detail panel is missing on a phone

Below 760 px it is hidden by CSS, which puts the per-term contributions out of reach on a narrow
viewport. Same brief, same release. Use a wider window until then.

### `make dom` prints "27 skipped"

Node ≥ 22 is not on `PATH`. The DOM tests **skip loudly** rather than failing, and a skipped harness
must never be read as a green one. Install Node or read the count.

## When you are stuck

```sh
python -m netcorenoc audit verify     # is the audit chain intact?
make checksums                        # are the vendored console bytes what they should be?
make qa                               # lint, types, dead code, the full suite, and the eval gate
```

Then check [`findings.md`](findings.md) — if it is a known defect it is there with a reproduction
command and its measured output.
