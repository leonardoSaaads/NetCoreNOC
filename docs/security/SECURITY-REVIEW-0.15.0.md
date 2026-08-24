# Security review — v0.15.0

**Theme: the record becomes a ledger; the documentation becomes a product's documentation.**

This document is written at **Gate 0**, before any document has been deleted, condensed or
rewritten, and before the release's own review. It exists now for one reason: it issues the two
findings the v0.14.0 QA left unissued, and one of them says that a sentence in
`SECURITY-REVIEW-0.14.0.md` scopes a defect too narrowly. **A security review is a record and is not
edited.** The correction is a new finding that supersedes the scope, exactly as ADR corrections work
under DECISIONS #102.

Findings continue from **F60**. Nothing before it is renumbered and nothing before it is edited.

Both findings below are **issued, not fixed**. F61's mechanism is v0.15.5's work; F62 is a note for
the release that next revisits the background set. Per Part VII.6, the next finding after this phase
is **F63**.

---

## 0. What this release changes, and therefore what it can break

Stated at Gate 0 so the assessment in Phase 10 has something to be measured against:

| | v0.15.0 |
|---|---|
| New runtime dependencies | **0** (five, unchanged since v0.2.0) |
| New development dependencies | **0** — no doc tooling, no site generator, no link checker |
| New HTTP routes | **0** |
| New capabilities / RBAC changes | **0** |
| New audit actions | **0** |
| Migrations | **0** (`0001`–`0013`, unchanged) |
| Changes to `src/` | the version string, and nothing else |
| Changes to `eval/` | **none**; `eval/corpus/` is not touched |
| Intentional behaviour changes | **0** — declared in advance, per Part VII.9 |

The release's entire risk is therefore **epistemic rather than operational**: it does not change what
the appliance does, it changes what can still be verified about how it came to do it. That risk has
one name — a claim in the ledger that does not resolve at the tag it names — and one mitigation, the
ledger-completeness test of Part X, which does not exist yet.

**At Gate 0 the working tree still contains every document.** Nothing in this release has been
removed, and the halt in [`../gates/v0.15.0-phase-0.md`](../gates/v0.15.0-phase-0.md) is what keeps
it that way until `v0.13.0` and `v0.14.0` are published on the remote.

---

## 1. F61 — F58's scope is wrong in `SECURITY-REVIEW-0.14.0.md` §3.2

**Issued unfixed.** The mechanism is v0.15.5's work. What must change now is the ROADMAP line, and
the reason is that **a fix built to the stated scope would not cover the worse case.**

### 1.1 The sentence

`SECURITY-REVIEW-0.14.0.md` §3.2, closing paragraph:

> **The guard that is working is `MIN_EDGE_N`.** 1 276 of 1 357 NE pairs never reach it — the median
> pair mass is 0.300, two orders of magnitude below the threshold. It holds for every ordinary pair
> and **is defeated only by a storm**, which is precisely the traffic it most needs to hold for.

The first two sentences are correct and are not disputed. **The third has the direction backwards**,
and it also mis-describes what `storm` means in the code.

### 1.2 The mechanism

```python
# learn.py
MIN_EDGE_N = 5.0        # co-occurrence mass before an E edge is trusted
STORM_DAMPING = 0.1     # 10x smaller updates during mass storms
STORM_ALARMS = 50       # window/situation occupancy that defines a storm

# correlate.py:326 — inside Correlator.process, BEFORE the new alarm joins the window
storm = len(self.index) >= STORM_ALARMS

# engine.py:294-296
self.learner.observe_activation(item_pair)
self.learner.observe_pairs(item_pair, [...recent...], outcome.storm)
```

`storm` is a property of **the window's occupancy at the instant an alarm arrives**, not of the size
of the burst. Below `STORM_ALARMS`, `weight` is `1.0` and no damping applies at all.

### 1.3 The reproduction

Driven through the shipped `netcorenoc.learn.Learner` and the real call sequence `engine._process`
uses. No re-implementation of NPMI, of damping, or of the threshold.

```
--- rounds of co-occurrence before the E edge is TRUSTED ---
  burst   probe pair   damped at formation?   rounds to trust
    340       (0, 1)                  False                 5
    340     (60, 61)                   True                51
    340   (300, 301)                   True                51
     40       (0, 1)                  False                 5
     40     (38, 39)                  False                 5
```

```
--- equal rounds, sub-threshold burst vs a genuine in-storm pair ---
rounds= 40
   40 alarms  (sub-threshold, UNDAMPED) mass= 40.0000 trusted= True affinity=0.975610
  340 alarms  (storm, DAMPED)           mass=  4.0000 trusted=False affinity=0.000000
  -> sub-threshold burst reaches the higher affinity: True

rounds= 50
   40 alarms  (sub-threshold, UNDAMPED) mass= 50.0000 trusted= True affinity=0.980392
  340 alarms  (storm, DAMPED)           mass=  5.0000 trusted=False affinity=0.000000
```

