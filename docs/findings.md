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
  from netcorenoc.engine.correlate.learn import Learner, MIN_EDGE_N
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
  from netcorenoc.engine.correlate.scoring import AdditiveScorer
  from netcorenoc.engine.evaluation.shadow_admission import admission, probe_features, verdict
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
- **v0.15.1 adds a consequence F63 did not record: it makes a byte-frozen gate intermittently red.**
  `test_shadow.py::test_the_report_is_deterministic_across_two_runs` compares two renderings of the
  shadow report with the two measured durations blanked. The **admission verdict is not blanked**,
  and it flips:

      -           False
      +            True
      - - speed: p99 23.952us over the budget 21.990us (10.0x the champion's 2.199us)

  A champion measured at 2.199 µs puts the budget at 21.99 µs, and the challenger's p99 came in at
  23.952 µs — a refusal at the production ratio, which F63's own sweep did not produce in 250 paired
  runs. **Measured**: 1 failure in 60 runs on the v0.15.1 tree and **4 in 60 on the v0.15.0 tree it
  was built from**, so the package move neither caused it nor changed it; 60 report pairs rendered
  in a single interpreter produced no difference at all, which is why it had not been seen.
- **Disposition**: **the finding is open; its intermittent test is fixed in v0.15.2.** The finding
  itself stands — a median or a repeated-measures comparison would be a behaviour change to the
  promotion gate, and this release changes no promotion behaviour. What v0.15.2 removes is the
  *flap*: `test_shadow.py`'s two rendering comparisons take a `pinned_scoring_clock` fixture that
  pins `time.perf_counter_ns` for the 256 timed `score()` calls, so `p99_us` stops being a coin
  toss between two identical scorers. `verdict()` still computes the refusal from the real values.
  **Measured on this tree**: 1 failure in 60 runs before (*"speed: p99 32.182us over the budget
  28.140us"*), **0 in 60 after**. Two things are deliberately not pinned:
  `test_the_report_measures_timings_that_are_real` stays on the real clock, because a synthetic
  counter would make the one test that asserts the timings are measurements vacuous; and
  `test_the_pinned_clock_does_not_hide_a_refusal_that_is_real` is the control — a scorer that reads
  the pinned counter forty extra times per call is still refused on speed, so the two tests above
  are not green because the check became unreachable.

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

## F65 — 67 prose references still name a module by its pre-v0.15.1 import path

- **What**: v0.15.1 moved 56 modules and rewrote every import that names one. It did **not** rewrite
  the module paths written in prose — docstrings, comments and assertion messages — because prime
  directive 1 for that release is that a move changes a file's imports and nothing else, and the
  content census is what proves it did. So `varbind_profile.py`'s docstring still says
  *"the other axis is `netcorenoc.shaping.scope`"* when the module is now
  `netcorenoc.crosscutting.shaping.scope`, and 66 more like it.
- **Reproduce**:
  ```sh
  python -c "
  import io, pathlib, re, tokenize
  moved = re.compile(r'netcorenoc\.(correlate|learn|scoring|capture|labels|census|incidents|seal|'
                     r'shadow|promotion|judge|training|challenger|attribution|receiver|events|'
                     r'known_oids|audit|auth|rbac|shaping|settings|runtime|logsetup|maintenance|'
                     r'gaps|engine_base|preview|severity|rootcause|bias|agreement)\b')
  n = 0
  for root in ('src', 'tests', 'eval', 'tools'):
      for p in sorted(pathlib.Path(root).rglob('*.py')):
          if '__pycache__' in p.parts: continue
          for t in tokenize.tokenize(io.BytesIO(p.read_bytes()).readline):
              if t.type in (tokenize.COMMENT, tokenize.STRING): n += len(moved.findall(t.string))
  print(n)"
  ```
- **Measured**: **50** — 44 in `src/`, 5 in `tests/`, 1 in `eval/`, 0 in `tools/`. None is an
  import: `mypy --strict` passes over 214 files and the suite is green, so every one of them is a
  sentence rather than a dependency. *(v0.15.2 corrects this bullet: it said 67, and the command
  above prints 50 on the tree it was written against — see F70.)*
- **Why it matters**: it is the same shape as the `docs/gates/…` citations `record.md` covers, and
  it has the same defence — no guard can see it. `test_documentation.py` checks decision numbers and
  Markdown links; nothing checks that a module path in a docstring resolves. The honest options are
  a guard that reads them (which would then have to be kept green through every future move) or a
  reading rule stated once. This release proposes neither and measures the size of the problem.
- **Disposition**: **closed in v0.15.2** (#229): a reading rule in [`record.md`](record.md), stated
  once, rather than a guard that every future move would have to be kept green through. Rewriting
  the docstrings inside the move release would have forfeited the census — the one property that
  made the move reviewable — to fix references that `git log --follow` already resolves.

## F66 — a startup failure hangs the appliance instead of exiting

- **What**: any exception between `Store.open()` and the end of `runner.run` leaves the store open,
  and `aiosqlite`'s connection worker thread is **not a daemon** — so the process prints a traceback
  and then never exits, ignoring `SIGTERM`. Two paths reach it: a failure before the `try:`
  (`start_receiver`), and a failure inside it, because the `finally:` re-awaits the same tasks and
  the already-failed one re-raises at `runner.py:224`, skipping the drain, the final maintenance
  pass and `store.close()`. Under `Restart=on-failure` (the systemd unit) and
  `restart: unless-stopped` (the compose file) a hung process is never restarted.
- **Reproduce**: start the appliance with something ordinary wrong, send `SIGTERM`, and measure.
  ```sh
  # TREATMENT — anything already on the HTTP port
  python -c "import socket,time;s=socket.socket();s.bind(('127.0.0.1',8097));s.listen(1);time.sleep(600)" &
  NETCORENOC_DB=/tmp/f66.db NETCORENOC_HTTP_PORT=8097 \
    timeout --signal=TERM --kill-after=20 12 python -m netcorenoc.main; echo "rc=$?"
  # CONTROL — a refusal the design intends, which must exit at once
  NETCORENOC_API_TOKEN=x timeout --signal=TERM --kill-after=20 12 python -m netcorenoc.main; echo "rc=$?"
  ```
- **Measured**: four treatments — HTTP port in use, trap port in use, `NETCORENOC_HTTP_PORT=99999`,
  `NETCORENOC_ALLOWLIST=not-a-cidr` — all `rc=137` after **32.0 s**, i.e. they survived `SIGTERM`
  and needed `SIGKILL`. `NETCORENOC_TLS_CERT` set without `NETCORENOC_TLS_KEY` is a fifth. Controls:
  `NETCORENOC_API_TOKEN=x` exits `rc=1` in **0.5 s**; a clean start takes `SIGTERM` and exits in
  12.1 s. A direct measurement shows the cause: an open `aiosqlite` connection leaves
  `Thread-1 (_connection_worker_thread) daemon=False alive=True` after `asyncio.run` returns.
- **Disposition**: **fixed in v0.15.2** (#225). `run()` closes the store on every exit path and the
  `finally:` no longer re-raises a task's exception before the cleanup it guards.

## F67 — every per-term link row is clipped at 390 px, and the pair is what is lost

- **What**: `.linkrow` is a non-wrapping flex row — score, three fixed-pixel term bars, then the two
  alarm names the link is between. At a phone width the names run past the row's box, which has no
  `overflow-x` and no scrollable ancestor, so they are clipped and unreachable. A row reading
  `0.65 T 0.30 A 0.00 E 0.35` with no pair is a decomposition of nothing.
- **Reproduce**: sign in, open a situation, and measure `.linkrow` and `.linkpair` at both widths —
  `scratchpad/clip.mjs` in the v0.15.2 build, or by hand in any browser's device toolbar at 390 px.
- **Measured**: at 390x844, all **30** rows overflow by **51 px** and all **30** `.linkpair` boxes
  fall right of the viewport, `scrollableAncestor: null`, page `scrollWidth == clientWidth == 390`
  (so the page does not scroll to them either). Control at 1440x900: overflow **0 px**, **0** boxes
  off-screen. The three term numbers stay on screen in both.
- **Why it matters**: it is the opposite of what `plans/v0.15.2-console.md` §2 predicted. The
  per-term contributions are **not** hidden on a phone — they render in `#work`, and the panel the
  breakpoint hides never held them. What the breakpoint costs is nothing; what the row costs is the
  identity of the pair.
- **Disposition**: **fixed in v0.15.2** (#220): the row wraps below the breakpoint.

## F68 — a wrong allowlist is invisible: no log line, no warning, no rendered counter

- **What**: `receiver.denied` is the only evidence an operator has that their allowlist is refusing
  their equipment. It is served by `/api/stats`, rendered by no screen, and logged nowhere; and the
  `warnings` channel only fires when the allowlist is **empty**, so a wrong one is quieter than no
  one at all. The appliance receives traffic, produces nothing, and says nothing.
- **Reproduce**: two appliances, same traps, allowlists that differ only in whether they match.
  ```sh
  # CONTROL
  NETCORENOC_ALLOWLIST=127.0.0.0/8  ... python -m netcorenoc.main
  # TREATMENT
  NETCORENOC_ALLOWLIST=10.99.0.0/16 ... python -m netcorenoc.main
  # then, into each: python tools/trap_replay.py eval/corpus/fiber_cut.json --port <port> --time-scale 0
  ```
- **Measured**: control `{received: 8, accepted: 8, denied: 0}`, 2 devices, 8 alarms. Treatment
  `{received: 8, accepted: 0, denied: 8}`, **0 devices, 0 alarms, `warnings: []`**. Log lines
  emitted while the traps arrived: **0 in both arms**.
- **Disposition**: **fixed in v0.15.2** (#222, #227): the counters reach the Overview, and a
  non-zero `denied` raises an operator warning — a counter read on the maintenance pass, never a
  line per packet (principle 4).

## F69 — five environment variables fail with a traceback that never names them

- **What**: `Settings.from_env` calls `int()` and `float()` on `NETCORENOC_TRAP_PORT`,
  `NETCORENOC_HTTP_PORT`, `NETCORENOC_RETENTION_DAYS` and `NETCORENOC_AUDIT_RETENTION_DAYS`, and
  `parse_allowlist` calls `ip_network` on `NETCORENOC_ALLOWLIST`. Each raises a bare `ValueError`
  through 20 lines of traceback that names the value and never the variable. Setting a documented
  variable to empty — which `.env.example` invites, since every line in it is an assignment an
  operator edits — is one of the cases.
- **Reproduce**: `NETCORENOC_RETENTION_DAYS= python -m netcorenoc.main`, and the same for the other
  four; control with `NETCORENOC_API_TOKEN=x`, which is designed to refuse.
- **Measured**: `ValueError: could not convert string to float: ''` /
  `ValueError: invalid literal for int() with base 10: 'abc'` /
  `ValueError: 'not-a-cidr' does not appear to be an IPv4 or IPv6 network`. The control prints
  *"NETCORENOC_API_TOKEN (and the legacy OPTICORR_API_TOKEN) was removed in v0.3.0. Unset it and
  issue a named service token instead…"* — the project already knows how to do this.
- **Disposition**: **fixed in v0.15.2** (#226).

## F70 — F65's own reproduction command does not produce F65's number

- **What**: F65 records **67** pre-v0.15.1 module paths in prose, *"49 in `src/`, 17 in `tests/`"*.
  Run verbatim on the tree it was written against, its command prints **50**.
- **Reproduce**: the code block in F65, unmodified.
- **Measured**: **50** — `src` 44, `tests` 5, `eval` 1, `tools` 0 — and the same at `3e5e874`,
  `44a1893` and `aac8fca`, so no commit in that range moved it. F65's per-directory split is what
  fails first: `tests/` is 5, not 17.
- **Why it matters**: the class, not the instance. A finding's measurement is the part a later
  release acts on, and this one would have sized a repair against a number nothing produces —
  *incrementing a number is not measuring it*, one document over.
- **Disposition**: **closed in v0.15.2**. F65's figure is corrected in place and the reading rule
  #229 chooses is stated once, so the count stops being a number anyone has to maintain.

## F71 — the console tells an operator to read a file that was deleted three releases ago

- **What**: the Network graph screen renders, to the operator, *"Naming that is deliberate: see
  `docs/gates/v0.13.0-phase-6.md`."* `docs/gates/` was deleted in v0.15.0. The reading rule in
  `record.md` resolves such a citation for someone with the repository; it cannot help someone
  looking at a screen. `.env.example` and `docker-compose.yml` name `docs/security/operations.md`
  the same way, and `SECURITY.md`'s link text says `docs/security/` while the link goes to
  `docs/security.md`.
- **Reproduce**: `grep -rn 'docs/gates/\|docs/security/' src/netcorenoc/ui/ .env.example docker-compose.yml`
  then `test -d docs/gates || echo ABSENT`.
- **Measured**: 1 operator-visible citation (`graph.js:167`), 2 in shipped configuration files, 3 in
  console source comments, and all four directories absent.
- **Disposition**: **fixed in v0.15.2** for the operator-visible one and the two configuration
  files; the source comments are covered by the reading rule (#229).

## F72 — the timeline caption describes two encodings the timeline does not have

- **What**: `views/timeline.js` tells the operator *"Raise marks sit above the axis in the alarm
  colour and carry a triangular glyph in the table"* and *"Both encodings are present so neither
  colour nor shape is load-bearing alone."* The table's `raise / clear` column renders the bare
  string and no glyph; the drawing places every mark on its device's row rather than above or below
  an axis; and `circle.tl-raise` and `circle.tl-clear` differ **only** in `fill`.
- **Reproduce**: `grep -n 'tl-raise\|tl-clear' src/netcorenoc/ui/style.css` and read the `kind` cell
  in `views/timeline.js`.
- **Measured**: the two rules differ in one declaration each, both `fill`. The word *raise* or
  *clear* does appear in the table, which is the accessible equivalent — so the fact is reachable
  and only the caption is wrong.
- **Disposition**: **fixed in v0.15.2**: the caption says what is actually encoded.

## F73 — `flake.nix` declares version 0.1.0, and the release check does not read it

- **What**: `flake.nix` builds `netcorenoc` with `version = "0.1.0"`. `tools/release_check.py`
  compares `pyproject.toml`, `src/netcorenoc/__init__.py` and `CHANGELOG.md` — three of the four
  places this repository writes its version down.
- **Reproduce**: `grep -n 'version' flake.nix` beside `python tools/release_check.py`.
- **Measured**: `flake.nix:16: version = "0.1.0";` while the check prints *"all sources agree on
  version 0.15.1"*. Fifteen releases apart, and green.
- **Disposition**: **fixed in v0.15.2** (#230): the version is corrected and the check reads four
  files, with a test that goes red when a fifth declaration appears unchecked.

## F74 — no release tag reachable from this repository except v0.12.0

- **What**: `docs/record.md` resolves every deleted document through commit `3ecf237` and cites tag
  `v0.14.0-gate0` as carrying a pre-registration hash *"in the tag's own annotation, independently
  of any file"*. No such tag exists locally or on the remote.
- **Reproduce**: `git tag -l` and `git ls-remote --tags origin`.
- **Measured**: local tags: **none**. Remote: `v0.12.0` only. Every cited **commit** resolves —
  `3ecf237`, `553b827`, `6b1c73a`, `78faace`, `4aed642` are all present in a full 207-commit
  history — so the reading rule itself is sound and only the tag claim is not.
- **Disposition**: open, not fixable from a build environment: a tag has to be pushed by the
  maintainer. The commands are in `HANDOFF.md`. Recorded here so the claim in `record.md` is not
  read as verified.

## F75 — an admin can store an allowlist that stops the appliance starting

- **What**: `POST /api/config` wrote `config.allowlist` into `meta`, audited it, answered
  `200 {"status":"saved"}` — and only then handed it to the live receiver, where `parse_allowlist`
  refuses it. The stored value **overrides the environment**, so the next boot could not start; and
  the Settings screen that would undo it is served by the appliance that will not start. The only
  way back was editing SQLite by hand.
- **Reproduce**: sign in as admin, `POST /api/config` with `{"allowlist": "not-a-cidr",
  "retention_days": 7}`, read `meta`, then ask what the next boot's `parse_allowlist` would do.
  Control: the same request with `10.0.0.0/8`.
- **Measured**: treatment — `POST -> 200`, `stored config.allowlist: 'not-a-cidr'`, *"next boot: the
  receiver would REFUSE"*. Control — `POST -> 200`, `stored: '10.0.0.0/8'`, *"next boot: the
  receiver would START"*.
- **Why it matters**: admin-only, so not a privilege boundary — but it is a one-request, irreversible
  denial of service on the appliance's own console, reachable by a typo in a text box.
- **Disposition**: **fixed in v0.15.2** (#226): `ConfigIn` parses the allowlist before the write, so
  a refused value is a 422 naming the entry and nothing is stored. A database written by an older
  version is handled too — the startup refusal names the stored row and prints the SQL that clears
  it.

## F76 — a corpus scenario fails its own stated requirement completely, and the aggregate hides it

- **What**: `eval/corpus/dual_incident.json` describes itself as *"Two unrelated incidents overlap
  in time on disjoint NEs; **must stay separate**."* They do not. A real appliance fed its sixteen
  traps over a real UDP socket at their real 0.3 s gaps puts **all sixteen in one situation** inside
  five seconds, merging both ground-truth incidents; the other three situations it formed are
  absorbed and left with zero members. It is F61's arithmetic arriving at the product —
  `MIN_EDGE_N` is cleared by **six** ordinary alarms, after which the entity-affinity term links
  network elements that have nothing to do with each other.
- **Reproduce**: `python -m pytest tests/test_operation.py` drives it end to end. For the offline
  half, which is where the gate looks:
  ```sh
  python -c "
  import asyncio, sys, pathlib; sys.path[:0] = ['eval', 'tools']
  import harness
  out = asyncio.run(harness.run_scenario(pathlib.Path('eval/corpus/dual_incident.json')))
  print(harness._score(out['scored']))"
  ```
- **Measured**: this scenario alone scores `pairwise_f1 0.636`, **`ari 0.000`**, **`over_merge_rate
  1.000`**, `under_merge_rate 0.000`. Every scored alarm carries `pred_sit dual_incident:sit1` while
  its `truth_sit` is `incident_A` for two devices and `incident_B` for the other two. `make eval`
  over the whole corpus reports `pairwise_f1 1.0000` and `over_merge_rate 0.0312`, and passes.
- **Why it matters**: the numbers are not in disagreement — they are differently weighted. The
  aggregate is pair-weighted and `pon_dying_gasp` contributes 1 051 of the corpus's events, so a
  16-event scenario at `over_merge_rate 1.0` moves it by a rounding error. **A gate whose
  aggregate can absorb a scenario that fails totally is a gate that cannot see a scenario.** The
  frozen hash is doing its job; what is missing is a per-scenario floor, and inventing one is a
  change to a gated artefact rather than a patch.
- **Disposition**: open, issued not fixed, and **pinned by a test that asserts the wrong answer on
  purpose** — `tests/test_operation.py::test_the_two_incidents_are_merged_into_one_situation_and_that_is_a_defect`,
  whose failure message says to replace it with the purity assertion when the correlator is
  repaired. Repairing it is F58/F61's disposition: *"the next release that touches the correlator
  owns it, and should decide what `MIN_EDGE_N` is counting before changing either number."*
  Doing it here would move `eval`'s frozen hash and the trap path in a release about neither.

## F77 — the network graph pushed three of its four nodes off the canvas

- **What**: three defects on the one screen no test executes. (1) A node's radius is
  `7 + 2.5 * sqrt(active_alarms)` with **no ceiling**, so a device carrying a storm grows without
  bound — and past `forceCollide(26)`'s own radius, which the layout then reasons with wrongly.
  (2) The force simulation had **no centring force** at all: charge repels at -220, link pulls only
  where an edge exists, collide only pushes apart, and nothing pulled toward the middle. (3) The
  SVG has no `viewBox`, so a node outside the box is simply gone rather than scaled back in.
- **Reproduce**: point a browser at the Network graph of an appliance carrying a busy device and
  read every `circle`'s `r` and whether its box is inside `#graphwrap`.
- **Measured**, on a 1 172 x 460 panel with four devices and ~1 400 active alarms:

  | | radii (px) | nodes on canvas |
  |---|---|---|
  | before | 12, 12, **62.96**, **80.70** | **1 of 4** |
  | + radius capped at 24 | 12, 12, 24, 24 | 1 of 4 |
  | + a centring force | 12, 12, 24, 24 | 3 of 4 |
  | + clamped to the box on each tick | 12, 12, 24, 24 | **4 of 4** |

  The largest circle covered **3.79 %** of the canvas and now covers **0.34 %**. Each step was
  measured on its own, which is how the second and third causes were found — capping the radius
  alone changed nothing about the ejection.
- **Why it matters**: it is the screen whose entire purpose is the relationships between elements,
  and an operator saw one circle. It is also the screen the DOM harness substitutes a recording
  double for, so **no assertion in this repository could have seen it** — `graph.js` says so in its
  own first paragraph, and this is what that sentence costs.
- **Disposition**: **fixed in v0.15.2**. Still not covered by any test, for the reason `graph.js`
  states; the measurement above is the evidence, and a browser is what produced it.

## F78 — MIGRATION.md's own row count has been one behind since v0.15.0

- **What**: the sentence an upgrading operator reads first — *"Six of nineteen have an action; the
  rest are start-the-new-binary"* — sits above a table of **twenty** rows.
- **Reproduce**: parse the table rather than counting by eye, and compare against the sentence:

  ```sh
  python - <<'PY'
  import re, pathlib
  t = pathlib.Path("MIGRATION.md").read_text(encoding="utf-8")
  rows = [l for l in t.splitlines() if re.match(r"^\|\s*v\d+\.\d+\.\d+\s*→", l)]
  print(re.search(r"(\w+) of (\w+) have an action", " ".join(t.split())).group(0), "vs", len(rows))
  PY
  ```
- **Measured**: the same probe run over **every commit that has ever touched the file** agrees at
  each one from v0.5.0, where the sentence was written, until `47157c0` (*release: v0.15.0 — the
  repository*), which added the twentieth row and left the sentence at nineteen. One commit, named
  by bisect rather than by guess.
- **Why it matters**: on its own, a wrong number in a sentence. What it demonstrates is that
  **a release that adds a row does not re-read the paragraph above it** — and this release adds a
  row too. A count nobody recomputes is a count that drifts once per release.
- **Disposition**: **fixed in v0.15.2**, along with the row this release owes. Not guarded by a
  test: the guard would have to know how many rows *ought* to exist, which is the same problem.
  Stated here so the next release knows the sentence is one it has to change.

## F79 — the sole admin can demote itself and permanently lock the appliance

- **What**: two independent defects that compose into a lockout. (1) `POST /api/users/{uid}/role`
  checks that the user exists and nothing else, so an admin may set its own role to `viewer` while
  it is the only admin; the same hole is in `DELETE /api/users/{uid}`, which refuses only
  *self*-deletion. (2) `auth.bootstrap_admin` guards on `count_users() > 0` — **users, not
  admins** — so once any non-admin account exists, a restart never re-bootstraps.
- **Reproduce**: boot a clean appliance; create a second non-admin user (**the control** — it must
  succeed, or a later refusal would be the endpoint being broken rather than the guard working);
  demote the sole admin; then **restart the process**. Both halves are needed: without the restart
  this is a bad interaction, not a lockout.
- **Measured**, by execution on the v0.15.2 tree:

  ```
  CONTROL   POST /api/users  viewer u2        -> 200 {"id":2,"username":"u2","role":"viewer"}
  TREATMENT POST /api/users/1/role  viewer    -> 200 {"status":"role changed"}
  GET /api/users  (same session)              -> 401 authentication required
  GET /api/me     (same session)              -> 401 authentication required
  POST /api/login admin                       -> 200 role="viewer"
  GET /api/users  as that principal           -> 403 insufficient role
  -- restart, same database --
  bootstrap_admin returned a password         -> False
  users in the database    [('admin','viewer'), ('u2','viewer')]
  ENABLED ADMINS REMAINING -> 0     count_users() = 2   <- what bootstrap_admin guards on
  ```

  The role change revokes the caller's sessions, which is correct in itself and is what turns a
  recoverable mistake into an immediate one: the operator is signed out mid-gesture.
- **Why it matters**: there is no CLI recovery command, and a restart does not help. The only
  remedy on the shipped tree is deleting the database — every situation, every learned entity,
  every audit row and the whole feedback dataset. The maintainer has lost an environment to it.
- **Disposition**: **fixed in v0.15.3** (#233, #234). Both halves: a last-enabled-admin invariant
  refused server-side wherever that could stop being true, and `bootstrap_admin` re-guarded on the
  quantity it always meant.

## F80 — the account screen runs 2 443 px off a 390 px phone

- **What**: `.kv` is `grid-template-columns: max-content 1fr`, and the account screen puts the
  principal's whole capability list in one `<dd>` of it. `max-content` is the *unwrapped* width of
  the longest row, so the grid is sized to the capability list laid end to end and the column never
  wraps. Thirty elements sit outside the viewport with no scrollable ancestor.
- **Reproduce**: sign in as admin at 390x844 in a real browser, go to `#/account`, and read the
  bounding box of every element against `document.documentElement.clientWidth`.
- **Measured**, Chromium at 390x844:

  | role | view | elements outside the viewport | worst right edge |
  |---|---|---|---|
  | admin | `account` | **30** | **2 833 px** (viewport 390) |
  | viewer | `account` | 10 | 2 833 px |
  | admin | `timeline` | 6 | left **−9 px** (y-axis labels clipped) |
  | admin | `promotion` | 1 | 489 px (a 64-hex run id) |

  `document.scrollWidth` equals `clientWidth` on every one of them, so **the page does not scroll
  and the content is simply unreachable** — the same shape as F67, on a screen F67 did not look at.
- **Why it matters**: it is the screen V.3 puts the two-factor and recovery declarations on, and
  the screen an operator opens to change their password on the device in their hand.
- **Disposition**: **fixed in v0.15.3** (#237). Not visible to the DOM harness, which has no
  layout: found by a browser, as F67 and F77 were.

## F81 — every interactive control in the top bar is below the touch-target floor

- **What**: the theme button measures **29x23**, the density button **30x23**, the identity link
  **32x19**, and the situation permalink **18x17** — at every width, phones included. Three to five
  controls per view are under 24 px on their short edge.
- **Reproduce**: at any width, measure `getBoundingClientRect()` of every `button`, `a[href]`,
  `input`, `select` and `[tabindex]:not([tabindex="-1"])`.
- **Measured**: 3 controls under 24 px on the leanest view, 5 on `overview` and `labelling`; the
  count is identical at 1440, 820 and 390 px, so nothing about the narrow layout addresses it.
- **Why it matters**: the accessibility floor v0.13.0 set is about *keyboard* reach and it holds.
  Pointer reach was never measured, and this product is now claimed to work on a phone.
- **Disposition**: **fixed in v0.15.3** (#236). One of the four controls — density — was removed
  rather than resized (#235).

## F82 — the account screen told the operator the opposite of what the route does

- **What**: after a successful `POST /api/password` the console rendered *"Password changed. Other
  sessions are unaffected."* The route calls `store.revoke_user_sessions(principal.user_id)`, which
  deletes **every** session that account holds — the caller's included — and its own return value
  says `"password changed; sign in again"`. The console overwrote a true sentence with its negation.
- **Reproduce**: open two sessions for one account, change the password from one, and ask `/api/me`
  from both.
- **Measured**:

  ```
  before: changer /api/me -> 200      other /api/me -> 200
  POST /api/password      -> 200 {"status":"password changed; sign in again"}
  after:  changer /api/me -> 401      other /api/me -> 401
  ```

- **Why it matters**: it is small in blast radius and large in what it says about how a caption gets
  written. `tests/test_auth.py::test_password_change_revokes_all_sessions` has asserted **both**
  sessions die since v0.2.0 — the suite knew, the route's own return string knew, and the screen
  said the opposite for three releases. A caption is not covered by a test that asserts the
  behaviour it describes, and nothing else was ever going to look.
- **Disposition**: **fixed in v0.15.3**, in the same commit as V.2's password surface. Found by
  reading the route while wiring the strength meter, then confirmed by execution rather than filed
  from the reading.

## F83 — the timeline's y axis had thirty pixels for a device name

- **What**: `timeline.js` used one constant, `PAD = 30`, for all four sides of the SVG.
  `d3.axisLeft` draws its tick labels to the **left** of the axis it is translated to, so every
  device name had 30 px and needed about 55.
- **Reproduce**: open the Timeline in a browser at any width and read the bounding box of every
  `text` under `g.axis` against the viewport.
- **Measured**, Chromium: `127.0.0.2` and `127.0.0.3` both at **x = −9**. At 390x844 that is six
  elements outside the viewport with `document.scrollWidth == clientWidth`, so the page did not
  scroll and the axis could not be read at all. After `PAD_LEFT = 82` and a clipped tick label:
  **zero**, at 1440, 820 and 390 px.
- **Why it matters**: the y axis is what makes the drawing a per-device timeline rather than a
  scatter of dots. It is also **the second d3 screen defect this project has found by looking**,
  after F77's three — and like those, no assertion in this repository could have seen it, because
  the DOM harness substitutes a recording double for d3 and has no layout at all.
- **Disposition**: **fixed in v0.15.3** (#246). Still not covered by a test, for the reason
  `graph.js` and `timeline.js` both state in their own first paragraphs; the measurement above is
  the evidence.

## F84 — the threshold a situation's links had to clear was never served

- **What**: `views/situations.js` has passed `threshold=${detail.threshold}` to "Why these were
  grouped" since v0.13.0. `GET /api/situations/{sid}` has never returned that key. The screen's
  own docstring lists *"the threshold the sum had to clear"* among the three things it provides.
- **Reproduce**: read the payload rather than the code — `curl` the route on a live appliance and
  look for the key.
- **Measured**: nineteen keys returned, `threshold` not among them. The rendered sentence was
  *"Every pair below scored above the link threshold."* — grammatical, complete-looking, and
  missing the only number that makes it checkable. The value lives in `scorer_config.threshold`
  and the situation row already names its configuration in `scorer_config_id`, so nothing had to
  be computed: it had to be joined.
- **Why it matters**: principle 2 is that an operator can check the grouping rather than trust it.
  A score of `0.55` with no threshold beside it is a number to trust. It also silently disarmed
  v0.15.3's own redesign, whose summary is built on the **margin** over that threshold — the
  screen would have shipped saying "the threshold was not reported" on every situation.
- **Disposition**: **fixed in v0.15.3** (#247), reading the configuration the situation names
  rather than the active one. Found in a browser: the missing value degraded to a sentence that
  read correctly, which is the failure mode a code review does not catch and a screen does.

## F85 — the container's console was missing five modules, and every test was green

- **What**: `[tool.setuptools.package-data]` listed one glob per directory level — `ui/*.js`,
  `ui/app/*.js`, `ui/app/views/*.js`. v0.15.3 added `ui/app/views/parts/` (#239) and did not add a
  fourth line, so a wheel built the way the **Dockerfile** builds one carried the console minus
  five modules. The container's first page load raised five
  `RuntimeError: File at path … does not exist`, for `why.js`, `verdict.js`, `facts.js`,
  `model.js` and `retention.js`.
- **Why nothing caught it, which is the larger half.** `package-data` is not the only thing that
  decides a wheel's contents: setuptools runs `egg_info` during every build, and
  `include_package_data` then ships whatever `SOURCES.txt` ends up naming. **Two different files
  will each complete a wheel that `package-data` leaves incomplete, and the container has
  neither:**

  1. **`MANIFEST.in`, whose `graft src` names every file under `src/`.** The Dockerfile copies
     `pyproject.toml README.md LICENSE` and `src/` — *not* `MANIFEST.in`. So it is absent from the
     image build and present in **every** build done in this repository, a clean clone and CI's
     included. This is the mask that mattered, and the first draft of this finding missed it.
  2. **`src/netcorenoc.egg-info/SOURCES.txt`**, left by `pip install -e` and excluded by
     `.dockerignore`. Redundant with the first on a developer's machine; sufficient on its own.

  So **every wheel built here was right and the one the container ran was wrong**, and v0.15.3's
  own delivery check — install the wheel, boot it, fetch all 45 declared assets — passed at 45/45
  while measuring a wheel no container can build.

  And a third mask, independent of those two: **the guard that exists for exactly this used the
  wrong matcher.** `test_all_ui_assets_are_covered_by_package_data_globs` matched with `fnmatch`,
  whose `*` crosses `/`. Setuptools expands package-data with `glob`, whose `*` stops at a
  separator.

- **Reproduce**, as a 2x2 over the two completing files, with v0.15.3's globs held fixed. The
  Dockerfile's `COPY` list decides the first column and `.dockerignore` the second, so the last
  row is the container and the first three are every machine this project is developed on:

  ```
  MANIFEST.in  src/*.egg-info | ui/app/views/parts/*.js in the wheel   UI files
  True         True           | 5/5                                    50
  True         False          | 5/5                                    50
  False        True           | 5/5                                    50
  False        False          | 0/5                                    45   <- the container
  ```

  With `ui/**/*` in place all four rows are 5/5 and 50 files. **The first attempt at this
  reproduction copied `MANIFEST.in` into the context and came out green**, which is the same
  mistake as the one being investigated and is why the guard now asserts the file's absence
  instead of merely arranging it.

- **Blast radius, measured on the same globs rather than assumed.** `pip install .` from a source
  checkout: 50/50, the checkout has `MANIFEST.in`. `pip install` of the sdist: 50/50 — the sdist
  excludes `MANIFEST.in` but carries the `SOURCES.txt` that `graft src` produced, which completes
  the wheel built from it. **Only the image build has neither**, so the container was the only
  affected install and every other one was correct under v0.15.3.

  And the matcher, directly:

  ```
  fnmatch("ui/app/views/parts/why.js", "ui/*.js")  -> True     <- what the guard believed
  glob("ui/*.js")                                  -> 1 file, none of them under parts/
  UI files setuptools would not ship: the five under ui/app/views/parts/
  ```

- **Why it matters**: this is **F12 again**, in the same file, guarded by a test written for F12.
  F12 was *"a built wheel shipped only index.html"*; the fix was a glob per level, and a glob per
  level is a rule that must be re-obeyed every time the tree grows — which is the same shape as
  the defect. It also says something sharper about verification: `make qa` was green at 1637
  tests, the release's own artefact check was green at 45/45, and both were measuring a wheel the
  container never builds.
- **Disposition**: **fixed in v0.15.4** (#251). One recursive glob replaces the per-level list; the
  matcher now expands globs the way setuptools does, through one function both it and its
  guard-on-a-guard call, so reverting it turns both red; and a new guard **builds a wheel from a
  Docker-shaped context and looks inside it**, which is the only one of the three that does not
  depend on reasoning about globs at all. That context is now derived from the Dockerfile's own
  `COPY` lines rather than a hand-written list, and refuses to run if either completing file is in
  it — the one way this guard could go quiet is somebody making its context "more realistic".

  `MANIFEST.in` keeps `graft src`: an sdist that could not rebuild the wheel would be the worse
  defect. What had to change is that the *guard* builds without it.

## F86 — the reveal button ate the password field, and the card's own CSS did it

- **What**: on the sign-in card the password input rendered **18 px wide (5 % of the row)** while
  the reveal button took **100 %**, and the two sat 8 px out of vertical alignment. An operator
  could not see the password as they typed it.
- **Cause**: `.login-card input` and `.login-card button` — element selectors, descendant
  combinator — written when the card held exactly one input and one submit button, so "every
  button in this card" and "the card's submit button" were the same set. v0.15.3 composed a
  `PasswordInput` into the card: an input and a reveal button nested in `.pw-field > .pw-row`. The
  descendant selector reached them. `width: 100%` on an item that is also `flex: none` is a base
  size with **shrink factor 0**, so the button claimed the whole row and refused to give any back;
  `.pw-row input`'s `flex: 1` (basis 0) collapsed to nothing beside it. The 8 px was `margin-top`
  on the button against `margin-bottom` on the input, neither intended for a row.
- **Measured** in Chromium at two widths, before and after:

  ```
                 input     button   misaligned   spellcheck
  before  1440   18 px     330 px      8 px        "true"
  before   390   18 px     308 px      8 px        "true"
  after   1440  298 px      28 px      0 px        "false"
  after    390  276 px      28 px      0 px        "false"
  ```

- **Why it matters, beyond the screen**: the stylesheet said the opposite of what it did. The
  comment above `.pw-row` read *"the input keeps the full width the login card gives it"* — an
  intention the CSS never delivered, sitting four lines from the rule that broke it. And the shape
  is **F85's, in CSS**: a rule whose meaning silently widens every time the tree beneath it grows.
  `>` says what it always meant and keeps saying it when the card grows again.
- **Disposition**: **fixed in v0.15.5** (#252). `.login-card > input` / `.login-card > button`; the
  reveal button carries `order: -1` so the icon sits to the left of the field as asked, while
  staying **after** the input in the markup — tabbing out of the username box has to land in the
  password box, not on a toggle. Guarded by
  `test_no_card_styles_a_bare_element_it_does_not_own`, which is scoped to containers that compose
  other components: a leaf styling its own single control is not the defect.

## F87 — one click in three did nothing, and the control never said what it was

- **What**: the theme control took two clicks to change the theme, and its label was wrong the
  whole time.
- **Two independent causes**, and the second is the one that made the first unreadable:

  1. **Three states, two appearances.** The ring was `dark -> light -> system -> dark`. "system"
     is not an appearance, it is a deferral, and it always resolves to one of the two beside it —
     so one transition in every three was a no-op on screen. No ordering of three states over two
     appearances avoids this.
  2. **The control never re-rendered.** `TopBar` read the theme from a **cookie**, which Preact
     cannot observe, and relied on `forceRepaint()` to nudge it. That helper called
     `store.setConnection(store.get().connection)` — passing the setter its own current value, and
     `setConnection` returns early when the value is unchanged. It published nothing. The icon and
     the label were frozen at first render; the only thing moving was `data-theme`, which `apply()`
     writes straight to the document root, never through the framework. The helper also stamped a
     `data-theme-tick` attribute that **nothing has ever read** — not the CSS, not a test.

- **Measured**, six clicks in Chromium at `prefers-color-scheme: light`:

  ```
  before   click 1 dark   click 2 light   click 3 light  <- dead   ... label: "Theme: system." throughout
  after    click 1 dark   click 2 light   click 3 dark            label tracks the state, every click
  ```

- **Disposition**: **fixed in v0.15.5** (#252). The control is a toggle: `nextTheme` returns
  whichever appearance the operator is **not** looking at, resolving "system" through
  `prefers-color-scheme` first. It is now a class component holding its own state — Preact core is
  vendored without hooks (ADR #174), so state means a class — and `forceRepaint` and its unread
  attribute are deleted. `system` remains the default an absent cookie means, and its icon still
  shows until the first click; it is no longer a stop on the ring, and returning to it means
  clearing `ncn_theme`. **That is a deliberate loss**: three states need a menu, and a menu is a
  design decision rather than a bug fix.

## F88 — the password field asked the browser to spell-check the password

- **What**: `<input type="password" … spellcheck="true">` in the shipped DOM, on both password
  fields and the username field, while every source file said `spellcheck="false"`.
- **Cause**: `spellcheck` is an IDL **boolean**. Preact assigns the property, and the non-empty
  string `"false"` is truthy, so the property became `true` and the attribute reflected `"true"` —
  the exact opposite of what the source said. Only `spellcheck=${false}`, the boolean, renders
  `spellcheck="false"`.
- **Why it matters**: spell-checking a password field means some browser builds send its contents
  to a remote spell-check service. It is also the flattest possible illustration of this
  repository's recurring lesson: **the source and the artefact disagreed, and reading either alone
  could not tell you.** It was visible in the served HTML for two releases.
- **Disposition**: **fixed in v0.15.5** (#252), and guarded by
  `test_no_enumerated_dom_attribute_is_passed_as_the_string_false`, which names `draggable` and
  `contenteditable` alongside it because they carry the identical trap.

## F89 — the second move out of a situation records its event and loses its label

- **What**: `feedback` is `UNIQUE (situation_id, verdict)` (F36). A `move` writes its negative half
  through `engine.apply_feedback` as a `split` carrying the departed alarm, so the **first** move
  out of a situation inserts a label and every later one inserts nothing. The gesture is recorded
  in full — `situation_event`, both membership snapshots, the confidence, the provenance — and the
  corpus grows by one row where the operator made two assertions.
- **Measured** (`tests/test_lifecycle.py`, two moves out of one situation):

  ```
  gesture 1   situation_event +1   feedback +1   feedback_id recorded on the event
  gesture 2   situation_event +1   feedback +0   feedback_id NULL
  ```

- **Why it matters**: `asserting_bags` is the quantity this release exists to move, and it counts
  labels. A busy operator restructuring one storm five times contributes one asserting bag, not
  five, so the census under-counts exactly the population that works hardest.
- **Disposition**: **FIXED in v0.16.1**, and by answering the question rather than by dropping the
  index. `PREREGISTRATION-0.16.1.md` §2, ratified before the repair, registers a bag's identity as
  `(situation_id, verdict, bag_key)` where `bag_key` is a digest over the member **set**; migration
  `0015` adds the column and widens the index. The second move now records its own label, and the
  same reproduction with the bag left **unchanged** between the two posts still records one — so
  F36's measured defect (N identical posts, N learning effects) stays fixed exactly where F36
  measured it. Both directions are in `tests/test_bag_identity.py`. The bound traded away is named
  in the amendment rather than discovered later: the cap on one situation's influence moves from
  *two applications* to *one per verdict per distinct membership*.

## F90 — the judge reconstructs the marked set positionally, from live membership

- **What**: `engine/evaluation/promotion_metrics.py::_asserting_bags` rebuilds the operator's
  marked set as `members[:excluded_reconciled]` — the first *n* alarm ids of the situation's
  **current** membership — while the ids the operator actually marked are stored, verbatim, in
  `feedback_exclusion`. Its docstring states the premise that would make this sound
  (*"the positions are the first `excluded_reconciled` members the server itself reconciled"*);
  `Exclusion.marked_positions` returns the positions where the marked ids **occur**, which is not a
  prefix and was never claimed to be. Second half of the same defect: the bag is read from
  `situation_alarm` (live) rather than `feedback_member(source='server')` (the snapshot the label
  was captured against), and `store.situation_members` has no `ORDER BY` while this reader has
  `ORDER BY alarm_id`, so the two orders are not even the same order.
- **Reproduce**: one bag of eight, two members marked, once high and once low. The control is the
  low case — if it disagreed too, the probe would be measuring the harness.

  ```
  PROBE   bag [1..8]  operator marked [7, 8]   judge reconstructed [1, 2]   agree False
  CONTROL bag [1..8]  operator marked [1, 2]   judge reconstructed [1, 2]   agree True
  ```

- **Why it matters**: `AssertingBag.marked` is the input to `asserted_negative_respected_rate`, the
  fourth named quantity of `PREREGISTRATION-0.10.0.md` §2.6(d) and an input to the promotion gate.
  A wrong marked set does not make that quantity noisy — it makes it a measurement of **different
  pairs than the operator asserted**, and it will read as a rate rather than as an error. It has
  been invisible since v0.9.1 for the reason this release exists: `asserting_bags = 0`, so nothing
  has ever reached it. **v0.16.0 is what makes it reachable.**
- **Disposition**: **FIXED in v0.16.1**, under `PREREGISTRATION-0.16.1.md` §1, ratified first. The
  bag is `feedback_member(source='server')` ordered by `position`; the marked set is
  `store.reconciled_marks` — `feedback_exclusion ∩ that snapshot` — which is the same expression
  `reconciliation_drift` recomputes the stored count from, so the count and the set cannot drift
  apart. Re-measured after the repair: **overlap 12 of 12**, and the control (the same bag marked
  low) agrees as it did before, which is what distinguishes a repaired judge from a broken probe.
  The residual limitation — that *which* members a scoped labeller could not observe was never
  recorded — is **F93**, not this.

## F91 — the behaviour record drops a route it cannot address, without saying so

- **What**: `tests/behaviour_identity.py::_request` substitutes path parameters from the seeded
  database and then `if "{" in concrete: return` — the route is skipped with **no line in the
  record**. The harness already has a mechanism for naming what it does not drive (`NOT_DRIVEN`
  writes a `not-driven` line); this branch bypasses it.
- **Measured**, `tests/fixtures/behaviour-identity.txt` as shipped:

  ```
  admin      100 rows
  editor      99 rows      missing: DELETE /api/tokens/{tid}
  viewer      99 rows      missing: DELETE /api/tokens/{tid}
  anonymous   99 rows      missing: DELETE /api/tokens/{tid}
  ```

  Only `admin` can mint a token, so for the other three principals `self.token_id` is `None`, the
  path stays unfilled, and the route leaves no trace.
- **Why it matters**: the record's whole claim is *every route × four principals*. For this route it
  covers one, and the gap is indistinguishable from a route that does not exist. **An
  authorization change on `DELETE /api/tokens/{tid}` for a viewer, an editor or an anonymous caller
  would not move this file** — which is the one thing the record is for. The failure is the shape
  Appendix B names: a guard that stopped guarding, and a count that reads as coverage.
- **Disposition**: **FIXED in v0.16.1**, in its own commit and before any other diff in that
  release, so the three lines it adds are attributable to it and to nothing else. The branch now
  appends `not-driven (unfilled: tid)` rather than returning, and the record moved from
  `33fe1bc0b3193dcf694843c9305e93a085151114df82fe3f3c6ddf9cb2992f05` to
  `9b5a6e75d4d141e86b8fb28a07c65d78cdebd94b63058e0608dcb8e3283499d3` by **exactly three lines**,
  one per principal that cannot mint a token — which is the measurement above, arriving as a diff.
  The entry is kept, per this file's own rule: it is where the reproduction lives.

## F92 — the promotion-path guard names four modules, and the path has five

- **What**: `tests/test_simulation.py::test_no_promotion_path_module_mentions_a_ground_truth_field`
  scans a hand-written tuple — `promotion.py`, `judge.py`, `shadow_cv.py`, `evaluation_folds.py` —
  and calls it *"the four modules the gate actually reads"*. It is not:
  `engine/evaluation/promotion_metrics.py` computes all four of the named quantities the gate reads
  and is not scanned. The list has been hand-maintained since v0.14.0 and a module added to the
  promotion path afterwards joins it only if somebody remembers.
- **Measured**, as this release's eighth mandatory injection. The same defect, injected twice:

  ```
  promotion_metrics.py  + "# situation_key, the generator's own truth key"   3 passed  <- not seen
  promotion.py          + "Injected: situation_key, …"                        1 failed  <- seen
  ```

- **Why it matters**: the guard exists because the simulator knows every event's correct
  `situation_key`, and a label the machine produced may not judge the machine
  (`PREREGISTRATION-0.14.0.md` §1). Its companion, `test_no_runtime_module_can_reach_the_simulator`,
  parses the whole tree and is unaffected — so an *import* is still caught everywhere. What escapes
  is the case this second test exists for: a truth field copy-pasted into a promotion-path module
  that imports nothing.
- **Disposition**: **FIXED in v0.16.1**, by deriving rather than by adding a filename.
  `promotion_path_modules()` walks out from `api/routes/promotion.py` — the module that computes
  the derived inputs and returns the verdict — and keeps every `engine/evaluation/` module the
  import graph reaches. Four became **seven**: `promotion_metrics.py`, `shadow_assertions.py` and
  `shadow_eval.py` join the original four, and a module added to the path afterwards joins without
  anyone remembering.
- **Why the walk stops at `engine/evaluation/`**, measured rather than assumed: the unrestricted
  transitive closure from the entry point is **112 modules**, and four of them mention `entity_key`
  legitimately — `store/entities.py` and three `engine/operate/` modules, where an entity key is a
  real domain concept and not the simulator's truth field. A guard over that set could never be
  green, which is exactly why the original was hand-written. The boundary is named in the test and
  the 112 is the reason it exists.
- A companion guard asserts the derivation is non-empty and names `promotion.py` and
  `promotion_metrics.py` by hand — the vacuity trap `test_the_preregistration_exists` exists for,
  applied to a walk that could silently return the empty set.

## F93 — which members a scoped labeller could not see is a count, so the judge picks the pair set

- **What**: `AssertingBag.hidden` needs alarm **ids**; the corpus stores only two counts —
  `scope_redacted_members` (how many the labeller could not observe) and
  `excluded_reconciled_out_of_scope` (how many of the *marks* were about one of those). The
  identities were deliberately never stored: `LabelScope.hidden_members` is transient by
  construction (v0.9.2, DECISIONS #137). So any reader has to *choose* which members were hidden,
  and `observable_pairs()` then enumerates a pair set that depends on the choice.
- **Reproduce**: a six-member bag, two marks, three hidden, one mark blind — §10's own measured
  case. The registered count is `(m − b) · ((n − m) − (h − b)) = 2`; the *identities* of those two
  pairs are not determined by anything stored, and two readers choosing differently would compute
  the same denominator over different numerators.
- **Measured**, on the reconstruction v0.16.1 replaced: it took the **last `h` members** of the
  bag as hidden, which puts zero hidden members inside the marked set whatever
  `excluded_reconciled_out_of_scope` records — so it computed `m · (n − m − h)` and agreed with
  the registered expression only where that column was 0. v0.16.1's `_hidden` honours `b` and the
  count is now exact; the selection is still arbitrary.
- **Why it matters**: `asserted_negative_respected_rate` is `kept / len(pairs)`. The denominator
  is now right for every row. The numerator counts pairs whose ends were **chosen** rather than
  recorded, so on a bag with a restricted scope the rate is one of several defensible values. It
  is a smaller error than F90 — the pairs are all genuinely `marked × rest` — and it is not zero.
- **Disposition**: open, and **not repaired by inventing a column**. Recording the hidden ids is a
  schema change to `0011`'s evidence boundary and an analytical decision about whether a redaction
  may leave a per-member trace on a label row — the redaction deliberately carries no alarm id
  (F47), and a table that recorded which ids were withheld would be the same disclosure written
  down. It belongs to a release that owns the evidence boundary, with a plan.

## F94 — MIGRATION.md has no row for the release that changed the state machine

- **What**: the upgrade table ends at `v0.15.4 → v0.15.5`. v0.16.0 added migration `0014`, renamed
  every situation status an operator sees (`open | closed | merged` → `new | open | resolved`) and
  rewrote `resolution` for every historical row — and the one document an operator reads before
  upgrading says nothing about it.
- **Reproduce**:
  ```sh
  grep -c '^| v' MIGRATION.md          # 25
  grep -n 'v0.16.0' MIGRATION.md       # nothing in the table
  ```
- **Measured**: 25 rows, the last naming v0.15.5, against a tree at v0.16.0 with fifteen
  migrations. The prose above the table — *"Two of twenty-five ask you to do something"* — was
  arithmetically correct, which is why nothing looked wrong: **the count matched the rows, and the
  rows were a release behind.**
- **Why it matters**: this is F78 recurring one release later, and F78's own repair added the
  sentence *"it counts rows, not sections; recount it when you add one"* — which counts what is
  present and cannot notice what is absent. A guard that checks a table against `docs/plans/
  releases.md` would; none exists.
- **Disposition**: **the two missing rows are added in v0.16.1** (v0.16.0's and this release's) and
  the arithmetic is restated. The *guard* is not written here: deriving the table's membership from
  the release chain is a change to `tests/test_documentation.py`'s subject, and this release's
  claim-marker check already owns that seam — recorded so the next release starts from a
  measurement rather than from a third recurrence.

## F95 — a UI guard attributed a write by filename, and the situation card moved

- **What**: `tests/test_security_ui.py::view_writes` decided which screen owns a component module
  by looking for `from "./{stem}.js"` — a **one-level, same-directory** import. It also built
  `component_sources` by concatenating every module under `app/views/` whose source contained the
  substring `from "./`, which is nearly all of them: the guard read as total and was total by
  accident rather than by derivation.
- **Reproduce**: split a writing component one directory deeper than its screen. v0.16.1 did
  exactly that, moving the situation card to `views/parts/card.js`:
  ```sh
  python -m pytest -q tests/test_security_ui.py
  ```
- **Measured**:
  ```
  app/views/parts/lifecycle.js issues writes but is neither a registry view nor imported by one,
  so no capability governs it. A write nothing owns is a write nothing gates.
  ```
  `situations.js` imports `./parts/card.js` and `card.js` imports `./lifecycle.js`, so the
  one-level scan found no owner for the module that issues **all five** operator gestures.
- **Why it matters**: it failed loudly here, which is the good case. The bad case is the mirror
  image and it was reachable: two of the three guards beside it read
  `views/situations.js` **by name** and asserted about the held card and the labelling payload
  contract — both of which moved. A guard keyed on a filename passes green on a file that no
  longer contains what it is asserting about, which is Appendix B's first trap in a third place.
- **The first repair widened it too far, and the injection that found that is this release's**:
  walking *every* local import made a screen's composed source reach `app/session.js`, where
  `can()` is **defined** — so every writing screen appeared to gate itself because a utility three
  hops away contained the string. Measured: deleting `classes.js`'s own `can("label.write")` left
  the whole suite **green**, which is the same class of defect one repair later.

  ```
  UI-5 injection, first repair   `const editable = true;`   56 passed   <- not seen
  UI-5 injection, bounded walk   `const editable = true;`   1 failed    <- seen
  ```

- **Disposition**: **FIXED in v0.16.1.** Ownership is the transitive closure of the import graph
  from each registry view, resolved by path and **bounded at `app/views/`**: a screen gates itself
  in its own module or in a part it owns, and a helper it imports gates nothing.
  `composed_source(view_id)` is what the three source-shape guards read. Issued as a finding rather
  than absorbed into the card split, because the defect is older than the split and would have
  outlived it — and the second measurement above is kept because a repair that had to be repaired
  is the more useful half of the record.

## F96 — `/favicon.ico` 404s on every page load, and the one-line fix is CSP-forbidden

- **What**: `index.html` declared no icon, so every browser asked for `/favicon.ico` and the
  appliance answered 404. Present at v0.15.5 and at v0.16.0; recorded in
  `docs/plans/v0.16.1-visualisation.md` §5 as *"harmless, pre-existing"*.
- **Reproduce**: load the console with the network panel open, or
  ```sh
  curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/favicon.ico   # 404
  ```
- **Measured**: one 404 per page load, per browser tab, forever. Harmless, and it is the kind of
  line that trains an operator to ignore the network panel.
- **Why it is not the one-liner it looks like**: the obvious repair is
  `<link rel="icon" href="data:image/svg+xml,…">`, and **this appliance's CSP forbids it**. A
  browser fetches a favicon as an image, `CSP` declares `img-src 'self'`, and a `data:` URI is not
  `'self'` — so that repair trades a 404 for a silent CSP violation and an icon that never appears.
  Nothing about the 404 says this, which is why it survived two releases as *"trivial"*.
- **Disposition**: **FIXED in v0.16.1**, as the only thing the policy permits: `ui/favicon.svg`,
  served from this origin through the same compile-time allowlist as every other asset, linked from
  `index.html`. The CSP is unchanged — `tests/test_security_ui.py::test_csp_is_unchanged_and_
  forbids_inline` still asserts that not one directive moved.

## F97 — a permalink to a situation the default tab excludes renders nothing at all

- **What**: `views/situations.js` opens on the **New** tab (DECISIONS #254) and a deep link
  (`#/situations/12`) calls `store.expand(sid)`. The card is expanded in the store and the list is
  filtered by tab, so a permalink to a situation in any state but `new` expanded a card the list
  did not contain: **no card, no error, no explanation** — an ordinary-looking New tab with the
  linked situation absent. The permalink exists to be *"shareable during an incident"*, and the
  most shareable situation is the one somebody has already touched, which is exactly the one that
  is no longer `new`.
- **Reproduce**, in a browser, with a control — a fresh context per case, because a hash-only
  navigation does not remount the screen and would hide it:
  ```
  CONTROL (status new)       #/situations/30 -> card present=True,  expanded=['30']
  TREATMENT (status open)    #/situations/31 -> card present=False, expanded=[]
  ```
- **Why it matters**: it is silent. Every assertion in this repository passes — the DOM harness
  drives views from captured payloads and never follows a permalink to a situation whose state
  disagrees with the tab, and no API is wrong. This is the sixth consecutive release in which a
  defect was visible only in a browser, and the seventh entry in this file whose reproduction is a
  screenshot rather than a stack trace.
- **Disposition**: **FIXED in v0.16.1**, and by the mechanism this release had already built for a
  neighbouring reason: a deep-linked situation is **pinned** (DECISIONS #267), so it appears where
  the operator is, carries its own state badge, and is released by collapsing it or choosing a
  tab. Introduced in v0.16.0 with the tabs; found here because a browser was driven, which is the
  only reason it was found at all.

## F98 — the API source corpus is a non-recursive glob behind a floor a subset already clears

- **What**: `tests/apisource.py` gives four text-scanning guards their corpus — F28 (no role
  comparison outside `rbac.py`), F34 (every mutating route below admin resolves scope), F39 (every
  mutating handler reaches the transaction helper) and the scorer-panel caveat. It builds that
  corpus from `PKG_DIR.glob("*.py")`, which does **not** descend, and asserts only that the result
  exceeds `MIN_SOURCE_CHARS = 60_000`. The seven non-route modules under `api/` are 74 640
  characters on their own, so moving the twelve route modules into a subdirectory would have
  dropped every route from the corpus and left all four guards green over source containing no
  routes at all. `MODULE_ORDER`'s "an unplaced module is an error" check does not see it either: it
  compares against the same non-recursive glob, so a module that moved out of the glob's reach is
  not unplaced, it is invisible.
- **Reproduce**, on the v0.16.1 tree:
  ```sh
  python -c "
  from pathlib import Path
  import netcorenoc.api
  d = Path(netcorenoc.api.__file__).resolve().parent
  flat = [p for p in d.glob('*.py') if not p.name.startswith('routes_')]
  print('machinery only:', sum(len(p.read_text()) for p in flat), 'chars; floor is 60000')"
  ```
- **Measured**: `machinery only: 74398 chars; floor is 60000`. The floor is cleared by 24 % with
  every route module absent.
- **Why it matters**: it is F92's shape one level up — a guard whose *scope* is written as a path
  expression that a legitimate refactor silently narrows. The four guards it feeds are the
  perimeter's text-level guarantees, and the release that moves the routes is exactly the release
  that would have retired them without one assertion going red.
- **Disposition**: **FIXED in v0.16.2** (DECISIONS #278), in the commit that moves the routes: the
  walk is `rglob`, and the floor is raised to a figure the machinery alone cannot clear. Found by
  reading every guard that names `api/` before moving anything, which is the only reason it was
  found — no test could see it.

## F99 — an integer severity above rank 4 renders as `low`, with no upper bound

- **What**: `engine/correlate/severity.py::_candidate_ranks` returns `kind="int"` with the varbind's
  **raw integer** as the rank when the values are not in the bundled vocabulary, bounded only by
  `SEVERITY_MAX_DISTINCT = 8` distinct values. Nothing constrains the magnitude, so an NE whose
  severity varbind reads 10, 20, 30 produces `severity_rank` 10, 20 and 30. `ui/app/format.js`
  matches ranks 0-2 by name and sends everything else to the `LOW` band, so all three render as
  *low* — including whichever one the appliance ranked most severe.
- **Reproduce**:
  ```sh
  python -c "
  from netcorenoc.engine.correlate.severity import _candidate_ranks
  print(_candidate_ranks({'10': 4, '20': 4, '30': 4}))"
  ```
- **Measured**: `('int', {'10': 10, '20': 20, '30': 30})` — ranks 10, 20 and 30, all three of which
  the console renders identically.
- **Why it matters**: a vendor that numbers severity 1-10 rather than by the bundled tokens gets a
  console that says every alarm is low, and the appliance's own ordering — which it validated
  against observed lifetimes before committing to it — is discarded at the last step. It is not
  reachable on any corpus scenario (0 of 2 252 alarms resolve a severity at all), which is why it
  has never been seen.
- **Disposition**: open, unfixed, and **deliberately so**. The repair is a normalisation from an
  arbitrary integer scale onto the five rendered bands, and deciding what that normalisation is —
  linear, rank-order, or an operator declaration — is v0.16.3's question about the same field.
  Issued in v0.16.2 (DECISIONS #276).

## F100 — an operator sees a raw OID for 46 of the 48 alarm classes a real corpus produces

- **What**: `alarm_class.name` is written from `known_oids.trap_name(oid)`, which answers only for
  the **standard** traps, and `alarm_class.vendor` from `known_oids.vendor_of(oid)`, which answers
  for any enterprise OID. `ui/app/format.js::alarmName` reads
  `class_label || class_name || class_oid` — the operator's label, then the standard-trap name, then
  the OID. There is no `class_label` until an operator writes one and no `class_name` for a vendor
  trap, so the third term is what an operator actually reads, and the vendor the appliance **has
  already resolved** appears nowhere in that chain.
- **Reproduce**: replay all ten corpus scenarios through a live appliance and read the table:
  ```sh
  python -c "
  import asyncio, sys; sys.path[:0] = ['tests', 'src', 'tools']
  from pathlib import Path
  from netcorenoc.store import Store
  import authutil, corpus_census, util
  async def main():
      store = Store('.demos/f100.db'); await store.open()
      engine, queue, _ = await authutil.make_env(store)
      for i, p in enumerate(sorted(Path('eval/corpus').glob('*.json'))):
          await util.drive(engine, queue, corpus_census.scenario_events(p, 1.7e9 + i * 3600))
      async with store.lock:
          cur = await store.conn.execute('SELECT vendor, name FROM alarm_class')
          rows = await cur.fetchall()
      print(len(rows), sum(r[1] is not None for r in rows), sum(r[0] is not None for r in rows))
      await store.close()
  asyncio.run(main())"
  ```
- **Measured**: **48 classes, 2 with a name, 46 with a vendor.** The two named are `linkDown` and
  `linkUp`. Every other class — Ciena, Huawei, Juniper, ZTE, Nokia, Cisco, Arista, H3C, Axis,
  Alcatel-Lucent — renders in the console as `1.3.6.1.4.1.2011.5.104.1`.
- **Why it matters**: it is not a storage defect and the columns are not unwritten — **that reading
  was checked and is wrong**, which is why this entry exists in this shape. It is a *fallback* whose
  last resort is reached 96 % of the time while a human-readable fact the appliance already holds
  sits one column away and is served to a different screen.
- **Disposition**: open, **not fixed here and deliberately not**. Two repairs are available — put the
  vendor into `alarmName`, or let an operator name the class — and choosing between them is exactly
  v0.16.3's question. Fixing the cheap one first would settle it by accident. Issued in v0.16.2.

## F101 — five of twenty test citations in `src/` named a test that does not exist

- **What**: a module that claims a property is checked cites the guard by name —
  `` `tests/test_store.py::test_x` `` — twenty times across `src/netcorenoc`. **Five of those
  twenty resolved to nothing.** The worst is `store/situations.py`, whose comment on `LIVE` has
  said since v0.16.0 that `tests/test_store.py::test_every_live_situation_query_uses_the_one_
  fragment` *"reads this module to assert nothing spells it out a second time"*. No such test
  existed anywhere in the tree, so the single-source property was a promise wearing a guarantee's
  clothes for two releases. Two more named the right test in the wrong file, one named a test that
  had been renamed, and one was correct but **wrapped across a line break** mid-identifier, so no
  reader and no tool could follow it.
- **Reproduce**:
  ```sh
  python -c "
  import ast, re
  from pathlib import Path
  cite = re.compile(r'tests/(test_[a-z0-9_]+)\.py::(test_[A-Za-z0-9_]+)')
  for p in sorted(Path('src/netcorenoc').rglob('*.py')):
      for mod, name in cite.findall(p.read_text()):
          f = Path('tests') / f'{mod}.py'
          names = {n.name for n in ast.walk(ast.parse(f.read_text()))
                   if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)} if f.is_file() else set()
          if name not in names:
              print('DANGLING', p, mod, name)"
  ```
- **Measured**, on the v0.16.1 tree: five dangling — `store/situations.py` (the `LIVE` guard, which
  never existed), `store/situation_events.py` × 2 (both live in other modules under other names),
  `engine/model/confidence.py` (wrong file *and* wrong name), and
  `crosscutting/rbac/tables.py` (correct, wrapped across two lines).
- **Why it matters**: it is #201's dangling-decision-pointer in a second register, and worse. A
  decision citation that dangles points at missing *reasoning*; a test citation that dangles points
  at a missing *check*, and a reader who follows it finds nothing while the property goes on being
  assumed. This is precisely the shape Appendix B calls *"where you write 'a test that guarantees
  X', ask whether you would know how to write that test"*.
- **Disposition**: **FIXED in v0.16.2.** The two guards `situations.py` promised are written; the
  three misdirected citations are repointed at the tests that exist; the wrapped one is reflowed.
  And `tests/test_documentation.py::test_every_test_cited_in_src_resolves_to_a_test_that_exists`
  is the sibling of the decision-citation guard, so the next one fails at review rather than
  surviving two releases — demonstrated red by a citation to a test that does not exist, with the
  restored tree as its control.

## F102 — two runtime paths were written as a count of `.parent`s, and moving a module broke one

- **What**: `api/routes_static.py` resolved the console's directory as
  `Path(__file__).parent.parent / "ui"`, with a comment from v0.7.2 explaining that the package
  split *"gains one `.parent` to resolve to the same directory"*. v0.16.2 moved that module one
  level deeper into `api/routes/` (DECISIONS #278), and the expression silently repointed at
  `src/netcorenoc/api/ui/` — a directory that does not exist. **Every static route answered 500 and
  the console did not load at all.** `store/types.py` computed `MIGRATIONS_DIR` the same way, from
  the same v0.7.3 reasoning, and was still correct only because nothing had moved it yet.
- **Reproduce**, on the v0.16.1 tree, without moving anything:
  ```sh
  python -c "
  from pathlib import Path
  import netcorenoc.api.routes_static as s
  print('would resolve to', Path(s.__file__).parent.parent.parent / 'ui', 'if this module moved one level deeper')
  print('actual UI dir:  ', s.UI_DIR, s.UI_FILE.is_file())"
  ```
- **Measured**: two such expressions in the runtime package, and no others. After the move, eight
  tests in `test_security_ui.py`, four in `test_behaviour_identity.py` and the whole static surface
  went red with `FileNotFoundError: .../api/ui/index.html`.
- **Why it matters**: a relative-parent count is a path written as a **number**, and it is wrong
  the moment the module moves — which is exactly when nobody is looking at it. Nothing in the move
  itself could see it: the imports all resolved, `mypy --strict` was clean, and only driving the
  real server found it. That is the same shape as F85 (the container's console missing five modules
  while every test was green).
- **Disposition**: **FIXED in v0.16.2.** Both resolve from `netcorenoc.__file__` — the package's own
  location, which is right from anywhere in the tree — and
  `tests/test_architecture.py::test_no_runtime_path_is_derived_by_counting_parents` refuses the
  third, demonstrated red by restoring the `store/types.py` form with the repaired tree as its
  control. One `.parent` is still allowed: a module's own directory is a fact about the file, not a
  count of steps to somewhere else.

## F103 — the tap-target floor excludes the one control an operator ticks most

- **What**: `style.css:247` reads
  `button, select, input:not([type="checkbox"]):not([type="radio"]) { min-height: var(--tap); }`.
  F81 installed `--tap: 28px` with the rule *"every interactive control is at least this on its
  short edge"*, and the selector excludes exactly the control that rule matters most for: the
  **member checkbox** in a situation's card, which is what decides the `excluded_ids` a partial
  split sends. Measured in a browser at 390 px, each renders **13 × 13 px** — less than a quarter
  of the floor's area, in a column of eight, in the gesture whose whole contract is that the
  appliance records only pairs a human actually marked.
- **Reproduce**, in a browser as an editor at 390 px, expanding any situation card:
  ```js
  [...document.querySelectorAll('input[type="checkbox"]')]
    .map(e => e.getBoundingClientRect())
    .map(r => `${Math.round(r.width)}x${Math.round(r.height)}`)
  ```
- **Measured**: `["13x13", "13x13", "13x13", "13x13", "13x13", "13x13", "13x13", "13x13"]` at all
  three widths, for the editor. A viewer is offered no checkboxes at all, which is why the
  measurement is role-specific.
- **Why it matters**: a mis-tick on a 13 px target is not a cosmetic annoyance — it writes a human
  judgement about a pair no human judged, which is exactly what
  `test_ui_invariants.py::test_a_partial_split_sends_exactly_the_marked_ids_and_no_others` exists
  to protect and cannot see. That guard asserts the client sends **what was ticked**; it says
  nothing about whether the operator could tick what they meant. Found in a browser, which is the
  eighth consecutive release in which that sentence is true.
- **Disposition**: open, **not fixed here**. Part VII rule 2 confines this release's console work to
  the severity pill, and the repair is a hit-area rule that belongs with v0.16.4's shell — where
  the row height, the checkbox column and the touch floor are one decision rather than three.
  Issued in v0.16.2.
