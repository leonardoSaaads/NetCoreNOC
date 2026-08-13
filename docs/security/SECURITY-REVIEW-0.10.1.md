# Security review — v0.10.1

Continues from **F49**. This release issues **F50**. The next finding this project issues, in any
release, is **F51**.

v0.10.1 is a patch release inside the v0.10.0 line. It adds **no route, no capability, no audit
action, no dependency, no migration and no schema change**; `make eval` is byte-identical; the
correlation, capture and ingest paths are untouched; and the full HTTP surface answers identically to
v0.10.0 across 39 measured probes. **One reported quantity changes**, declared and counted: the
shadow report's printed minimum detectable difference.

The security-relevant content is of one kind: **a guard that was not merely untested but wrong, in
the module every clustered estimate in this project depends on.**

---

## 1. F50 — the cycle rule in `incidents.py` resolves to the wrong minimum

**Severity: low (exploitability). High (correctness of a measurement). Confidentiality: nil.
Availability: nil. Privilege escalation: no. Production change required: none.**

Both halves of that rating are meant, and §1.5 argues each.

### 1.1 The defect

`incidents.resolve` follows `situation.merged_into` to a fixed point. On detecting a cycle it
returned:

```python
        if nxt in seen:
            return Resolution(min(seen), cycle=True)
```

`seen` is the **whole walk**, which includes the *tail* that led into the cycle. Its own docstring
states the intended behaviour and the reason for it:

> **A cycle resolves to the minimum id in the cycle**, not to wherever the walk happened to stop.
> […] two bags entering the same cycle at different points would otherwise be assigned different
> incidents, so a corrupt chain would silently *inflate* the incident count — the exact error the
> transitive resolution exists to remove, re-entering through the failure path.

**The code produces the failure the docstring describes.** When a tail id is smaller than every
member of the cycle, `min(seen)` *is* that tail id, so the tail resolves to itself while the cycle
members resolve to the cycle minimum.

### 1.2 Reproduced, with controls

```
DEFECT — a tail into a cycle, tail id BELOW the cycle minimum
  merge map:       {1: 7, 7: 8, 8: 7}
  incident_of:     {1: 1, 7: 7, 8: 7}
  distinct:        2                      <- there is one incident

CONTROL — every entry point IS a cycle member (the existing test's fixture)
  merge map:       {301: 302, 302: 303, 303: 301}
  incident_of:     {301: 301, 302: 301, 303: 301}
  distinct:        1                      correct

DEFECT — two tails into one cycle disagree with each other
  merge map:       {1: 7, 2: 7, 7: 8, 8: 7}
  distinct:        3                      <- there is one
```

### 1.3 Why the existing test did not see it

`test_every_entry_point_into_one_cycle_gets_the_same_incident` fixes `{301: 302, 302: 303,
303: 301}`, in which **every entry point is already a cycle member**, so `min(walk) == min(cycle)`
by coincidence of the fixture rather than by the code. It is not a bad test — v0.10.0's mutation
ledger records it killing the obvious mutant (`min(seen)` → `current`), re-verified here — but the
tail case is outside the instrument.

**A test's fixture is part of the guard, and a guard is only as wide as the inputs it was watched to
fail on.** This is the fourth time in this repository's history that a real test, killing a real
mutation, has been blind to the case that mattered.

### 1.4 The repair

The minimum is taken over the **cycle members**, collected by walking from the re-visited node back
to itself. Four tests, two of them controls — including one that passes on the *defective* code, so
that it discriminates a `max(cycle)` repair that the primary test would accept. Three injections,
each recorded red in `../gates/v0.10.1-guard-demonstrations.md`.

**The repair produced a second defect, which the mutation ledger found by hanging** — see §3.1.

### 1.5 Severity, stated in both directions

**Exploitability: low, and it is unreachable today.** `engine.py:466` does `sid = min(sids)` and
merges into the smaller id, so `merged_into` always points downward, every chain is strictly
descending, and **no cycle can be written through the live path**. There is nothing an operator, an
API client or a scoped principal can do to produce one. No production change is required and no
deployment is affected.

