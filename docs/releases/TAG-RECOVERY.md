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
| **`v0.10.0-gate0`** | `6b1c73a59e1dafc312f02a4d60e58093f575093a` — the commit that adds `docs/analysis/PREREGISTRATION-0.10.0.md` **and nothing else** (1 file, 512 insertions) | Phase 0 |
| **`v0.10.0`** | the merge commit on `main` | Phase 8 |

**`v0.10.0-gate0` is the one that matters and it is not ceremonial.** The pre-registration's entire
claim is that it was fixed *before* any result could be seen. `tests/test_preregistration.py` proves
the plan has not changed since its hash was recorded; it cannot prove *when* that was. The temporal
claim rests on the commit history, and an annotated tag on a single-file commit is what makes that
history addressable independently of any later branch, rebase or squash.

The tag already exists **locally**, created in Phase 0 on exactly that commit. It only needs
pushing:

```sh
# Phase 0 — created locally; the command that produced it, for the record:
git tag -a v0.10.0-gate0 6b1c73a59e1dafc312f02a4d60e58093f575093a \
  -m "v0.10.0 Gate 0 — PREREGISTRATION-0.10.0.md ratified, sha256 \
c03aef0181554c0c71482e57d03677f25964c3a5ac20a7bf1b1d74bff1ba1e01"

# Phase 8 — on the release commit, and AFTER the merge if the branch is rebased:
git tag -a v0.10.0 <merge commit on main> -m "v0.10.0 — the honest judge"

git push origin v0.10.0-gate0 v0.10.0
```

**Recreate `v0.10.0` after the merge**, pointing at the merge commit on `main`. A rebase rewrites the
branch's commits and orphans a tag created before it — which, on the evidence of §1, is the most
likely explanation for six of these having gone missing in the first place.

---

## 7. The tag v0.10.1 adds

| Tag | Points at | Created |
|---|---|---|
| **`v0.10.1`** | the release commit on the build branch, and **recreated on the merge commit after merging** | Phase 6 |

**One tag, and there is no gate tag.** v0.10.0 needed `v0.10.0-gate0` because its pre-registration's
whole claim is *fixed before any result could be seen*, and a hash proves content while a tag on a
single-file commit proves *when*. **v0.10.1 ratifies nothing.** It corrects an implementation and
appends to an append-only ledger, and neither carries a temporal claim that needs an anchor. Adding a
ceremonial gate tag would dilute the one place in this repository where a tag means something
specific.

```sh
# Phase 6 — on the release commit:
git tag -a v0.10.1 <release commit> -m "v0.10.1 — the corrections v0.10.0 earned"

git push origin v0.10.1
```

**Recreate `v0.10.1` on the merge commit after merging**, for the reason §6 gives and §1 measures: a
rebase rewrites the branch's commits and orphans a tag created before it, which is the most likely
explanation for six of these having gone missing in the first place.

### Verification, in the shape §5 uses

Each candidate is verified by **tree comparison** rather than by trusting the ref:

```
$ git rev-parse v0.10.0^{tree}
5d8a1786d175fd257e5c4315958a765b6dd2405d
$ git rev-parse v0.10.0^{commit}
fa82fa6b86dbdb395f67433de567df3b6bdf9560
```

`v0.10.0` and `v0.10.0-gate0` were **recovered into this build's clone** — the clone arrived with no
local tags at all, and neither tag exists on the remote — by fetching them from the `.git` directory
shipped alongside the v0.10.0 archive:

```sh
git fetch <path-to-v0.10.0-archive>/.git 'refs/tags/*:refs/tags/*'
```

That recovery is what made Gate 5's parity measurements possible: every *"identical to v0.10.0"*
claim in `../gates/v0.10.1-phase-5.md` is measured against a git worktree at the real `v0.10.0` tag,
not argued from a diff. **The tags being unpushed is not a bookkeeping detail — it is the difference
between a release that can be compared against and one that cannot.**

After tagging, the version check of §5 must print:

```
v0.10.1  ->  0.10.1
```

---

## 8. The two tags v0.11.0 adds

Neither can be pushed from the build environment (Appendix A: the repository is read-only to
automation), so both are recorded here with the others.

