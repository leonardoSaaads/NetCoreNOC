# NetCoreNOC v0.18.0 — "the audit"

**Confirmed by execution**: the tree you were given was `0.17.1`
(`python -c "import netcorenoc; print(netcorenoc.__version__)"`). This is `0.18.0`.

Not a feature release. The brief was *use the product, find what is broken, fix it*, so the
report opens with a count and every number below was taken after the last change.

---

## 1. Defects found by using the product

**Eleven**, of which **seven could not have been found by any test in this repository** — six
because no test exercised the path, and one (F128) because the test that did had already worked
around the defect. Ten are in the table; the eleventh, F129, is noted under it.

| # | Defect | Found by | Could a test have caught it? |
|---|---|---|---|
| **F134** | **A flapping link goes invisible, permanently.** The alternation learner registered the inverse of the shipped `linkDown → linkUp` seed, so every subsequent `linkDown` was dispatched as a *clear* and no alarm was raised again. Durable: both directions were written to `edge`. | Driving a clear/re-raise cycle through the engine while hunting the snowball | **No.** No test alternated starting from the clear class. |
| **F128** | **The replay tool collapsed four devices into one**, on every machine, silently. Eight of the corpus's twenty-five source addresses are TEST-NET-3 and unbindable. | `make replay SCENARIO=dual_incident`, then reading `ne` | **No** — and worse: `tests/test_operation.py` carried a private rewrite that avoided the fallback, so the one test driving that scenario over a socket could not see it. |
| **F76** | **Two unrelated incidents merged into one.** Open since v0.15.0, pinned by a test asserting the wrong answer on purpose. | Replaying `dual_incident` against a live appliance once F128 let four devices through | Partly — the offline harness scored it, but the aggregate absorbed it and the live reproduction was measuring F128. |
| **F133** | **A count chart printed its last bucket and called it the reading.** The Overview said `resolved 1` beside three resolved columns. | Reading the Overview in Chromium beside `GET /api/situations` | **No.** Every chart test asserted geometry; none compared the header against the data. |
| **F130** | **`testbed/Dockerfile.ne` cannot be built** — the root `.dockerignore` excluded all three of its COPY sources. | Parsing the ignore file against the Dockerfile | **No.** `docker compose config` never reads `.dockerignore`. |
| **F135** | **`shadow_opinion.same_oid_root` recorded a hardcoded `0`** for nine releases — not empty, which would have been honest, but wrong. | Reading the column while implementing the F76 fix | **No.** Nothing compared the column against the pairs. |
| **F136** | **One baseline file was both an immutable record and the file `make eval-baseline` overwrites.** | Being the first release to re-cut a baseline since the target was added | **No** — unreachable until a release needed it. |
| **F137** | **The README advertised four roles; the appliance has three.** No `operator` role exists anywhere in `src/`. | Trying to create the accounts for a three-role browser pass | **No.** Nothing compared the prose against `ROLE_RANK`. |
| **F131** | `control.py cut` printed a hardcoded `http://127.0.0.1:8080/` whatever port the lab was on. | Starting the lab with 8080 held | Yes in principle; nothing did. |
| **F132** | The lab refused to start beside a stranger on its port and that was the end of it — every later command tested nothing. | Reproducing the maintainer's afternoon | Yes in principle; nothing did. |

Plus **F129** (the replay tool's default port disagreed with the appliance's), found the same way.

---

## 2. What was deleted, one sentence each

