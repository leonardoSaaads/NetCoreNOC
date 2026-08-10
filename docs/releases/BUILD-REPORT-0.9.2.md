# Build report — v0.9.2, "the evidence boundary"

## The number this release exists to move

**On the richest corpus this repository can construct: zero rows disagreed, and the corpus-wide
asserted-negative total was 2 before and 2 after.**

```
A. the richest corpus this repository can construct
  label rows where reported != reconciled : 0
  marks that difference accounts for      : 0
  asserted negative pairs, v0.9.1 formula : 2
  asserted negative pairs, v0.9.2 formula : 2
  scope populations                       : {'clean': 0, 'checked': 0, 'unknown': 1}
```

That is the honest headline and it is the *less* interesting half. Here is the same measurement on
the corpus Gate 0 §1 built over HTTP, as an ordinary `editor`, rebuilt against the corrected tree:

```
B. Gate 0 §1's hostile corpus, rebuilt against the CORRECTED tree
  label rows where reported != reconciled : 2
  marks that difference accounts for      : 542
  asserted negative pairs, v0.9.1 formula : -259,084
  asserted negative pairs, v0.9.2 formula : 112
  client-reported marks / reconciled marks: 558 / 16
```

**−259 084 → 112.** The 112 is the eight honest labels asserting `8 × 2 × 7` pairs, which is what
they actually assert. The two hostile labels contributed `−260 096` and `+900` before; they
contribute **nothing** now, because neither asserted anything, and the corpus says so out loud —
two rows disagreeing, 542 marks that named nothing.

**The repair is prospective.** No deployed corpus has these rows, because the shipped UI sends only
ids it rendered. That is a fact about **sequencing**, not a property of the system: the write path
accepted the hostile label from an ordinary `editor`, over HTTP, in one request. Nothing prevented
it; nothing had yet tried.

---

## What shipped

| | |
|---|---|
| Findings issued | **F46**, **F47** — the next is F48 |
| Migrations | one, `0011`, additive and forward-only |
| ADRs | #131–#139 |
| Behaviour changes | **four**, enumerated in `SCOPE-0.9.2.md` §2, all in reports |
| New routes / capabilities / audit actions / served paths | **zero** |
| New runtime dependencies | **zero** — still five |
| `engine.py` | **untouched**, 569 lines |

---

## The verification

| Check | Result |
|---|---|
| `make eval` | `c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26` — byte-identical across two processes, and unchanged since v0.7.0 |
| `mypy --strict` | clean, 146 files |
| `ruff check` / `format --check` | clean, 330 files |
| dead-code gate | clean |
| `learn.penalize` | byte-identical, 20-case parity test |
| response status, body, timing | unchanged, asserted across four request shapes plus a timing test |
| upgrade from a database written by real v0.9.1 code | `0011` applies, data intact, audit chain to the **same** final hash, backfill correct and marked, no scope column populated |
| module-size guard | nothing over 400; **two modules split rather than the guard raised** |
| `DEBT_ALLOWLIST` / `COHESION_EXEMPT` | empty / one entry at 580, both unchanged |

### The guard demonstrations

| set | injections | caught |
|---|---|---|
| mandatory | 13 | **13** |
| mutation ledger, first run | 15 | **9** |
| mutation ledger, after the repairs | 15 | **15** |

Every case carries the diff that produced it, the verbatim red, the verbatim green, and a **named
control that passed under the injection**. The nine is the measurement; the fifteen is the receipt.
Six survivors, six repairs, all named in `docs/gates/v0.9.2-guard-demonstrations.md` §3 — including
two instances of the same mistake (*a test that calls the thing directly proves the thing works and
proves nothing about whether anything calls it*) and one partially-applied mutant that would have
been recorded as a survivor had the diff not been read.

---

## What this release does not claim

1. **The reconciled count does not mean the operator was right.** It means they marked members that
   existed in the bag.
2. **`m · (n − m)` on a restricted scope is not a count of observable pairs.** Gate 0 §3 measured 9
   reported, 8 reconciled, 2 joining two members the operator could see. The stored columns bound it;
   they do not state it.
3. **Every label written before `0011` is permanently uninterpretable for scope**, and is counted as
   `unknown` rather than assumed clean.
4. **Nothing here makes the corpus large enough to decide anything.** Sufficiency is v0.10.0's
   question, and this release chooses no floor and no floor unit.
5. **`app.js` is executed by no test.** The claim that the shipped UI cannot produce F46 or F47 is a
   claim about 52 KB of JavaScript that nothing runs — and it is the claim that keeps the measured
   corpus exposure at zero.

---

## Tagging, and a note for the maintainer

`git ls-remote --tags origin` returns **only `v0.7.3`**. There is no `v0.8.0`, `v0.8.1`, `v0.9.0` or
`v0.9.1` tag on the remote — the tag step has been missed for several releases. That is flagged here
so the missing tags can be recreated deliberately; this build does not attempt it.

When `v0.9.2` is tagged after a merge, point the tag at the **merge commit on `main`**: a rebase
orphans a tag created on the branch.
