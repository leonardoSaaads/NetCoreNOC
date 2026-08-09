# Build report — v0.9.1

**The operator can say *which* members do not belong — and the project audited whether its own tests
would notice if they were wrong.**

---

## 1. The three numbers this release is about

### 1.1 Asserted negative pairs: from **structurally zero** to a quantity that exists

Before this release, **no corpus that has ever existed held a single asserted negative pair.** Gate 0
§1 shows why, by query: the complete recorded evidence of a `split` was a verdict and an ordered
member list, and there was no column, table or row anywhere in the schema in which one member was
asserted to be separate from another.

Meanwhile `learn.penalize()` responded to that assertion by halving **every** matrix cell the bag
spanned. On a nine-member bag: **36 member pairs driving 72 cells, from an assertion that named none
of them.**

After this release, one gesture on that same bag — marking two members — asserts **fourteen negative
pairs**, and leaves twenty-two **unasserted**:

```
m·r  +  r(r−1)/2  +  m(m−1)/2   =   n(n−1)/2
14   +     21     +      1      =      36
```

The identity closes exactly, and it is asserted by a test rather than promised in a comment. That is
the release's whole product: **the operator's assertion, recorded exactly as made — never more, never
less.**

### 1.2 The channel split: the mechanism ships, the volume does not

`acquisition_channel` has read `'organic'` on every row since v0.8.0 built the column for this day.
v0.9.1 defines `'close'` and reports every existing figure per channel, in the bias report and in
every conditional cut of the agreement report.

**And the shipped UI writes `'organic'` on every row**, because DECISIONS #130 defers the gesture:
two more buttons is not a panel, but it would put five near-identical click targets in one row on the
one path where a mis-click is a *silently wrong label* — the failure v0.7.5 exists to prevent.

> **So this release raises the informativeness of a label and not the rate.** That is the honest
> summary, it is stated in the CHANGELOG, in Gate 4 and in the v0.10.0 specification, and it is not
> what the release set out to do.

### 1.3 The test audit's headline

**Thirty-one seeded defects, nineteen caught, twelve missed** — and it says at the top, in a block
quote, before any result, that it is **a sample and not a proof**.

The three that mattered most:

* **`feedback.write` and `situation.close` could each be moved from `editor` to `viewer` with all
  958 tests green** — granting every read-only account the ability to write labels and close
  situations. Everything downstream of `PERMISSIONS` is derived from it, and the generated
  authorization matrix regenerates its own expectation from the resolver, so it is self-consistent
  under *any* table. The only role assignment asserted anywhere was `config.read`.
* **`SECURITY-REVIEW-0.9.0.md` predicted the skew comparison's aliasing convention could be
  inverted silently. The audit inverted it, and nothing failed.** `shadow_skew_rows` selects the
  `served_*` columns and `_skew` never reads them.
* **The authorization perimeter fails *open* on an undeclared route, untested.** The declaration
  gate makes that state unreachable in a built application — so this is the second layer of a
  two-layer defence, and only the first layer is guarded.

Two honest qualifications on the nineteen: **three were caught by an import-time assertion rather
than by any test**, and **six were caught by a frozen report fixture** rather than by a test of the
thing that broke. The three report gates are empirically the broadest net in the repository, which
is a strength and a warning.

Three gaps closed, each because this release's themes touch it, and **each verified to fail under
its seed** before being kept. Everything else is a `ROADMAP.md` line.

---

## 2. Was the corpus moved? No, and the reason matters

Re-measured on this tree, unchanged from v0.9.0:

| Floor | Required | Observed | |
|---|---:|---:|---|
| `split` bags | 50 | **13** | ✗ |
| mixed bags | 20 | **5** | ✗ |
| *(observation, no floor)* `split ∧ mixed` | — | **1** | |

**No floor was moved**, and the v0.9.0 pre-registration is unedited: `bb5bff85…2cbaef`.

### The finding that bounds every claim in this document

Gate 0 §2.1 counted what those thirteen `split` bags actually contain:

| feedback id | members | member pairs after the cap |
|---:|---:|---:|
| nine of them | 1 | 0 |
| two of them | 0 | 0 |
| one | 240 | 190, spanning **1** class cell |
| one | 501 | 190, spanning **2** class cells |

> **Not one of the thirteen would yield a single asserted negative pair.** Eleven have no pair to
> assert anything about; the other two are storms nobody would mark member by member.

That is a property of the corpus's **mechanical labelling rule**, not evidence about operators — but
it means the exclusion set **cannot be demonstrated on any corpus this repository can construct**.
The semantics are proved on a purpose-built fixture, and **no projection of how fast asserted
negatives will accumulate is supportable**. This release publishes none.

