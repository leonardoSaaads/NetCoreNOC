# Tag recovery

**The repository is read-only to automation** (v0.10.0 build brief, Appendix A). This document is
executed by the maintainer, by hand, from a clone with push rights. Nothing in this project's gates
depends on it having been run.

**Why it is a Gate 0 item rather than a release-notes footnote.** v0.10.0's central discipline is a
pre-registered analysis plan whose guard is a **content hash**. A hash identifies content; it does
not make that content *findable*. A repository whose releases have no immutable reference on the
remote is one where "the plan as it stood at v0.10.0" is a claim about a branch that may be
rebased, force-pushed or deleted. The tags are what turn the hash guard from an assertion into a
recoverable fact.

---

## 1. What is actually missing, and how that differs from the brief

```
$ git ls-remote --tags origin
2fca2df68be68cf8e9d404aa6ba75597cc7f0b67	refs/tags/v0.7.3
8b2c1757b2977a126153e9b9389bd56253fb5d4a	refs/tags/v0.7.3^{}
f4299ffc495ffc41b61156ea9ebc037310b3f5da	refs/tags/v0.9.2
5a9b39c27f45d284803223d050a2dc3cef1df4a5	refs/tags/v0.9.2^{}

$ git tag -l
(empty — this clone carries no local tags at all)
```

**The brief says the remote returns only `v0.7.3`, and that `v0.8.0`, `v0.8.1`, `v0.9.0`, `v0.9.1`
and `v0.9.2` are missing. Two corrections, both measured:**

1. **`v0.9.2` now has a tag.** It was created after the brief was written. It points at
   `5a9b39c` — the **tip of the release branch**, not the merge commit. Nothing needs doing for it,
   and it is the control this document uses (§3).
2. **`v0.7.4` and `v0.7.5` are also missing**, which the brief does not mention. So the gap is
   **six** releases, not five, and it opens one release earlier than believed.

| Release | Remote tag | Action |
|---|---|---|
| v0.7.3 | present → `8b2c175` | none |
| **v0.7.4** | **absent** | **create** |
| **v0.7.5** | **absent** | **create** |
| **v0.8.0** | **absent** | **create** |
| **v0.8.1** | **absent** | **create** |
| **v0.9.0** | **absent** | **create** |
| **v0.9.1** | **absent** | **create** |
| v0.9.2 | present → `5a9b39c` | none |

---

## 2. How each candidate was identified, and how each was verified

**Identified** by walking `origin/main`'s first-parent history. Each release since v0.7.4 landed as
one `Merge pull request #N` commit, so the merge commit is the point on `main` at which that
release's content first existed on the default branch.

```
$ git log --oneline --first-parent origin/main
0d92544 Merge pull request #11 from leonardoSaaads/claude/netcorenoc-v0.9.2-evidence-tgxaot
d418852 Merge pull request #10 from leonardoSaaads/claude/netcorenoc-v0.9.1-build-cdkqes
fbccb92 Merge pull request #9  from leonardoSaaads/claude/netcorenoc-v0.9.0-build-pok0tf
a958cfd Merge pull request #8  from leonardoSaaads/claude/netcorenoc-v0.8.1-lifecycle-1w785w
85d5be7 Merge pull request #7  from leonardoSaaads/claude/netcorenoc-v0.8.0-build-ns2spr
dbf4cf1 Merge pull request #6  from leonardoSaaads/release/v0.7.5
b2ea0f7 Merge pull request #5  from leonardoSaaads/claude/netcorenoc-v0.7.4-build-zk0zlj
8b2c175 docs(v0.7.3): security review (no findings), v0.7.4 specification, release documentation
```

(`v0.7.3` predates the PR-merge pattern and sits directly on the first-parent line, which is why
its existing tag points at a non-merge commit.)

**Verified** four ways per candidate, because a branch name is a label and not evidence:

* **tree equality** — the merge commit's tree against its second parent's tree. Identical means the
  merge introduced no content of its own, so tagging either object captures the same bytes;
* **`pyproject.toml`** version at that commit;
* **`src/netcorenoc/__init__.py`** `__version__` at that commit;
* **`CHANGELOG.md`** top entry at that commit.