**A 40-alarm burst defeats `MIN_EDGE_N` in 5 rounds. A genuinely damped in-storm pair needs 51 — ten
times the repetition.** The storm is the *harder* case for an attacker or a confounder, not the
easier one.

> **The `rounds=50` row reads `mass=5.0000 trusted=False`, and that is correct output, not a
> transcription error.** `STORM_DAMPING` is `0.1`, and fifty binary-floating-point additions of
> `0.1` accumulate to `4.999999999999998`, which is below `MIN_EDGE_N = 5.0`. The display rounds to
> four places; the comparison does not. This is why the crossing is at 51 rounds and not 50, and it
> is noted here so that a later reader does not "fix" a line that is right.

### 1.3.1 Reproduce it

Self-contained; drives the shipped module and adds nothing to the repository.

```python
# python - <<'EOF'   (from the repository root, with src/ importable)
import sys

sys.path.insert(0, "src")
from netcorenoc.learn import MIN_EDGE_N, STORM_ALARMS, Learner

LEARN_CAP = 20  # engine.py:65


def burst(learner, n, class_id=1):
    window = []
    for k in range(n):
        item = (class_id, k)  # one distinct NE per alarm
        storm = len(window) >= STORM_ALARMS  # correlate.py:326, BEFORE window.append
        learner.observe_activation(item)  # engine.py:294 — never damped
        learner.observe_pairs(item, window[-LEARN_CAP:], storm)  # engine.py:295
        window.append(item)


def rounds_to_trust(n, probe, limit=5000):
    learner = Learner()
    for r in range(1, limit + 1):
        burst(learner, n)
        if learner.E.pair_mass(*probe) >= MIN_EDGE_N:
            return r


print("40 alarms, sub-threshold pair :", rounds_to_trust(40, (38, 39)))  # 5
print("340 alarms, in-storm pair     :", rounds_to_trust(340, (300, 301)))  # 51
print("340 alarms, EARLY pair        :", rounds_to_trust(340, (0, 1)))  # 5  <- §1.5
# EOF
```

### 1.4 The controls

| control | must be | measured |
|---|---|---|
| a single pair, one burst of 2 alarms | not trusted | `mass=1.0000 trusted=False affinity=0.000000` ✔ |
| a 40-alarm burst, **one** round | not trusted | `mass=1.0000 trusted=False affinity=0.000000` ✔ |

The second control is the one that keeps the finding honest: **the defeat still requires
repetition.** F61 is not "one burst opens an edge". It is that the amount of repetition required is
*lowest* exactly where the review says the guard holds.

### 1.5 The second defect in the same sentence

A first probe of this finding compared the **earliest** pair of each burst and found *no difference
at all* between 40 alarms and 340 — both trusted after 5 rounds, both reaching affinity
`0.975610`. That null result is not noise; it is a second, independent problem with the same
sentence:

> **In a 340-alarm storm, the first ~50 pairs are formed while the window is still under
> `STORM_ALARMS`, and are never damped either.**

`storm` is recomputed per arrival. A storm does not damp its own beginning. So "a storm" is not a
population of damped pairs — it is a population whose first fifty are undamped and whose remainder
are damped, and the review's sentence treats it as uniform.

### 1.6 What does **not** reproduce, stated plainly

The build brief's Part IV describes this finding as: *"40 concurrent alarms produce pair mass 40.0
and entity affinity 0.9756, **higher** than a 340-alarm storm's 0.9714."*

**`0.9756` reproduces exactly** — `0.975610` for a 40-alarm burst at 40 rounds. **`0.9714` does
not.** Under every path driven here, a 340-alarm burst's early (undamped) pair gives the *same*
`0.975610`, and its late (damped) pair gives `0.0` because it is not trusted at all at 40 rounds.
No configuration reproduced `0.9714`.

The qualitative claim — F58's scope is too narrow and a storm-scoped fix would miss the worse case
— **reproduces and is stronger than stated.** The specific figure is not carried forward, and this
paragraph exists so that nobody later cites `0.9714` as measured. Appendix B: *when a command and a
paragraph disagree, the command is more likely to be right.*

### 1.7 Consequence

**The ROADMAP line for v0.15.5 must be rewritten before anyone implements it.** A fix scoped to
"storms" — raising `STORM_ALARMS`, deepening `STORM_DAMPING`, or damping harder above the threshold
— leaves the 5-round sub-threshold path untouched, and that path is the faster one. The boundary
that matters is not the storm threshold; `MIN_EDGE_N` is defeated by **repetition at any burst
size**, and `STORM_DAMPING` only ever slowed down the half of the traffic that was already slower.

---

## 2. F62 — the decision half of the discrimination floor rests on one row

**Issued unfixed.** The background set is registered by `PREREGISTRATION-0.14.0.md` §3 and frozen by
`tests/test_preregistration.py`, so it will not change. The finding is that the property holds **by
one row of data rather than by any line of code** — Appendix B's *"a property that holds by
accident."*

