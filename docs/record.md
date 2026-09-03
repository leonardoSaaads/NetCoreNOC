# Where the old documentation went

v0.15.0 deleted about 57 000 lines of `docs/` — the phase-gate evidence, the per-release scope
documents, the build reports, the security reviews, and the drafts for releases that have shipped.
**Every deleted file is at commit `3ecf237` on `main` and will be there forever.**

```sh
git show 3ecf237:docs/gates/v0.14.0-phase-7.md          # read one
git ls-tree -r --name-only 3ecf237 docs/                 # list them all
git log --diff-filter=D --name-only v0.14.0..HEAD -- docs/   # what this release removed
```

There is no ledger, no manifest and no index of removed files, deliberately: git already is one, and
a second copy would be the thing that goes stale. The reason is [decision #197](adr/DECISIONS.md).

## Reading a `docs/gates/…` citation in a docstring

**A `docs/gates/`, `docs/scope/`, `docs/releases/` or `docs/security/SECURITY-REVIEW-…` path named
anywhere in this tree is a path at `3ecf237`.** That is the one reading rule, stated here once
rather than by editing every citation.

`src/netcorenoc/` alone carries **44, across 32 files**, **deliberately not updated**: v0.15.0's
strongest check is that every file under `src/` is byte-identical to v0.14.0 except the version
string, and rewriting a docstring would forfeit it to fix a reference `git show` already resolves.
The same applies in `tests/`, `tools/` and `eval/`, where a citation is provenance for a measurement
rather than a dependency on a file.

## Reading a `netcorenoc.<module>` path in a docstring

**A `netcorenoc.<module>` path named in prose that does not resolve is a pre-v0.15.1 path, and
`git log --follow` resolves it.** That is the second reading rule, stated here once for the same
reason as the first and with the same defence: no guard can see these, and a guard that read module
paths out of docstrings would have to be kept green through every future move — a standing cost paid
to repair references that are already resolvable, and a second reason to edit a docstring during a
refactor (decision #229).

v0.15.1 moved 56 modules and rewrote every import that names one. It did not rewrite the paths
written in *sentences*, because the release's own reviewability rested on a content census showing
that a move changed a file's imports and nothing else. **Measured on this tree: 50** — 44 in `src/`,
5 in `tests/`, 1 in `eval/`, 0 in `tools/`. None is an import; `mypy --strict` passes over the whole
tree and the suite is green, so every one of them is a sentence rather than a dependency (F65, and
F70 for why the figure moved from the 67 that finding first recorded).

Where a deleted document was a **runtime** dependency — a test that *opened* it and asserted on its
contents, rather than citing it — the test changed with the file. There are five such places, and
each moved to the live document that now holds what the deleted one held:

| Test | Read | Now reads |
|---|---|---|
| `test_preregistration.py` | the four hashes' second home in the phase gates | this page |
| `test_simulation.py` | the registered seed in `v0.14.0-phase-6.md` | this page |
| `test_documentation.py` | the release table in `ROADMAP-0.8-TO-0.13.md` | [`plans/releases.md`](plans/releases.md) |
| `test_documentation.py` | a build report, for a claim's provenance | the release table, plus a red demonstration and its control |
| `test_governance.py` | *"not tenant isolation"* in three deleted documents | [`security.md`](security.md), `README.md`, `MIGRATION.md` |

The last one is the one that nearly slipped: it read `docs/scope/SCOPE-0.7.md`,
`docs/architecture/DESIGN.md` and `docs/security/threat-model.md`, and the deletion left it red.
That is the guard doing its job — the claim it protects is that an operator is told visibility
scoping is not tenant isolation, and two of the three replacements had to be *written* rather than
repointed.

**The only irreversible act in this repository is a force-push or a history rewrite of `main`.**
That is the whole preservation rule, and it is in [`CONTRIBUTING.md`](../CONTRIBUTING.md).

**Four links inside `analysis/` point into the deleted directories and will never be repaired.**
`PREREGISTRATION-0.9.0.md` names `../gates/v0.9.0-phase-1.md`, and the v0.10.0 and v0.11.0 plans name
three specification drafts — and every one of those files is hash-pinned below, so editing a link
would change the plan's SHA-256 and turn `tests/test_preregistration.py` red. A link inside an
immutable document is a reference to the tree as it was; the reading rule above resolves it, and
`tests/test_structure.py` forgives exactly those four and no others.

## The pre-registration hashes

Five analysis plans in [`analysis/`](analysis/) are pinned by SHA-256 in
`tests/test_preregistration.py`. Each hash lives in **two** files on purpose: one alone could be
edited quietly in the same commit as the plan it guards; two make that an obviously deliberate diff.
The second home used to be the release's phase-gate document. Those are deleted, so it is this
section — [decision #204](adr/DECISIONS.md).

The first four hashes were **copied, not recomputed**, from the gate documents at `3ecf237`. There
is no gate document for the fifth: v0.15.0 deleted the directory, so `v0.16.0`'s hash was recorded
here and in the annotated tag, and this section is the only second home it has ever had.

| Plan | SHA-256 | Ratified in | Recorded at `3ecf237` in |
|---|---|---|---|
| `PREREGISTRATION-0.9.0.md` | `bb5bff851588837aa07f21c54b5301f7ada5fec3f8017a5ca4e9d7f7da2cbaef` | `553b827` | `docs/gates/v0.9.0-phase-1.md` |
| `PREREGISTRATION-0.10.0.md` | `c03aef0181554c0c71482e57d03677f25964c3a5ac20a7bf1b1d74bff1ba1e01` | `6b1c73a` | `docs/gates/v0.10.0-phase-0.md` §4 |
| `PREREGISTRATION-0.11.0.md` | `e011ee6ad2367d44f2ede14cad7b072df598298f91ecc1a405744358b589d449` | `78faace` | `docs/gates/v0.11.0-phase-0.md` §1 |
| `PREREGISTRATION-0.14.0.md` | `5607328a573d9a3c78374e47ba11e6dcff76f07c023b3f2e8174b6feed4d219f` | `4aed642`, tag `v0.14.0-gate0` | `docs/gates/v0.14.0-phase-0.md` §6 |
| `PREREGISTRATION-0.16.0.md` | `81aadc3b7a0695c0a6221a8302fb4e4e591f800a1cceeb89e6a52cca8ecca448` | `b00d231`, tag `v0.16.0-gate0` | — (the gates are deleted; see above) |

The temporal claim — that each plan was written before the results it governs — never rested on the
gate document. It rests on the ratifying commit, which changed nothing else and is permanent.
`v0.14.0-gate0` and `v0.16.0-gate0` each carry their plan's hash in the tag's own annotation,
independently of any file.

## The simulated network's seed

`PREREGISTRATION-0.14.0.md` §5.1 registers *"a fixed seed recorded in the gate"* without writing the
number, so the gate document was the only place it existed outside the code. Same discipline, same
move: the seed is **`20140000`**, copied from `docs/gates/v0.14.0-phase-6.md` §1 at `3ecf237`, and
`tests/test_simulation.py::test_the_seed_is_the_registered_one` reads it here.

## The frozen `eval` output

`python eval/harness.py | sha256sum` is
`c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26` and has been since v0.7.0. It
depends on the exact contents of `eval/corpus/`, which is why that directory is never edited to tidy
it.