**Correctness of a measurement: high.** `IncidentMap.incidents` is the `n` every clustered estimate
in this project is expressed in — the cross-validation folds, the cluster bootstrap, the power
condition, the sufficiency floors and the sealed holdout's membership all group by it. This is the
guard for the case where the descending-merge property fails: a hand-edited database, a restore from
a partial backup, or a future merge policy that does not pick the minimum. **And the guard was not
merely untested; it was wrong.**

That is the same argument that made the perimeter's fail-open branch worth a test in v0.9.2: the
entire value of a fail-safe path is in the case nobody expects to reach, and a fail-safe path that
has never been watched fail is a comment.

**Where it would have surfaced.** Nowhere loudly. A corrupt chain is flagged `cycle` and excluded
under §7.9 — but the count it is excluded *from* would already be wrong, and the seal and the
estimator would be resolving the same corpus into different incident sets with nothing going red.
That is the failure mode `PREREGISTRATION-0.10.0.md` §3.3 exists to prevent, arriving through the
error path instead of through a second implementation.

---

## 2. What this release does **not** change

| | |
|---|---|
| routes | none added, none reordered; 50, with identical handler hashes |
| capabilities | none |
| audit actions | none |
| RBAC, scoping, the perimeter | untouched |
| migrations / schema | **zero**; `schema_sha` identical to v0.10.0 |
| runtime dependencies | 5 |
| the ingest path | untouched; `engine.py` unchanged at 569 lines |
| `PREREGISTRATION-0.10.0.md` | unmodified; both hash guards green |
| the seal | query count **0**; AST isolation guard green; reconstruction refused |
| the byte-frozen reports | bias and agreement **byte-identical** |

The one declared behaviour change is the shadow report's printed detection threshold,
**0.182 → 0.161** at its fixture's `n = 100`, with the verdict, its four triggers and every other
number identical (Gate 5 §4).

---

## 3. Three honest critical notes

### 3.1 Which of this release's own guards I am least confident in

**`test_the_cycle_walk_terminates_even_when_its_precondition_is_violated`, and the bound behind it.**

The F50 repair introduced `_cycle_members`, whose loop was `while node != entry`. That terminates
**only** because the caller passes a node that is on the cycle. The mutation ledger seeded the
obvious one-token mistake — pass the walk's start instead — and the run **did not fail; it hung**. A
ten-minute harness timeout was the only thing that noticed.

I am least confident in this guard for three reasons, and they compound:

1. **It tests a private function directly**, because no input to `resolve` can reach the state. That
   is honestly the only way to test it, and it is also the shape of a test that stops being true
   when the caller changes and nobody notices.
2. **Its control does not discriminate an off-by-one.** `test_the_bound_is_never_reached_on_a_real_cycle`
   uses a cycle of exactly `MAX_CHAIN_DEPTH`, and mutation A13 — the bound firing one step early —
   **survived**. It is an equivalent mutant on every reachable input, demonstrated by measurement,
   but "equivalent" and "untested" look identical from inside the suite.
3. **The bound does not make a violated precondition correct.** It converts a hang into a wrong
   number. That is the right trade for an offline report and it is stated as such — but a reader
   skimming the test could take it for a correctness guarantee, and it is not one.

Runner-up: `test_all_four_consumers_resolve_identity_through_the_one_implementation` reads **call
names, not call semantics**. A consumer that called `resolve_all` and discarded the result satisfies
it. It is deliberately presented as one half of a pair with the expression guard rather than as a
guarantee, and mutation M6 kills both — which is luck rather than design.

### 3.2 Could B1's consolidation have moved a frozen number without anyone noticing?

**Yes, in one direction, and the mitigation is structural rather than observational. Here is the
full argument.**

*The reassuring half.* Both frozen reports are byte-identical, measured across two runs, two
processes and against a real v0.10.0 tree (Gate 5 §4). Nothing moved.

*The half that matters.* **On this corpus, nothing could have moved.** Measured:

```
bias fixture:      situations=4    labelled=4   unlabelled=0   merge edges=0
agreement fixture: situations=12   labelled=12  unlabelled=0   merge edges=0
```