| Deleted | What it was protecting, and why that no longer applies |
|---|---|
| **`TRAP_PATH_HASHES`, `TRAP_PATH_BODY_HASHES`, their two tests and the shared import-stripping helper** (173 lines, `tests/test_architecture.py`) | They kept releases with no business in the ingest path from editing it; the v0.18.0 brief withdrew byte-pinning by name, and a hash could not tell a comment fix from a blocking `open()` — the remedy for both was "recompute the hash". **Replaced** by a guard that reads `datagram_received`'s AST and fails on an `await`, a lock or an I/O call, with a control proving it can fail. |
| **`test_score_link_body_is_unchanged_by_the_capture_change`** (`tests/test_correlate.py`) | It hashed the *source text* of `Correlator.score_link` and `features` to police one sentence of v0.8.0's build prompt — ten releases past, and `features` legitimately changes here. **Replaced** by a behavioural parity test that checks the number rather than the text. |
| **`test_the_two_incidents_are_merged_into_one_situation_and_that_is_a_defect`** (`tests/test_operation.py`) | It pinned F76's failure so the behaviour was visible rather than hidden in an aggregate; its own message said to replace it with the purity assertion once the correlator was repaired. It is repaired, and that assertion is now there. |

Nothing else was removed. `_body` and its control were **kept** when the byte pins went, because
the module-size guard still needs the same import strip for a live reason.

---

## 3. Is the architecture going the right way?

**Yes, and the measurement that settles it is v0.17.0's own, repeated on this tree:**

```
engine/ domains: correlate, dataset, evaluation, model, operate, report
cross-domain edges: 15 distinct
2-cycles: [('dataset', 'model')]
pure sinks: ['correlate']          imported by nobody: ['operate', 'report']
store/: 26 modules, 0 imports of engine/ or api/
```

Identical to v0.17.0 on every figure: six domains, **15** cross-domain edges, one 2-cycle
(`dataset ↔ model`), `correlate` a pure sink, `store/` with zero upward imports. This release
added three modules and moved code between two, and the domain graph did not move — which is what
a real structure does and a drawer does not.

Two modules were split, each by the guard that has chosen every seam in this project, and each on
a **subject** rather than a slice:

* `engine/correlate/learn.py` → `alternation.py`. The affinity matrices answer *"how related are
  these two things?"*; the two alternation learners answer *"which class turns this one off?"*
* `ui/app/charts.js` → `compare.js`. `Series` compares a thing to its own past; `Bars` and `Map`
  compare things to each other.

Reproduce: the script is in §7.

---

## 4. What an operator can now see about the models that they could not see before

Everything, because the answer before was **nothing**. The appliance could report on a
*challenger*, on the feedback corpus and on the promotion gate, and could say nothing whatever
about the scorer that was running — no per-decision record, no score distribution, no count of
links made and refused, no drift signal.

`GET /api/correlation` and the new **Correlator** screen (Operations group, `viewer+`) answer
*"is the correlator doing a good job right now?"* without an offline report:

* **what is running** — scorer, contract, threshold, window, candidate cap, and whether it has
  degraded to the fail-safe;
* **what it is deciding** — activations, candidate pairs evaluated, linked, refused, accept rate;
* **how sure it is** — the distribution of scores with the threshold's bucket marked, and how
  many decisions land within 0.05 of it (a decision that would flip on a small move in either
  affinity);
* **what is carrying the links** — which of the three terms contributed most;
* **what it did to the grouping** — merges per activation, storms, and the subtree refusals.

Every figure appears **twice**: lifetime, and over the last 500 activations. One number cannot
answer *"is this normal?"*; the difference between two can, with no stored history to keep in step.

**It earned its place on the first run.** Four replayed scenarios, 530 activations, 47 950 pairs:
learned entity affinity carried **518 of 520** accepted links. That is F58's arithmetic, visible
in the product for the first time.

**It is not evidence.** Nothing is stored, no promotion path reads it, the screen offers no
control that records an opinion, and the state dies with the process — which is also why the
caption can honestly say *"since this appliance started"*.

---

## 5. What is still broken, and why it was not fixed

