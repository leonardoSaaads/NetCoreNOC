# Build report — v0.10.1

**A guard that was not merely untested but wrong, a conclusion about a statistic that ran backwards,
and a reported number that did not reproduce.** All three fixed, and not one line of the ratified
plan those numbers were measured against was moved.

---

## 1. F50 — the cycle rule resolved to the wrong minimum

`incidents.resolve`'s docstring:

> **A cycle resolves to the minimum id in the cycle** […] two bags entering the same cycle at
> different points would otherwise be assigned different incidents, so a corrupt chain would
> silently *inflate* the incident count — the exact error the transitive resolution exists to
> remove, re-entering through the failure path.

The code was `return Resolution(min(seen), cycle=True)`, and **`seen` is the whole walk, including
the tail that led into the cycle.**

```
merge map: {1: 7, 7: 8, 8: 7}      the cycle is {7, 8}; situation 1 is a tail into it
incident_of: {1: 1, 7: 7, 8: 7}
distinct incidents: 2               <- there is one
```

**The line that claimed to prevent the failure produced it.**

`IncidentMap.incidents` is the `n` every clustered estimate in this project is expressed in — the
folds, the bootstrap, the power condition, the sufficiency floors and the sealed holdout's
membership all group by it.

**Rated low for exploitability and high for correctness of a measurement**, and both are meant.
`engine.py:466` does `sid = min(sids)`, so `merged_into` always points downward and no cycle can be
written through the live path today: nothing an operator or an API client can do produces one, and
no deployment needs changing. But this is the guard for the case where that property fails — a
hand-edited database, a restore from a partial backup, a future merge policy — and **the guard was
not merely untested; it was wrong**. That is the argument that made the perimeter's fail-open branch
worth a test in v0.9.2.

**Why the suite was green on it.** `test_every_entry_point_into_one_cycle_gets_the_same_incident`
fixes `{301: 302, 302: 303, 303: 301}`, where **every entry point is already a cycle member**, so
`min(walk) == min(cycle)` by coincidence of the fixture. It is not a bad test — v0.10.0's mutation
ledger records it killing the obvious mutant, re-verified in Gate 0 — but the tail case is outside
the instrument. **A test's fixture is part of the guard.** Fourth occurrence in this repository's
history.

Repaired by walking the cycle from the re-visited node; four tests, two of them controls, three
injections recorded red.

---

## 2. The detection threshold was pessimistic, and #142's conclusion ran backwards

v0.10.0 measured a disagreement between the shipped closed form and the ratified plan's §3.1 table,
carried it to `SECURITY-REVIEW-0.10.0.md` §3.2 as *"the plan's small-`n` figures are optimistic"*,
and recorded it as `DECISIONS #142`.

**The plan was never optimistic. The formula was pessimistic.**

```
delta = (z_α/2 + z_power) · sqrt(2 · p(1−p) / n)
```

The `2 ·` gives **both** arms the base rate's variance. The second arm sits at `p + delta`, and when
the detectable delta is large — exactly the small-`n` regime — its variance is far smaller. At
`n = 37`, `p = 0.70`, the two arms are **0.210 and 0.058**. Assuming the larger for both demands a
bigger delta than reality, and the error grows as `n` shrinks:

|    n | naive | variance-correct | independent MC | plan |
|-----:|------:|-----------------:|---------------:|-----:|
|   37 | 0.298 |        **0.238** |          0.240 | 0.25 |
|  120 | 0.166 |        **0.149** |          0.150 | 0.16 |
|  300 | 0.105 |        **0.099** |          0.099 | 0.10 |

**The plan reproduces at every size where a threshold exists.**

**Why #142's corroboration failed.** Its Monte-Carlo returned 0.33 at `n = 37` — *higher* than the
naive form, where the truth is *lower*. **Two methods that share an assumption are not two methods.**
Its agreement is what made the wrong conclusion look confirmed.

**What was and was not touched.** `PREREGISTRATION-0.10.0.md` is **unmodified**, both hash guards
green — §8 forbids editing it, and the table is a reported quantity and a verdict trigger, never a
floor. `DECISIONS #142` is **unmodified**; **#154** is appended and supersedes it.
`SECURITY-REVIEW-0.10.0.md` §3.2 carries a dated correction block **above** the section it corrects,
with nothing below it deleted.

