# Where the old documentation went

v0.15.0 deleted about 57 000 lines of `docs/` — the phase-gate evidence, the per-release scope
documents, the build reports, the per-release security reviews, and the specification drafts for
releases that have shipped. Nothing was lost. **Every deleted file is at commit `3ecf237` on `main`
and will be there forever.**

```sh
git show 3ecf237:docs/gates/v0.14.0-phase-7.md          # read one
git ls-tree -r --name-only 3ecf237 docs/                 # list them all
git log --diff-filter=D --name-only v0.14.0..HEAD -- docs/   # what this release removed
```

There is no ledger, no manifest and no index of removed files, deliberately: git already is one, and
a second copy would be the thing that goes stale. The reason is [decision #197](adr/DECISIONS.md).

**The only irreversible act in this repository is a force-push or a history rewrite of `main`.**
That is the whole preservation rule, and it is in [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## The pre-registration hashes

Four analysis plans in [`analysis/`](analysis/) are pinned by SHA-256 in
`tests/test_preregistration.py`. Each hash lives in **two** files on purpose: one alone could be
edited quietly in the same commit as the plan it guards; two make that an obviously deliberate diff.
The second home used to be the release's phase-gate document. Those are deleted, so it is this
section — [decision #204](adr/DECISIONS.md).

The hashes below were **copied, not recomputed**, from the gate documents at `3ecf237`.

| Plan | SHA-256 | Ratified in | Recorded at `3ecf237` in |
|---|---|---|---|
| `PREREGISTRATION-0.9.0.md` | `bb5bff851588837aa07f21c54b5301f7ada5fec3f8017a5ca4e9d7f7da2cbaef` | `553b827` | `docs/gates/v0.9.0-phase-1.md` |
| `PREREGISTRATION-0.10.0.md` | `c03aef0181554c0c71482e57d03677f25964c3a5ac20a7bf1b1d74bff1ba1e01` | `6b1c73a` | `docs/gates/v0.10.0-phase-0.md` §4 |
| `PREREGISTRATION-0.11.0.md` | `e011ee6ad2367d44f2ede14cad7b072df598298f91ecc1a405744358b589d449` | `78faace` | `docs/gates/v0.11.0-phase-0.md` §1 |
| `PREREGISTRATION-0.14.0.md` | `5607328a573d9a3c78374e47ba11e6dcff76f07c023b3f2e8174b6feed4d219f` | `4aed642`, tag `v0.14.0-gate0` | `docs/gates/v0.14.0-phase-0.md` §6 |

The temporal claim — that each plan was written before the results it governs — never rested on the
gate document. It rests on the ratifying commit, which changed nothing else and is permanent.
`v0.14.0-gate0` carries its plan's hash in the tag's own annotation, independently of any file.

## The frozen `eval` output

`python eval/harness.py | sha256sum` is
`c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26` and has been since v0.7.0. It
depends on the exact contents of `eval/corpus/`, which is why that directory is never edited to
tidy it.
