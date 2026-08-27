# The decision log

[`DECISIONS.md`](DECISIONS.md) is a single, numbered journal of every scope-ambiguity resolution and
notable engineering choice. One file rather than one file per record, because the entries reference
one another (`#34` → `#39`) and read best as a chronological narrative.

## Format

A numbered heading, then about six lines: **Decision** — what was chosen; **Reason** — why,
including the trade-off accepted; and the release. An entry may add a **Measured** bullet where a
number is what settled it, and that bullet is never dropped when an entry is condensed.

## Rules

- **Numbered in sequence, never renumbered.** Gaps are deliberate.
- **A later decision that supersedes an earlier one references it by number rather than editing it.**
  A project whose entries can be edited has entries nobody can cite.
- **An entry may be removed only when no code and no live document cites it**, and condensed only
  down to its decision, its reason and its measurements (#201). The full original text of every
  entry is at commit `3ecf237` — see [`../record.md`](../record.md).
- **Security-relevant ambiguity resolves toward the stricter option**, and scope is never expanded to
  resolve ambiguity.