**What does not change**: v0.10.0's verdict. A *lower* threshold makes `observed > detectable`
**easier** to satisfy, so `INSUFFICIENT_EVIDENCE` was reached on a floor failure and would have been
reached anyway. What is repaired is a trigger that was **more conservative than intended** — a future
release with a real corpus would have been told it could not resolve a difference it actually could.

**A second finding, recorded and not repaired.** §3.1 registers **0.42** at `n = 12`. At `p = 0.70`
that puts the second arm at **1.12**, which is not a proportion. The largest difference that exists
at this base rate is 0.30 and its simulated power is **0.519** — so at 12 incidents there is no
detectable difference *at all*. That is a stronger version of the plan's own conclusion, and it means
the registered figure is not a threshold that was too optimistic but one that does not exist.

**And the disagreement is now a test rather than a paragraph.** `tests/test_shadow_cv_power.py`
searches for the threshold by simulation — exact inverse-CDF binomial draws, a pooled z-test, a
fixed absolute grid, its own generator — sharing no arithmetic with the closed form, with a
tolerance derived from three named terms.

---

## 3. The coverage figure, in two parts

**Part one is closed.** Every count reproduced exactly and the percentage did not, because
**`96.20 %` was never a coverage.py output.** It is the four printed columns computed by hand, and
`BrPart` (123 partially-covered *statements*) is not `missing_branches` (143 missing *arcs*):

```
coverage.py's own arithmetic : (6480 + (1462 - 143)) / 8128 = 95.9523%
the v0.10.0 hand-arithmetic  : (6480 + (1462 - 123)) / 8128 = 96.1983%   -> "96.20"
```

`make coverage` is pinned, with the rule in a comment above it: **quote the tool's own
`Total coverage:` line and never recompute it from the columns.** v0.9.2 was re-measured
like-for-like at **96.19 %** — reproducing to the digit — rather than compared against `96.22 %`,
which appears in this repository only as a sentence in a brief.

**Part two was found by measuring twice, and it is not closed.**

```
$ make coverage      Total coverage: 96.21%
$ make coverage      Total coverage: 96.10%     # same command, same tree
```

`receiver.py` carries the **entire** 0.11-point band and nothing else in the suite drifts:
`tests/test_receiver.py` fuzzes the BER trap decoder with `@given(st.binary(…))` and Hypothesis
generates different examples every run.