The brief asks for verification "against the local tagged commit's tree". **There are no local
tags**, so that comparison is not available and this is the equivalent that is: the merge commit and
the release-branch tip are compared to each other, and the three independently-maintained version
declarations are compared to the release name. A wrong candidate fails all four.

### The measurement, verbatim

```
v0.7.4
  merge   b2ea0f73fdf6bd46992af67a8290c69de7da5e9e
  parent2 e7046a1920e67a9bf9bcb5c88d231cdc80dfab01
  trees   IDENTICAL
  pyproject=0.7.4  __init__=0.7.4  changelog="## [0.7.4]"

v0.7.5
  merge   dbf4cf17ffb86a783e67bcfc5f046e0030d493aa
  parent2 636ab6d4ecaaf833507f830833cfb95e4202b660
  trees   IDENTICAL
  pyproject=0.7.5  __init__=0.7.5  changelog="## [0.7.5]"

v0.8.0
  merge   85d5be713e282a7989784f7afcec5d444a4fece4
  parent2 80ca1fc082c1ee61b1863f3d5beb6bfcf8d3a853
  trees   IDENTICAL
  pyproject=0.8.0  __init__=0.8.0  changelog="## [0.8.0]"

v0.8.1
  merge   a958cfddbc823e88a672f1b32f81f38e5a9b8b81
  parent2 dc3fb3be6d5474320c5ea716f914e7cbf38c4ec8
  trees   IDENTICAL
  pyproject=0.8.1  __init__=0.8.1  changelog="## [0.8.1]"

v0.9.0
  merge   fbccb9280c856b6285f1aebb7d7d535c35068865
  parent2 bb101068981c6e4d00f1079be821b32999cdd395
  trees   IDENTICAL
  pyproject=0.9.0  __init__=0.9.0  changelog="## [0.9.0]"

v0.9.1
  merge   d418852dc9709539230c2f83f25a9eb107be768e
  parent2 6b4a292cc65f87c84357863f2795b8af7563f856
  trees   IDENTICAL
  pyproject=0.9.1  __init__=0.9.1  changelog="## [0.9.1]"

v0.9.2                                    <- the CONTROL: this one is already tagged
  merge   0d92544ecc36bc16dead878cdc5fe87655c6dd20
  parent2 5a9b39c27f45d284803223d050a2dc3cef1df4a5
  trees   IDENTICAL
  pyproject=0.9.2  __init__=0.9.2  changelog="## [0.9.2]"
```

Reproduce it with:

```sh
for entry in v0.7.4:b2ea0f7 v0.7.5:dbf4cf1 v0.8.0:85d5be7 v0.8.1:a958cfd \
             v0.9.0:fbccb92 v0.9.1:d418852 v0.9.2:0d92544; do
  ver="${entry%%:*}"; c="${entry##*:}"
  [ "$(git rev-parse "$c^{tree}")" = "$(git rev-parse "$c^2^{tree}")" ] \
    && same=IDENTICAL || same=DIFFER
  printf '%s  %s  pyproject=%s\n' "$ver" "$same" \
    "$(git show "$c:pyproject.toml" | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)"
done
```

## 3. The control, and what it establishes

**`v0.9.2` is already tagged on the remote, and this procedure was not used to create it.** It is
therefore an independent answer against which the method can be checked, and the brief's Appendix B
rule — *a probe with no control proves nothing* — is why it is used that way rather than merely
skipped.

The method predicts that `v0.9.2`'s content is the tree shared by `0d92544` and `5a9b39c`. The
existing remote tag resolves to **`5a9b39c`**, and the two trees are **IDENTICAL**. The method
agrees with an answer it did not produce.

It also surfaces the one genuine discrepancy in this document, which is a **choice**, not an error:

> The maintainer's existing tags point at the **release-branch tip** (`v0.9.2` → `5a9b39c`;
> `v0.7.3` → `8b2c175`). The brief instructs that recreated tags point at the **merge commit on
> `main`**.