| Tag | Points at | Created |
|---|---|---|
| **`v0.11.0-gate0`** | `78faace` — the commit that adds `docs/analysis/PREREGISTRATION-0.11.0.md` **and nothing else** (1 file, 187 insertions) | Phase 0 |
| **`v0.11.0`** | `2234c3b` — the release commit on the build branch, and **recreated on the merge commit after merging** | Phase 8 |

**`v0.11.0-gate0` matters for the same reason `v0.10.0-gate0` did, and slightly more.** v0.10.0's
plan governed a verdict nothing acted on. v0.11.0's plan governs a **promotion**, and its §4 fixes
the two refusals *before* the approval path exists. The claim the release rests on is an ordering —
the refusal was specified first — and an ordering is a fact about history, not about content. The
hash guard proves the plan has not changed; only the tag on a single-file commit says *when* it was
fixed.

The tag already exists **locally**, created in Phase 0 on exactly that commit:

```sh
# Phase 0 — created locally; the command that produced it, for the record:
git tag -a v0.11.0-gate0 78faace \
  -m "v0.11.0 Gate 0 — PREREGISTRATION-0.11.0.md ratified, sha256 \
e011ee6ad2367d44f2ede14cad7b072df598298f91ecc1a405744358b589d449"

# Phase 8 — on the release commit, and AFTER the merge if the branch is rebased:
git tag -a v0.11.0 <merge commit on main> -m "v0.11.0 — champion/challenger"

git push origin v0.11.0-gate0 v0.11.0
```

**Recreate `v0.11.0` after the merge**, pointing at the merge commit on `main`, for the reason §6
gives and §1 measures: a rebase rewrites the branch's commits and orphans a tag created before it,
which is the most likely explanation for the historical tags having gone missing.

After tagging, the version check of §5 must print:

```
v0.11.0  ->  0.11.0
```

**Both tags exist locally and neither could be pushed.** `git push` returned **403 on both
attempts**, which Appendix A of the build prompt anticipates: *the repository is read-only to
automation*. The cap is two attempts and it was not routed around — no fork, no API write path.

A **verified `git bundle`** is produced alongside the tree so both tags and the whole history travel
with the archive rather than depending on a push that cannot happen:

```sh
git bundle create NetCoreNOC-v0.11.0.bundle --all
git bundle verify NetCoreNOC-v0.11.0.bundle
#   -> The bundle records a complete history.

# to recover, from a clone of the repository:
git fetch /path/to/NetCoreNOC-v0.11.0.bundle 'refs/tags/*:refs/tags/*' \
    'refs/heads/claude/netcorenoc-v0-11-build-78ysex:refs/heads/v0.11.0-build'
```

The bundle is **evidence alongside a report, never a substitute for one**: the push failure is
reported above in the terms Appendix A asks for.

---

## v0.12.0 — the instrument and the shape

**No Phase 0 tag this release.** There is no pre-registration to ratify: v0.12.0 fits nothing,
evaluates nothing and reads no holdout, so there is no result to register a plan against. Gate 0 is
`../gates/v0.12.0-phase-0.md`, and what it fences is a *measurement* (no test executes `ui/app.js`),
not a prediction.

```sh
# On the release commit, and AFTER the merge if the branch is rebased:
git tag -a v0.12.0 <merge commit on main> -m "v0.12.0 — the instrument and the shape"
git push origin v0.12.0
```

**Recreate `v0.12.0` after the merge**, pointing at the merge commit on `main`, for the reason §6
gives and §1 measures: a rebase rewrites the branch's commits and orphans a tag created before it.

**The Sync button in VS Code does not push tags.** `git push origin <tag>` — or
`git push --follow-tags` — is required. This is the most likely explanation for the historical tags
that went missing, and it is repeated here because it has already cost this repository once.

After tagging, the version check must print:

```
v0.12.0  ->  0.12.0
```

### The push, reported rather than routed around

`git push` returned **403 on both attempts**, which Appendix A of the build prompt anticipates: *the
repository is read-only to automation.* The cap is two attempts and it was **not** routed around —
no fork, no MCP write tool, no API write path, no alternate remote.

```
$ git push -u origin claude/netcorenoc-v0-12-build-ezb4sb
fatal: unable to access 'https://github.com/leonardoSaaads/NetCoreNOC/': The requested URL returned error: 403
```