**Deliberately not derandomised** (DECISIONS #159) — that trades a real fuzzer against a trap decoder
for a tidy number, which is A3's own instruction one register up. The consequence is stated instead:
every coverage figure in this project's history is **one sample from a distribution**, the
v0.9.2 → v0.10.0 movement of 0.24 points is about two bands (*probably real, not established*), and
a release gate of the form *"coverage at or above X"* is **not well-defined on this suite** and has
not been for as long as the fuzzing has existed.

---

## 4. The consolidations

**Four consumers, one implementation.** `agreement.py` and `bias.py` were the last two computing
identity with a one-hop `COALESCE`; both now read merge edges and resolve through
`incidents.resolve_all`.

**Both frozen reports are byte-identical — and could not have been otherwise.** Measured:

```
bias fixture:      situations=4    labelled=4   unlabelled=0   merge edges=0
agreement fixture: situations=12   labelled=12  unlabelled=0   merge edges=0
```

Neither corpus contains a single merge edge, so a one-hop reading, a transitive reading and an
edge-less reading return identical numbers. **The project's strongest gate cannot discriminate
between them**, and the mutation ledger demonstrated it: restoring either `COALESCE` leaves both
reports byte-identical and **only the structural guard goes red**.

**Which is the whole argument for B2**: an `ast` guard asserting no module computes identity in SQL
at all, so a fifth consumer cannot be written rather than having to be found. `ast` and not `grep`,
because six modules name the expression in prose right now. **With its own vacuity check** — and
that pair is demonstrated: with the extractor broken and a real offender planted, the guard **passes
and reports the package clean**; only the vacuity check notices.

**F49 got both repairs**, because they fail independently. The pinned expression is tied to
`0011_evidence_boundary.sql`, and `test_upgrade.py`'s v0.9.1 fixture now writes the client's reported
bag containing the ghost id. Deleting the predicate from the migration fails **three** tests, against
v0.10.0's measured **1093 passing**.

`agreement.py` reached 402 of a 400-line ceiling and was **split, never exempted** —
`agreement_bags.py` at 178, `agreement.py` at 265, `DEBT_ALLOWLIST` still empty, no ceiling moved.

---

## 5. The mutation ledger found a defect in this release's own repair

Thirty injections. The result worth reading is **A1**: passing the walk's start to `_cycle_members`
instead of the re-visited node — a one-token edit — **did not fail. It hung.** The loop was
`while node != entry`, which terminates only because the caller passes a node that is on the cycle.

`incidents.py` is the module whose entire subject is merge chains the schema does not forbid and no
code prevents. **A walk whose only stopping condition is an invariant is the wrong shape there.**
Bounded by `MAX_CHAIN_DEPTH`, returning what it collected — which converts a hang into a wrong
number, and a wrong number is visible where a hang is a support ticket (DECISIONS #158).

**Three more survivors were the same weakness three times.** A8, A9 and A10 survived because the
frozen corpora have no unlabelled situations and no merge edges — and A10 not even by corpus
accident: `labelled_bags` returns one row per situation, so `min` and `max` over the seal's ordering
agree *by construction of the query* and differ only across **merged** situations. All three closed
with purpose-built fixtures beside the guard they complete, **never by editing a byte-frozen corpus
to buy a mutation kill**.

Two survivors are equivalent mutants, demonstrated equivalent **by measurement** rather than
asserted. One survivor was a bad injection of mine, recorded as such.

**Third release running in which the mutation ledger found something the rest of the suite was blind
to.**

---

## 6. What this release did not do

| | |
|---|---|
| migrations | **zero**; `schema_sha`, `user_version`, integrity and foreign keys identical to v0.10.0 |
| routes, capabilities, audit actions | **none added** |
| runtime dependencies | **5** |
| `PREREGISTRATION-0.10.0.md` | **unmodified**; both hash guards green |
| `DECISIONS #142` | **unmodified**; superseded by #154 |
| the seal | query count **0**; AST guard green; reconstruction refused |
| `engine.py`, `correlate.py`, `receiver.py`, `learn.py`, `scoring.py`, `challenger.py`, `capture.py`'s write path, `rbac/`, `shaping/` | **untouched** |
| `make eval` | byte-identical, unchanged since v0.7.0 |
| intentional behaviour changes | **one**, declared and counted — §7 |

## 7. The one behaviour change, declared

The shadow report's printed `minimum detectable difference` moves **0.182 → 0.161** at its fixture's
`n = 100`. Measured against a real v0.10.0 tree, the diff is **one line** of a 10 655-byte report;
the verdict, its four trigger lines, every floor and every other number are byte-identical.

**Everything else is zero.** 50 routes with identical handler hashes, 39 live HTTP responses
identical across three roles, both byte-frozen reports identical, the schema identical, the eval hash
identical.

## 8. Numbers

| | v0.10.0 | v0.10.1 |
|---|---|---|
| tests | 1174 | **1196** |
| `mypy --strict` | clean, 159 files | **clean, 161 files** |
| coverage | 95.95 % *(reported as 96.20 %)* | **96.21 % / 96.10 %** — the band is §3 |
| `src/` | 90 files, 17 418 lines | **91 files, 17 586 lines** |
| ADRs | #153 | **#159** |
| findings | F49 | **F50** |
| migrations | `0001`–`0012` | **`0001`–`0012`** |
| runtime dependencies | 5 | **5** |
| `eval` hash | `c2e8a0ce…8b9b6f26` | **identical** |

## 9. What v0.11.0 inherits

* **Three documents that specify and implement nothing** — `DATA-LINEAGE.md`,
  `OBSERVABILITY-DRAFT.md`, `STORAGE-PORTABILITY-DRAFT.md` — so the next three efforts can be scoped
  rather than discovered.
* **The open item that matters most**: incident identity is **not stable in time**. Folds are
  deterministic in the incident ids, identity depends on `merged_into`, merges change it, and no
  snapshot of the merge graph is retained anywhere. The seal is materialised over incident ids, so
  drift means the sealed set and the estimator's exclusions come from two maps taken at two different
  times. **v0.10.1 closed the spatial version of that hazard. The temporal version is open, and
  v0.11.0 is the release that cites an evaluation.** Three candidates named, none chosen.
* **Three ROADMAP lines** from the review: the duplicated correlated subquery in
  `reconciliation_drift`, repeated coverage measurement, and a corpus with a real merge chain.