| Still open | Why not here |
|---|---|
| **F58 / F61 — `MIN_EDGE_N` is cleared by six ordinary alarms** | This release narrows where that arithmetic is *believed* (the subtree gate) rather than fixing the arithmetic. Changing what `MIN_EDGE_N` counts moves every affinity in the appliance and needs its own evidence run. The audit added a measurement the next release needs: **affinity mass is driven by burst density, not recurrence** — thirty recurrences of one pair 600 s apart give `entity_affinity 0.0000`, sixteen alarms in five seconds give 0.833. |
| **The corpus has no cross-subtree incident** | The gate in §"F76" is measured to cost nothing on all ten scenarios, and **the corpus contains no scenario that could show its cost**. That gap is real, is recorded in F76's disposition and in `docs/plans/releases.md`, and closing it means writing a scenario with invented ground truth — which belongs in the release that owns the corpus (v0.17.2), not in one bolted on at the end of an audit. |
| **I.1 — correlation cannot see past 120 s** | Confirmed and **left as a written plan**, which the brief permits. `docs/plans/releases.md` §"What v0.18.0 measured about the 120-second window" gives three candidate mechanisms, the measurement that chooses between them, and the reason the choice depends on F58 being resolved first. Widening `WINDOW_S` is not the fix: at a month the window evicts on `MAX_WINDOW_ALARMS` rather than on time, so the result is arbitrary memory, not longer memory. |
| **`device` duplicates `ne`** | Measured on a live appliance after 549 traps: **8 device rows, 8 ne rows, `device.id == ne.id` for 8 of 8** — the property F105 flagged still holds, still with two independent sequences and no foreign key, so it still holds *by accident*. Migration `0003`'s own comment says `device_id` is *"retained and kept in sync for one version"* and it has been fifteen. **Not fixed here**: removing it means a migration touching `alarm`, `link`, `dataset_pair` and the `device`-kind edges, which changes the shape of the feedback dataset and the graph — a structural release's work, not an audit's, and doing it badly at the end of one is how a correlator loses its topology. `device.vendor`/`ne.vendor` are `0` non-null of 8, as F105 recorded; that finding is already closed by deleting the *renders*, and the columns stay unwritten on purpose. |
| **F56** — a malformed corpus file hangs the harness | Untouched. Offline tooling; the blast radius is a person's afternoon. |
| **F116** — `unitText` prints `1 alarms` | Untouched, and still visible. Its own disposition says the one-line fix moves every chart in the console, so it belongs in a commit with the pins that move with it. |
| **`docker build` was not executed** | This environment has **no Docker daemon** (`/var/run/docker.sock` absent) — the same constraint v0.17.0 recorded for a different reason. F130's fix is therefore verified by a guard that needs no daemon, which is deliberate: a guard that needs one is a guard that skips, and skipping is how the unbuildable image shipped. |
| **The judge still returns `INSUFFICIENT_EVIDENCE`** | Unchanged and correctly so: the floor is 50 asserting bags, the corpus ceiling is 41, and softening a registered floor because the data is short is the one move the evidence rules forbid. This release measures the **champion** instead, which needs no promotion. |

---

## 6. What the live pass did not cover

Driven in Chromium (headed binary, headless) against a **real appliance** on real replayed
traffic — `dual_incident`, `fiber_cut`, `olt_storm`, `flapping_noise`, 549 traps — at **390 / 820
/ 1440 px**, as **viewer, editor and admin**: all eighteen views plus a situation permalink, per
role, zero page errors and zero page-level horizontal overflow.

**Not covered:**

* **No real browser other than Chromium.** No Firefox, no Safari, no mobile Safari.
* **No touch input.** Tap targets were measured geometrically, not tapped.
* **No screen reader.** ARIA labels are asserted in the DOM harness; none was heard.
* **No `docker compose up`** — no daemon, as above. The lab was driven through
  `testbed/run_local.py`, repeatedly, including the port-conflict path.
* **No TLS.** Every drive was plain HTTP on loopback.
* **The scoped-principal view** was not driven in a browser; visibility scoping is covered by the
  API suite only.
* **The Correlator screen's drift band** was never seen in its `warn` state on live traffic — the
  recent and lifetime accept rates stayed within 5 % of each other for the whole pass. The branch
  is exercised by unit-level data, not by a drive.
* **No sustained load through the console.** `make loadtest` and `make burst` were not run in this
  session; the perf assertions are the suite's.

---

## 7. Reproduction commands

Run from the repository root with the virtualenv active
(`python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"`).

**F134 — the flapping link goes invisible** (three lines, no appliance):