### 2.1 The check

`shadow_admission.verdict` refuses a scorer on either of two independent grounds:

```python
if challenger["score_spread"] <= challenger["min_score_spread"]:
    reasons.append("discrimination (spread): …")
if not challenger["probes_linked"] or not challenger["probes_unlinked"]:
    reasons.append("discrimination (decision): … links every pair or no pair …")
```

The two halves are deliberately separate — §4.2 of the plan requires the reason to say *which*
malfunction occurred. This finding is about the **second** one.

### 2.2 The measurement

Champion (`scoring.default_scorer()`) over the registered 256-row background, via the shipped
`probe_features()`:

```
linked   : 255
unlinked : 1   at index [0]
spread   : 0.10052657518281488   (floor 0.01 — the SPREAD half passes comfortably)
lowest   : 0.008169838526691757   (row 0, linked=False)
next     : 0.6137043257036225     (row 4, linked=True)
the gap  : 0.605534
```

**There is no second candidate anywhere near the decision boundary.** The property "both verdicts are
present" is carried by a single row, at a score two orders of magnitude below the next lowest.

### 2.3 The counterfactual, through the real gate

Not a re-implementation of the reason logic — the real `admission()` and `verdict()`:

```
--- drop row 0 (255 probes remain) ---
  linked=255  unlinked=0  spread=0.0891994038967323
  verdict ok = False
    - discrimination (decision): 255 of 255 probes linked and 0 did not. A scorer that links
      every pair or no pair returns one answer to every question.
```

**The incumbent fails the floor it is the reference for**, on a background set differing from the
registered one by one row.

### 2.3.1 Reproduce it

```python
# python - <<'EOF'   (from the repository root, with src/ importable)
import sys

sys.path.insert(0, "src")
from netcorenoc.scoring import default_scorer
from netcorenoc.shadow_admission import admission, probe_features, verdict, _discrimination

probes, champion = probe_features(), default_scorer()
samples = probes[:64]

print("full 256 rows :", _discrimination(champion, probes)[1:])  # (255, 1)
ok, why = verdict(
    admission(default_scorer(), samples, budget_ratio=3.0, probes=probes),
    admission(champion, samples, budget_ratio=3.0, probes=probes),
)
print("verdict       :", ok, why)  # True []   <- the control

drop = next(i for i, f in enumerate(probes) if not champion.score(f).linked)
reduced = [f for i, f in enumerate(probes) if i != drop]
print("dropping row  :", drop, _discrimination(champion, reduced)[1:])  # 0 -> (255, 0)
ok, why = verdict(
    admission(default_scorer(), samples, budget_ratio=3.0, probes=reduced),
    admission(champion, samples, budget_ratio=3.0, probes=reduced),
)
print("verdict       :", ok)  # False
for r in why:
    print("   -", r)
# EOF
```

### 2.4 The controls

| control | must be | measured |
|---|---|---|
| the real gate on the **real** 256-row background | decision half **passes** | `verdict ok = True`, `reasons = []` ✔ |
| dropping any **other** single row | decision half still passes | `rows whose removal breaks it: none` ✔ — all 255 checked |
| the **spread** half under the same removal | survives | `0.1005 → 0.0892`, both ≫ floor `0.01` ✔ |

The first control is what makes §2.3 evidence: the failure there is caused by the missing row and
not by the harness. The second establishes that **exactly one of 256 rows** carries the property —
measured over every row, not asserted. The third shows the two halves are not interchangeable: what
breaks is specifically "both verdicts present", not discrimination in general.

### 2.5 Why it is issued rather than fixed

The background is registered and frozen, so the property cannot silently change: any edit to
`background.py` fails `tests/test_preregistration.py`'s sibling guard and any edit to the plan fails
the hash. **The risk is not that the row disappears. It is that the guarantee is believed to be
structural when it is empirical**, and the next release that regenerates the background — from a
different corpus, a different stride, or a `MAX_CANDIDATES` change — could lose it without any test
saying so, because no test asserts *"the background must contain at least one row the champion does
not link."*

That test is the fix, and it belongs to the release that next revisits the background set. Writing it
now would pin a property of data this release has no mandate to touch.

---

## 3. What is not assessed here

Phase 10 assesses the release. This document, written at Gate 0, deliberately does **not** claim:

* that `src/` is byte-identical — nothing has changed yet, and the claim belongs after the change;
* that the ledger's claims are verifiable — **there is no ledger**, and Part XI's rule is that it is
  written in Phase 2 while the documents still exist;
* that the record rewrite is recorded — `docs/adr/DECISIONS.md` is untouched at #196.

Each is a Phase 10 assertion and each would be vacuous today. A review that made them at Gate 0
would be the *"measuring nothing and concluding CLOSED"* trap of Appendix B.
