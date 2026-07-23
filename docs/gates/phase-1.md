# Gate 1 — Core ingestion (self-review)

Date: 2026-07-19. Verdict: **PASS**.

## Criteria and evidence

### `make qa` green

`ruff check` and `ruff format --check`: clean. `mypy --strict` over `opticorr`, `tools`,
`tests`: no issues in 15 source files. Test run:

```
45 passed in 1.73s

Name                     Stmts   Miss Branch BrPart  Cover
opticorr/__init__.py         1      0      0      0   100%
opticorr/events.py          22      0      0      0   100%
opticorr/known_oids.py      24      0      8      0   100%
opticorr/main.py           118     24     26      2    81%
opticorr/receiver.py       103      4     28      2    95%
opticorr/store.py          204      0     20      1    99%
TOTAL                      472     28     82      5    94%
Required test coverage of 85.0% reached. Total coverage: 93.68%
```

(`main.py` misses are the process entrypoint/signal wiring, exercised only in real runs.)

### Integration proof (zero configuration)

`tests/test_integration.py::test_end_to_end_ingestion_zero_config` sends real SNMPv2c
trap PDUs over UDP using `tools/trap_replay.py`'s encoder, from two distinct loopback
source addresses and two distinct enterprise OID prefixes, plus one garbage datagram,
against a receiver + engine + store started with no configuration at all. It asserts:

- both devices discovered from source IPs (`127.0.0.2`, `127.0.0.3`);
- vendor labels resolved from the bundled IANA table (`1271` → Ciena, `2011` → Huawei);
- the duplicate trap deduplicated by fingerprint (`count == 2`, one alarm row);
- the instance heuristic captured the payload varbind (`port-1/1`);
- the garbage datagram quarantined with reason `ber-decode-failed`, process unharmed.

## Defensive parsing

Property-based tests (hypothesis) feed random binaries into `parse_trap` and directly
into `datagram_received`: any input either parses or quarantines with a reason; nothing
raises past the protocol callback. SNMPv1 packets are diagnosed as
`unsupported-snmp-version-0` by peeking the BER header.

## Notes

- Dedup/flapping live in the engine (`main.py`): fingerprint upsert in SQL, periodic
  flapping demoted via a coefficient-of-variation test (Decision 6).
- `store.py` slightly exceeds the ~300-line guide (≈370 lines): it is deliberately flat,
  thin hand-written SQL — the alternative (splitting into repositories/helpers) was
  rejected as framework-building.