The tag `v0.12.0` exists **locally only**, on `d78204d`. A **verified bundle** is produced alongside
the tree so the tag and the whole history travel with the archive rather than depending on a push
that cannot happen:

```sh
git bundle create NetCoreNOC-v0.12.0.bundle --all
git bundle verify NetCoreNOC-v0.12.0.bundle
#   -> The bundle records a complete history.

# to recover, from a clone of the repository:
git fetch /path/to/NetCoreNOC-v0.12.0.bundle 'refs/tags/*:refs/tags/*' \
    'refs/heads/claude/netcorenoc-v0-12-build-ezb4sb:refs/heads/v0.12.0-build'
```

The bundle is **evidence alongside a report, never a substitute for one**: the push failure is
reported above in the terms Appendix A asks for.

---

## v0.13.0 — the tag exists locally; the push returned 403 on both attempts

`git push` returned **403 on both attempts**, which Appendix A of the build prompt anticipates: *the
repository is read-only to automation.* The cap is two attempts and it was **not** routed around —
no fork, no MCP write tool, no API write path, no alternate remote.

```
$ git push -u origin claude/netcorenoc-v0-13-build-bi782x
fatal: unable to access 'https://github.com/leonardoSaaads/NetCoreNOC/': The requested URL returned error: 403
```

The tag `v0.13.0` exists **locally only**, on the tip of
`claude/netcorenoc-v0-13-build-bi782x`. A **verified bundle** carries the tag and the whole history
alongside the archive, so neither depends on a push that cannot happen:

```sh
git bundle create NetCoreNOC-v0.13.0.bundle --all
git bundle verify NetCoreNOC-v0.13.0.bundle
#   -> The bundle records a complete history.

# to recover, from a clone of the repository:
git fetch /path/to/NetCoreNOC-v0.13.0.bundle 'refs/tags/*:refs/tags/*' \
    'refs/heads/claude/netcorenoc-v0-13-build-bi782x:refs/heads/v0.13.0-build'
git rev-parse v0.13.0^{commit}   # == the branch tip; the tag is annotated
```

**No commit hash is quoted here, deliberately.** v0.12.0's entry named one, and a hash written
*into* the commit that records it cannot be correct — amending to fix the hash changes the hash.
The branch name and the tag name are stable; the hashes are one `git rev-parse` away and are
verified by the bundle rather than by a number in prose.

**The bundle is not committed.** A 3.5 MB pack of the repository, inside the repository, is a copy
that is stale at the next commit. The two commands above regenerate it.

The bundle is **evidence alongside a report, never a substitute for one**: the push failure is
reported above in the terms Appendix A asks for.

### After merging, recreate the tag on the merge commit

Unchanged from v0.12.0 and repeated because it is the documented cause of six historically missing
tags in this repository: **the Sync button in VS Code does not push tags**, and a rebase orphans a
tag that pointed at the pre-rebase commit.

```sh
git checkout main && git pull
git tag -a v0.13.0 -m "v0.13.0 — the UI" <merge-commit>
git push origin v0.13.0        # tags need their own push, always
```

---

## v0.14.0 — and `v0.13.0`, which was never pushed. **The seventh occurrence.**

### The measurement, on the tree this release starts from

```
$ git ls-remote --tags origin
92cbb455f7a421ced51847e64a7d8cf9be27a1f5	refs/tags/v0.12.0
e367a99319e2bbd510d8b4c7a62af9963a80e4fc	refs/tags/v0.12.0^{}

$ git tag -l
(empty — this clone, like v0.10.1's, arrived with no local tags at all)
```

**`v0.13.0` is absent from the remote.** §v0.13.0 above records the tag as existing *locally only*
after a 403; the clone this release starts from carries no local tags, so the tag now exists
**nowhere**. That is the seventh time a release in this repository has ended without an immutable
reference on the remote, and the cause is the one §v0.12.0 already names: **the Sync button in VS
Code does not push tags**, and a rebase orphans a tag created before it.

### Identifying the commit `v0.13.0` should carry, by tree comparison

Same method as §2, and the same four checks, because a branch name is a label and not evidence:

```
v0.13.0
  merge   c31817c88133280dd3025519b7e0656bd99009e7   (Merge pull request #16)
  parent2 f7b32c55ffc3268c375d72ed3c41864ea49b5693   (tip of claude/netcorenoc-v0-13-build-bi782x)
  tree(merge)   06320881a8ae4885c71bf6ed0eb2a9813ac0c57c
  tree(parent2) 06320881a8ae4885c71bf6ed0eb2a9813ac0c57c
  trees   IDENTICAL
  pyproject=0.13.0  __init__=0.13.0  changelog="## [0.13.0] - 2026-08-15 — \"the UI\""
```

**The control, and it is a real one.** `v0.12.0` is the one tag the remote still has, and this
method was not used to create it. The method predicts `v0.12.0`'s content is the tree shared by the
merge commit `e8de092` and its second parent `e367a99`; the remote tag dereferences to **`e367a99`**
and both trees are **`2528111d7015ca01ca01809e03e0225b223b7aaf`** — identical. The method agrees
with an answer it did not produce, and it also confirms §3's observation that **the maintainer's
tags point at the release-branch tip rather than at the merge commit**. Either captures the same
bytes; the commands below follow §4 and tag the merge commit, and substituting `f7b32c5` for
`v0.13.0` is equally correct if the maintainer prefers consistency with `v0.12.0`.

### The three tags this release adds to the backlog

| Tag | Points at | Created |
|---|---|---|
| **`v0.13.0`** | `c31817c` — the merge commit on `main` whose tree is `06320881…` (or `f7b32c5`, the branch tip, for consistency with `v0.12.0`) | never; recovered here |
| **`v0.14.0-gate0`** | the commit that adds `docs/analysis/PREREGISTRATION-0.14.0.md` **and nothing else** | Phase 0 |
| **`v0.14.0`** | the release commit on the build branch, and **recreated on the merge commit after merging** | Phase 10 |

**`v0.14.0-gate0` matters more this release than in any before it, and the reason is specific.**
v0.10.0's plan governed a verdict nothing acted on; v0.11.0's governed a promotion that never
happened. **v0.14.0's plan governs a corpus this release GENERATES**, and the one thing that
separates that from a manufactured result is that the generator's shape, its proportions, its seed,
its labelling rule and its stopping rule were all fixed *before* any verdict was seen. The hash
guard proves the plan has not changed. Only a tag on a single-file commit says **when** it was
fixed, and here that ordering is the whole of the release's honesty.

```sh
# Phase 0 — created locally; the command that produced it, for the record:
git tag -a v0.14.0-gate0 <the single-file commit> \
  -m "v0.14.0 Gate 0 — PREREGISTRATION-0.14.0.md ratified, sha256 <see ../gates/v0.14.0-phase-0.md>"

# The recovery of the tag that was never pushed:
git tag -a v0.13.0 c31817c88133280dd3025519b7e0656bd99009e7 \
  -m "v0.13.0 — the UI (merge commit on main; tree 06320881 verified against f7b32c5)"

# Phase 10 — on the release commit, and AFTER the merge if the branch is rebased:
git tag -a v0.14.0 <merge commit on main> -m "v0.14.0 — the model family"

git push origin v0.13.0 v0.14.0-gate0 v0.14.0     # tags need their own push, always
```

**No hash is quoted for `v0.14.0-gate0`**, for §v0.13.0's reason: a hash written *into* the commit
that records it cannot be correct, because amending to fix the hash changes the hash. The commit is
identified by its content — the only commit in this branch that touches
`docs/analysis/PREREGISTRATION-0.14.0.md` and nothing else — and by the plan's SHA-256, which is in
`../gates/v0.14.0-phase-0.md` and pinned by `tests/test_preregistration.py`:

```sh
git log --oneline --diff-filter=A -- docs/analysis/PREREGISTRATION-0.14.0.md
git show --stat <that commit>          # must be: 1 file changed, N insertions(+)
```

### Verification after the push

```sh
git ls-remote --tags origin | grep -c 'refs/tags/v[0-9]\+\.[0-9]\+\.[0-9]\+\^{}'
for v in v0.12.0 v0.13.0 v0.14.0; do
  printf '%s  ->  %s\n' "$v" \
    "$(git show "$v:pyproject.toml" | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)"
done
```

Every line must print the tag's own version. `v0.14.0-gate0` is deliberately **not** in that loop:
it points at a commit where `pyproject.toml` still reads `0.13.0`, which is correct — the gate
precedes the version bump, and a check that expected otherwise would be asserting that the fence
came after the work.
