# Architecture Decision Records

[`DECISIONS.md`](DECISIONS.md) is NetCoreNOC's decision log: a **single, append-only, numbered**
journal of every scope-ambiguity resolution and notable engineering choice made during the
build, per the autonomous decision protocol.

## Format

Each entry is a numbered heading followed by four short parts:

- **Context** — the situation or ambiguity that forced a choice.
- **Options** — the alternatives considered.
- **Choice** — what was decided.
- **Reason** — why, including the trade-off accepted.

(Some entries add a **Subtlety** note where an edge case deserves the record.)

## Rules

- **One file, append-only.** New decisions are appended and numbered in sequence, continuing
  from the last entry. The log is grouped by version with a heading, but the numbering never
  restarts.
- **History is never renumbered or rewritten.** Entries 1–38 are the record as it was made; a
  later decision that supersedes an earlier one references it by number rather than editing it
  (e.g. #39 extends the deprecation window #34 set).
- **Security-relevant ambiguity resolves toward the stricter option**, and scope is never
  expanded to resolve ambiguity — both per the decision protocol.

## Why a single file rather than one file per ADR

The classic `adr/NNNN-title.md` layout fragments a log that reads best as a chronological
narrative — the entries reference one another (`DECISIONS #34` → `#39`) and share running
context. Keeping the 38 existing entries together as one append-only log preserves that
narrative and its cross-references; splitting them would be churn without benefit (the
anti-overengineering rule the project holds itself to). New entries continue in the same file.

## Range

Entries **1–38** cover v0.1.0–v0.4.0. The v0.5.0 (organization/structure) release continues from
**#39** — the only behaviour-adjacent one is #39, extending the legacy `OPTICORR_*`
environment-alias deprecation window from v0.5.0 to v0.6.0 (a non-removal); the rest record the
reorg and packaging/deployment mechanics.
