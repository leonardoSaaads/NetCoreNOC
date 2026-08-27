# Open findings

Every finding this project has issued and not closed. Five bullets each: what it is, how to
reproduce it, what the reproduction printed, why it matters or what would repair it, and its
disposition.

The series is continuous and is never renumbered. **F1–F55, F57, F59 and F60 are closed** — the
reviews that issued and closed them are at commit `3ecf237` (see [`record.md`](record.md)). From
v0.15.0 a finding is an entry here rather than a section in a per-release security review; the
reason is [decision #197](adr/DECISIONS.md).

A finding **issued and closed by the same release** keeps its entry, marked so in its disposition:
the entry is where the reproduction and the measurement live, and deleting it the moment the fix
lands would throw away the only record of what the guard could not see. F64 is the first.

Run every command below from the repository root with the virtualenv active.

---

## F56 — a malformed corpus file hangs `eval/harness.py` rather than failing it

- **What**: a scenario missing its `truth` key raises `KeyError` at `eval/harness.py:233`, prints a
  traceback, and never exits. `run_scenario` leaves the `Engine.run()` task alive and
  `asyncio.run`'s shutdown waits on it. In CI that is an indefinite hang, not a red build.
- **Reproduce**: copy any `eval/corpus/*.json` to a scratch directory, delete one event's `truth`
  key, point the harness at it. **It does not terminate** — the reproduction is the hang, so run it
  under `timeout 30`.
- **Measured**: traceback printed, exit code never returned; `timeout` is what ends it.
- **Repair**: one `finally` that cancels the task.
- **Disposition**: open, unfixed. Offline tooling, so the blast radius is a person's afternoon.
  Issued in the v0.14.0 review.

## F58 — a storm defeats `MIN_EDGE_N` for every NE in the window

- **What**: `Learner.observe_pairs` deposits mass on the pair between the new alarm's NE and **every
  distinct other NE currently in the window**, so a large enough burst clears the `MIN_EDGE_N = 5.0`
  gate that is supposed to mean *"this pair has been seen together enough times to trust"*.
- **Reproduce**: see F61's command, which measures both branches.
- **Measured**: in a storm, 52 arrivals across two NEs clear the gate at weight `STORM_DAMPING`.
- **Repair**: unknown. `STORM_DAMPING` damps the pair mass and not the marginals, and the fix has to
  decide what the guard is counting.
- **Disposition**: open, unfixed, and **its stated scope is wrong — see F61.** Issued in the v0.14.0
  review; not fixed there because the trap path was byte-identical for that whole release, and not
  fixed here because v0.15.0 changes no `src/`.

## F61 — F58's scope is stated backwards, and the case that matters is the ordinary one

- **What**: `SECURITY-REVIEW-0.14.0.md` §3.2 says the guard *"holds for every ordinary pair and is
  defeated only by a storm."* Measured, both halves are wrong. It is defeated by **6** ordinary
  alarms, not 52 — `STORM_DAMPING` does not apply below `STORM_ALARMS`, so each arrival deposits
  1.0 rather than 0.1. And clearing it *in a storm* yields an entity affinity of **0.000000**,
  because the damping applies to the pair mass while `observe_activation` leaves the marginals
  undamped, so the NPMI is driven to zero. Clearing it in ordinary traffic yields **0.833**, rising
  to 0.93 by fourteen alarms.
- **Reproduce**:
  ```sh
  python -c "
  from netcorenoc.learn import Learner, MIN_EDGE_N
  def n_to_defeat(storm):
      lr, win = Learner(), []
      for i in range(1, 400):
          it = (7, 101 if i % 2 else 202)
          lr.observe_activation(it); lr.observe_pairs(it, list(win), storm); win.append(it)
          if lr.E.pair_mass(101, 202) >= MIN_EDGE_N:
              return i, lr.entity_affinity(101, 101, 202, 202)
  print('ordinary', n_to_defeat(False), 'storm', n_to_defeat(True))"
  ```
- **Measured**: `ordinary (6, 0.8333333333333334) storm (52, 0.0)`. The control — the arrival before
  each crossing — gives `0.0` in both branches, so the numbers are the guard's and not the probe's.
- **Why it matters**: a fix scoped to storms would address the branch that **cannot move a
  grouping**, and leave the branch that produces a 0.83 affinity term untouched. F58's own quoted
  figures (`5/6, 6/7, 7/8, 8/9`, largest `34/35` "beside a pair mass of 34.000") are the *undamped*
  branch: affinity is exactly `m/(m+1)` there, and an integer pair mass is only reachable at weight
  1.0. F58 measured ordinary traffic and described a storm.
- **Disposition**: open, issued not fixed. v0.15.0 changes no `src/`. The next release that touches
  the correlator owns it, and should decide what `MIN_EDGE_N` is counting before changing either
  number.

## F62 — the discrimination floor's decision half rests on one row of data

- **What**: `shadow_admission.verdict` refuses a scorer that links every probe or no probe. Over the
  frozen 256-row background of `PREREGISTRATION-0.14.0.md` §3, the champion links **255** and leaves
  **one** unlinked. Remove that row and the incumbent fails the floor it is itself the reference for.
- **Reproduce**:
  ```sh
  python -c "
  from netcorenoc.scoring import AdditiveScorer
  from netcorenoc.shadow_admission import admission, probe_features, verdict
  p = probe_features(); a = lambda ps: admission(AdditiveScorer(), ps, budget_ratio=1e9, probes=ps)
  print('full', verdict(a(p), a(p))[0], a(p)['probes_linked'], a(p)['probes_unlinked'])
  print('minus row 0', verdict(a(p[1:]), a(p[1:]))[0])"
  ```
- **Measured**: `full True 255 1` / `minus row 0 False`. Sweeping all 256 single-row deletions: **1
  of 256** refuses, and it is row 0. Row 0 scores `0.00817` against a threshold of `0.5`; the next
  closest row is `0.1137` away, so no other row could take over the role. Controls: the full set
  admits the champion (or every deletion would refuse), and a constant scorer is refused on the same
  set (or the check would be unreachable).
- **Why it matters**: the property holds by one row of the background rather than by any line of
  code, and the background is frozen by a pre-registration hash — so the guard is *stable*, and
  stable is not the same as *grounded*.
- **Disposition**: open, issued not fixed. Widening the background is a change to a pre-registered
  artefact and belongs in a new pre-registration, not in a patch.

## F63 — the admission speed check compares one order statistic against itself

- **What**: `verdict` refuses on `challenger.p99_us > champion.p99_us * budget_ratio`, with both
  measured in the same process moments apart. p99 over 256 samples is the 254th sorted timing, so it
  is set by whichever call was interrupted rather than by the code.
- **Reproduce**: run `admission(AdditiveScorer(), probes, budget_ratio=r, probes=probes)` twice and
  take the ratio of the two `p99_us`, repeatedly. Found while sweeping F62.
- **Measured**: over 250 paired runs of the **same class** at the production ratio (10.0): 0
  refusals, ratio min `0.25`, median `1.01`, max `3.66`. At `budget_ratio=3.0` the F62 sweep produced
  **7 speed refusals in 256** paired runs, none of which reproduced on re-measurement. Control: a
  scorer with a 200 µs sleep is refused at 10.0, so a speed refusal is reachable.
- **Why it matters**: the headroom between the observed noise (3.66×) and the production budget (10×)
  is a factor of 2.7, it is a property of the machine rather than of the model, and nothing measures
  or pins it. A slower CI runner narrows it.
- **Disposition**: open, issued not fixed. A median or a repeated-measures comparison would be a
  behaviour change to the promotion gate, which v0.15.0 does not make.

## F64 — the citation guard could not see inside an f-string, and it cost a decision entry

- **What**: `tests/test_documentation.py::_python_citations` filtered `token.type in (COMMENT,
  STRING)`. **PEP 701 (Python 3.12) moved f-strings out of `tokenize.STRING`** into
  `FSTRING_START` / `FSTRING_MIDDLE` / `FSTRING_END`, so from the day this project moved to 3.12
  every citation written inside an f-string was invisible to the guard that asserts a cited decision
  still resolves — and to the measurement v0.15.0 used to decide which entries nothing cited.
- **Reproduce**:
  ```sh
  python -c "
  import sys, pathlib, tempfile; sys.path.insert(0, 'tests')
  import test_documentation as td
  d = pathlib.Path(tempfile.mkdtemp())
  for label, src in {
      'CONTROL  plain string': 'DOC = \"a string citing #176\"',
      'CONTROL  comment     ': '# a comment citing #176',
      'TREATMENT f-string   ': 'X = 1\nDOC = f\"citing #176 {X}\"',
  }.items():
      p = d / (label.split()[1] + label.split()[0] + '.py'); p.write_text(src)
      print(label, '->', sorted(td._python_citations(p)))"
  ```
- **Measured**, before the fix: `CONTROL plain string -> [176]`, `CONTROL comment -> [176]`,
  `TREATMENT f-string -> []`. Widening the filter over the whole tree surfaced exactly **one**
  previously invisible citation — `#176` in `tests/test_security_ui.py`, in the f-string that builds
  a failure message — and it did not resolve: v0.15.0 had deleted that entry, on the measurement
  this blind spot corrupted.
- **Why it matters**: the class, not the instance. A guard written against one version of a language
  keeps reporting green after the language moves the thing it reads. The question that finds this is
  *"what can my guard not see?"*, and it is not the question a passing test asks.
- **Disposition**: **closed in v0.15.1** (#215). The filter is widened, the tokens are resolved by
  `getattr` so the reader still works on an interpreter that predates PEP 701, decision #176 is
  restored, and `test_the_citation_reader_sees_comments_and_strings_and_nothing_else` gained an
  f-string case beside the two controls that always passed.