**Neither frozen corpus contains a single merge edge.** So a one-hop reading, a transitive reading
and an edge-less reading all return the same numbers, and the byte-for-byte comparison — the
project's strongest gate — **cannot discriminate between them**. That is not a hypothetical: the
mutation ledger *demonstrated* it. A9 replaced the edge map with `{}` and every agreement test
passed; A8 resolved every situation instead of the labelled ones and every bias test passed; M6 and
M7 restored each one-hop `COALESCE` and both frozen reports stayed byte-identical.

So the honest answer is: **the frozen reports did not verify B1. They could not have.** What verifies
B1 is (a) the structural guard that makes the expression unwritable, which is the only thing that
went red under M6 and M7, and (b) three purpose-built fixtures added in Gate 4 with actual merge
chains in them.

**And the residual risk is real.** A number *could* move on a customer corpus with a two-hop chain —
that is the entire point of the change, and it would be a correction rather than a regression. But
this repository has no way to see it happen, and nothing in `make qa` would tell an operator that
their corpus is the first one where the two readings differ. `census.py` prints
`reduction_from_one_hop` for exactly this reason, and on every corpus this project can construct it
prints **zero**. A number that has only ever been zero is a number nobody checks.

### 3.3 The coverage figure is not reproducible, and that is a weaker claim than A3 set out to make

A3's brief was *fix the ambiguity, not the number*. The ambiguity had **two** causes and the release
only removes one of them.

The first is fixed: `96.20 %` was never a coverage.py output — it was the printed columns computed
by hand, with `BrPart` (partially-covered *statements*) used where `missing_branches` (missing
*arcs*) belongs. `make coverage` is pinned and its comment says to quote the tool's own line.

The second is not, and I found it only because Gate 5 measured twice: **96.21 % and 96.10 % on the
same tree, same command.** `receiver.py` carries the whole 0.11-point drift, because
`tests/test_receiver.py` fuzzes the BER decoder with `@given(st.binary(…))` and Hypothesis generates
different examples each run.

**I chose not to fix it** (DECISIONS #159) — derandomising trades a real fuzzer against a trap
decoder for a tidy number — but the consequence must be stated plainly rather than buried:

* every coverage figure in this repository's history, including v0.9.2's 96.19 % and v0.10.0's
  95.95 %, is **one sample from a distribution**, and none was ever reported as such;
* the v0.9.2 → v0.10.0 movement of 0.24 points is about **two bands**: probably real, **not
  established**;
* a release gate of the form *"coverage at or above X"* — which several of this project's briefs
  have used — is **not a well-defined gate** on this suite, and has not been for as long as the
  fuzzing has existed.

That last point is the one I would want a reviewer to argue with. It is a criticism of a practice
this project has followed for six releases, discovered by taking one extra measurement.

---

## 4. Findings ledger

| finding | release | status |
|---|---|---|
| F44 — the operational prune deleted human labels | v0.8.1 | closed |
| F46 — the asserted-negative count is the client's list length | v0.9.2 | closed |
| F47 — the assertion does not record whether it could have been made | v0.9.2 | closed |
| F48 — the `source='server'` predicate probed in only one of three places | v0.9.2 | closed |
| F49 — a migration's pinned SQL is not tied to the migration | v0.10.0 | **closed in v0.10.1** (both repairs; §B3) |
| **F50 — the cycle rule resolves to the minimum of the walk, not of the cycle** | **v0.10.1** | **closed in v0.10.1** |

**The next finding this project issues is F51.**

## 5. ROADMAP lines this review raises and does not resolve

* **`Store.reconciliation_drift` carries the same correlated subquery twice**, in its `SELECT` list
  and in its `WHERE`, with nothing tying the copies together — F49's shape, one statement down.
  Removing the predicate from only the reported copy is undetectable, because the `WHERE` copy still
  selects (ledger M11a). Not a defect today: both copies agree, and M11′ proves the pair is
  load-bearing.
* **Repeated coverage measurement.** §3.3. A defensible trend needs more than one sample per
  release, and this release does not invent a method for it.
* **A corpus with a real merge chain.** §3.2. Every incident-identity property in this project is
  proved on purpose-built fixtures because the corpus generator produces no multi-hop merges. The
  strongest gate the project has cannot see the change this release made.