---

## 3. Will this actually work?

The question §13 of the build prompt asks to be answered rather than dressed up.

**Informativeness: yes, and by a large factor where it applies.** A partial split on a nine-member
bag yields fourteen asserted negatives where a plain split yielded none. There is no arithmetic under
which that is not an improvement in the evidence per label, and it is the first negative evidence the
system has ever held.

**Rate: no, not in this release.** The close channel ships as contract and reporting only. The
labelling rate is unchanged, and Gate 0 §3 could not even *measure* the quantity the change was aimed
at, because the only corpus available labels every situation by construction and none of its closes
came from an operator.

**The floors: almost certainly not, and not soon.** Two independent reasons, and the second is worse
than the first:

1. **The exclusion is optional and the plain path is one click.** An operator in a hurry presses
   Split. Nothing in this release makes that harder — deliberately — so the fraction of splits that
   carry an exclusion is an operator-behaviour question nobody has data on.
2. **The floors are counted in *bags*, and this release adds no bags.** `split` bags ≥ 50 and mixed
   bags ≥ 20 are population counts. A partial split is still **one** `split` bag. Making each bag
   more informative does not move a threshold denominated in bags, and it was never going to.

**So the honest projection is: this release changes what a label is worth and does not change how
many there are, and the registered floors are unmoved.** What it does buy is real and is worth the
diff: when the corpus does reach a size worth modelling, the `split` bags in it will support a
negative class that is *observed* rather than *derived* — which is the choice v0.9.0 had to make
between policy A (fabricate) and policy B (discard), and neither was good.

**The one thing that would move the floors is the thing this release deliberately did not do**: raise
the acquisition rate. The endpoint for it now exists, tested and reported per channel. The gesture is
a UI release's to make, and DECISIONS #130 says why it should not have been squeezed into this one.

---

## 4. What shipped

| | |
|---|---|
| Migrations | **one** — `0010`, additive, seeds nothing, **backfills nothing** |
| Intentional behaviour changes | **two** — the endpoint accepts an exclusion; `penalize` uses it |
| New runtime dependencies | **0** (five, unchanged for nine releases) |
| New capabilities / audit actions / routes / served paths | **0** |
| UI files | unchanged; one checkbox cell in the table already on screen |
| Tests | 923 → **960** |
| `make eval` | `c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26` — **byte-identical** |
| Pre-registration | `bb5bff851588837aa07f21c54b5301f7ada5fec3f8017a5ca4e9d7f7da2cbaef` — **unedited** |
| ADRs | #123 – #130 |
| Findings | none issued; the next is still **F46** |

## 5. The decisions worth reading

* **#123** chooses the exclusion set over pairwise marking (dominated at every bag size ≥ 3) and a
  full partition (asks a question the operator usually cannot answer, and a guess recorded as an
  assertion is worse than no assertion).
* **#124** fixes the semantics at `marked × rest` **and nothing else**, with the identity
  `m·r + r(r−1)/2 + m(m−1)/2 = n(n−1)/2` as the check, and rules that an exclusion on a `confirm` is
  a contradiction rather than evidence.
* **#125** has `penalize` act on the assertion, with a **provable** subset bound rather than a
  measured one.
* **#126** makes a close-recorded verdict a second acquisition channel, and closes a capability gap
  a stored governance policy could have opened.
* **#127 / #130** ship the exclusion gesture and defer the close gesture — the second would have put
  five near-identical click targets in one row on the one path where a mis-click is a *silently
  wrong label*.
* **#128 / #129** are the structural cost: the label row's children moved to `store/feedback.py`,
  and a `LabelContext` bundle plus `server_bag` moved out of `engine.py`, which had **zero
  headroom** against its 580-line cohesion ceiling.

## 6. What I would tell the next engineer

**The corpus is still the binding constraint, and this release did not loosen it.** It made each
`split` bag capable of carrying real evidence. It did not make there be more of them, and the floors
are counted in bags.

**Two facts that will save a day each.** The `split` bags in the constructible corpus are singletons
and storms, so any feature that depends on what an operator would *choose* cannot be validated
there. And `engine.py`'s cohesion ceiling equals its exact size, so every release touching the
ingest path pays a refactoring tax before it can add a line — twice now that tax has improved the
code, and it will not always.

**The audit is worth re-running.** It cost an afternoon of wall-clock and found a fail-open branch in
the authorization perimeter, an unpinned capability table, and confirmed a defect a previous review
had only predicted. v0.10.0 adds an evaluator — a release whose guards matter more than most.