```sh
python -c "
from netcorenoc.engine.correlate.learn import ClearPairLearner
lr = ClearPairLearner(); lr.register(2, 3)            # the shipped linkDown->linkUp seed
for c in (3, 2, 3, 2, 3, 2): lr.observe(7, 'p1', c)   # a link that flaps, first seen recovering
print('linkDown is a clear class:', lr.clear_to_raise.get(2))"   # was 3, must be None
```

End to end: replay eight traps alternating `1.3.6.1.6.3.1.1.5.4` / `…5.3` from one source,
**starting with the up**, and read `SELECT COUNT(*) FROM alarm WHERE status='active'`. Before: 0
with the link down. After: 1.

**F76 — two incidents stay separate:**

```sh
NETCORENOC_TRAP_PORT=1162 python -m netcorenoc.main &
NETCORENOC_TRAP_PORT=1162 python tools/trap_replay.py eval/corpus/dual_incident.json
sqlite3 netcorenoc.db "SELECT s.id, COUNT(sa.alarm_id), GROUP_CONCAT(DISTINCT d.ip)
  FROM situation s LEFT JOIN situation_alarm sa ON sa.situation_id=s.id
  LEFT JOIN alarm a ON a.id=sa.alarm_id LEFT JOIN device d ON d.id=a.device_id GROUP BY s.id"
```

Before: one situation, 16 alarms, four devices. After: two situations of 8, split on the vendor
boundary. The offline half is `python eval/harness.py`.

**F128 — the replay tool's device count:** the same replay prints the rewrite and
`sent 16 traps from 4 source address(es)`. Before the fix, `SELECT ip FROM ne` held one row.

**F130 — the unbuildable image:**

```sh
PYTHONPATH=tests python -c "
import pathlib, dockerignore
i = dockerignore.DockerIgnore.parse(pathlib.Path('.dockerignore').read_text())
for s in dockerignore.copy_sources(pathlib.Path('testbed/Dockerfile.ne').read_text()):
    print(i.explain(s))"
```

**F131/F132 — the lab beside a stranger:**

```sh
python -c "
import socket,time
t=socket.socket(); t.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
t.bind(('127.0.0.1',8080)); t.listen(1)
u=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); u.bind(('127.0.0.1',1162)); time.sleep(300)" &
make lab &                       # picks free ports and says so
sleep 12 && make lab-status      # names the lab's real URL, not 8080
make lab-cut
```

**F133 — the chart header:** open the Overview after any traffic. *Situations, by when they were
created* must read `TOTAL …`, and its `resolved` figure must equal
`GET /api/situations?status=resolved`.

**F137 — the role list:** `python -m pytest tests/test_documentation.py -k roles`.

**The snowball (refuted):** the three probes are described in §"What was measured and left alone"
of `CHANGELOG.md`; the structural one is an anchor alarm that never clears plus a cycler on the
same element, with rounds 480 s apart. The biggest situation grows from 2 to 3 members on the
first round and then stops, over eleven rounds and three simulated hours.

**The architecture measurement (§3):** the script is reproduced in the v0.18.0 CHANGELOG entry's
*"What was measured and left alone"*; it walks `src/netcorenoc/engine/*/` and counts
`ast.ImportFrom` edges between domains.

**The observability surface:**

```sh
curl -s --cookie-jar /tmp/c -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}' http://127.0.0.1:8080/api/login
curl -s --cookie /tmp/c http://127.0.0.1:8080/api/correlation | python -m json.tool
```

---

## 8. Gates, all run after the last change

See §"Verification" below for the figures. Every one was taken on the delivered tree.

## 9. On tags

**This repository contains no tags at all** — `git tag` prints nothing, on the tree as delivered
to me and on this one. Not "only its own tag": zero. F74 records this ("no release tag reachable
from this repository except v0.12.0") and it has since become none. I have not created any,
because inventing tags for releases I did not build would be fabricating history. The release is
identified by `CHANGELOG.md`, by `__version__`, and by the four-way agreement
`tools/release_check.py` verifies.