Both capture byte-identical trees, so nothing about the released content depends on the choice.
**§4 follows the brief and tags the merge commits**, for the reason the brief gives: a merge commit
is on `main`'s first-parent line permanently, whereas a branch tip is reachable only while the
branch or the PR ref survives — and the branch of a merged PR is routinely deleted. If the
maintainer prefers consistency with the two existing tags, substitute the `parent2` hash from §2;
the trees are identical either way and §5's verification passes unchanged.

## 4. The commands

Annotated tags (`-a`), so each carries an author, a date and a message, and so `git describe`
prefers them. **Run from a clone with push rights, on an up-to-date `origin/main`.**

```sh
git fetch origin main --tags

git tag -a v0.7.4 b2ea0f73fdf6bd46992af67a8290c69de7da5e9e \
  -m "v0.7.4 — the write perimeter, extracted (merge commit on main; tree verified)"
git tag -a v0.7.5 dbf4cf17ffb86a783e67bcfc5f046e0030d493aa \
  -m "v0.7.5 — the feedback path repaired (merge commit on main; tree verified)"
git tag -a v0.8.0 85d5be713e282a7989784f7afcec5d444a4fece4 \
  -m "v0.8.0 — the scoreboard: operator feedback as a durable dataset (merge commit on main)"
git tag -a v0.8.1 a958cfddbc823e88a672f1b32f81f38e5a9b8b81 \
  -m "v0.8.1 — governed dataset lifecycle (merge commit on main; tree verified)"
git tag -a v0.9.0 fbccb9280c856b6285f1aebb7d7d535c35068865 \
  -m "v0.9.0 — shadow mode: a challenger that acts on nothing (merge commit on main)"
git tag -a v0.9.1 d418852dc9709539230c2f83f25a9eb107be768e \
  -m "v0.9.1 — the informative label: partial split (merge commit on main; tree verified)"

git push origin v0.7.4 v0.7.5 v0.8.0 v0.8.1 v0.9.0 v0.9.1
```

**Do not pass `--force` and do not re-tag `v0.7.3` or `v0.9.2`.** Both already exist and both are
correct; moving an existing tag is the one operation that would make this situation worse than it
is.

## 5. Verification after the push

```sh
git ls-remote --tags origin | grep -c 'refs/tags/v[0-9].*\^{}'   # expect 8
for v in v0.7.3 v0.7.4 v0.7.5 v0.8.0 v0.8.1 v0.9.0 v0.9.1 v0.9.2; do
  printf '%s  %s\n' "$v" \
    "$(git show "$v:pyproject.toml" | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)"
done
```

Every line must print the tag's own version. A tag pointing at the wrong commit shows up here as a
version mismatch, which is the cheapest possible check and the one that would have caught this six
releases ago.

---

## 6. The two tags v0.10.0 adds

Neither can be pushed from the build environment, so both are recorded here with the others.

| Tag | Points at | Created |
|---|---|---|
| **`v0.10.0-gate0`** | the commit that adds `docs/analysis/PREREGISTRATION-0.10.0.md` **and nothing else** | Phase 0 |
| **`v0.10.0`** | the release commit | Phase 8 |

**`v0.10.0-gate0` is the one that matters and it is not ceremonial.** The pre-registration's entire
claim is that it was fixed *before* any result could be seen. `tests/test_preregistration.py` proves
the plan has not changed since its hash was recorded; it cannot prove *when* that was. The temporal
claim rests on the commit history, and an annotated tag on a single-file commit is what makes that
history addressable independently of any later branch, rebase or squash.

```sh
# Phase 0 — after the pre-registration commit, which changes nothing else:
git tag -a v0.10.0-gate0 <that commit> \
  -m "v0.10.0 Gate 0 — PREREGISTRATION-0.10.0.md ratified, sha256 <hash>"

# Phase 8 — on the release commit, and AFTER the merge if the branch is rebased:
git tag -a v0.10.0 <merge commit on main> -m "v0.10.0 — the honest judge"

git push origin v0.10.0-gate0 v0.10.0
```

**Recreate `v0.10.0` after the merge**, pointing at the merge commit on `main`. A rebase rewrites the
branch's commits and orphans a tag created before it — which, on the evidence of §1, is the most
likely explanation for six of these having gone missing in the first place.
