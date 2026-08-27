# NetCoreNOC documentation

NetCoreNOC is a zero-configuration SNMP trap correlator: one Python 3.12 asyncio process, one SQLite
(WAL) file, one static web console. Start at the project [`README.md`](../README.md) to get it
running in five minutes; come here when you have it running and have a question.

## The manual

| | |
|---|---|
| [`install.md`](install.md) | Docker, Compose, pip, Nix, systemd — and what each one gives you |
| [`configure.md`](configure.md) | Every environment variable, its default, and what changing it costs |
| [`operate.md`](operate.md) | First boot, the bootstrap admin, sending traps, reading a situation |
| [`console.md`](console.md) | The seventeen views and the question each one answers |
| [`correlation.md`](correlation.md) | How two alarms come to be linked, and how to read the breakdown |
| [`security.md`](security.md) | The posture, the perimeter, RBAC, visibility scoping, the audit chain |
| [`troubleshoot.md`](troubleshoot.md) | What breaks, and what the symptom looks like |
| [`architecture.md`](architecture.md) | The layers, the modules, the dependency rule, the three phases |

## The record

| | |
|---|---|
| [`findings.md`](findings.md) | Every finding issued and not closed, with its reproduction |
| [`adr/DECISIONS.md`](adr/DECISIONS.md) | Every decision, numbered, with the reason ([format](adr/README.md)) |
| [`ROADMAP.md`](ROADMAP.md) | Open items, with the measurement that makes each one an item |
| [`record.md`](record.md) | Where the deleted documentation went, and how to read it |

## What does not exist yet

[`plans/`](plans/) holds specifications for releases that have not been built. A document there
describes something you cannot run.

| | |
|---|---|
| [`plans/releases.md`](plans/releases.md) | **The single source of truth for what each release is** |
| [`plans/v0.15.1-package-tree.md`](plans/v0.15.1-package-tree.md) | 58 modules at the package root, and the axis question |
| [`plans/v0.15.2-console.md`](plans/v0.15.2-console.md) | What is broken in the console, measured |
| [`plans/v0.15.3-console-design.md`](plans/v0.15.3-console-design.md) | What v0.15.2 will leave undone |
| [`plans/cartridge.md`](plans/cartridge.md) | v0.16.0 — the external cartridge, and why it slipped |
| [`plans/archetypes.md`](plans/archetypes.md) | v0.17.0 — per-archetype weights, *likely, review before committing* |

## The analysis plans, and why they are not records

[`analysis/`](analysis/) holds four **pre-registered** analysis plans: what a release would measure
and what it would conclude **under every outcome**, written before the data existed and hash-guarded
since by `tests/test_preregistration.py`.

They are a live control, not history. v0.14.0 ran a simulated network to completion and returned
`INSUFFICIENT_EVIDENCE` because the corpus fell short of a floor registered in advance — that is
these documents working. A plan is **immutable once written**: editing one after seeing a result
turns the suite red, which is the whole mechanism. A disagreement with a registered plan goes into
[`findings.md`](findings.md) as an opinion for the next release, never into the plan.

The guard makes a plan immutable, not honest; that limit is stated in the test's own docstring.

## Conventions

- **A release writes no gate document, no scope document, no build report and no security review**
  (decision #197). Findings go to [`findings.md`](findings.md), five lines each. Decisions go to
  [`adr/DECISIONS.md`](adr/DECISIONS.md), six lines each. Everything else is a commit message and a
  `CHANGELOG` line. Working notes during a build are scratch files outside the repository.
- **No documentation build step.** Markdown that renders on GitHub. No `mkdocs`, no `sphinx`, no new
  dependency — principle 5 covers documentation tooling too.
- **The only irreversible act in this repository is a force-push or a history rewrite of `main`.**
  Everything else is recoverable, which is why deleting a document is cheap. See
  [`record.md`](record.md).
- **Moves preserve history** (`git mv`), and every internal cross-link is checked by
  `tests/test_structure.py::test_no_broken_relative_markdown_links`.

### Saying what a release is — the claim form

The repository once stated **two different answers** to "what is v0.8.0", four lines apart in
`ROADMAP.md`, and nothing noticed for a whole release. `tests/test_documentation.py` now enforces
that there is exactly one answer. It can only do that if a claim is detectable, so there are two
marked forms and they do different jobs.

**1. A release claim — what a release *is*.** One HTML comment, on its own line, directly above the
prose it formalises:

```
<!-- release-claim: v0.8.0 = operator-feedback-dataset -->
```

The key on the right must match the `claim` column of the release table in
[`plans/releases.md`](plans/releases.md), **which is the single source of truth**. If you believe a
release is something else, change the table — with a decision — and let the documents follow. Never
the other way round. The comment is invisible in rendered Markdown, so the prose beneath it stays
the thing a human reads.

**2. An element tag — what a spec *element* is planned for.**

```
## 1. The blessed ONNX path (`v0.16.0: planned`)
```

A document that carries a release claim may only tag elements for **that** release; that is what
catches a half-finished supersession, where a draft is retagged but one heading is missed. Backticks
are the convention and the guard reads them — between v0.7.4 and v0.7.5 it did not, and 31 % of the
repository's tags were invisible to it. **Only a fenced block is exempt**, like the two examples
above, which show the forms without asserting them.
