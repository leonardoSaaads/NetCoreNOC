# SCOPE — v0.12.0

**The instrument and the shape.**

Before rewriting fifty-two kilobytes that no test executes, build the thing that would notice — and
decide the shape of what replaces it.

**This release changes no pixel of the UI.** Not `app.js`, not `index.html`, not `style.css`, not
`vendor/`. That is asserted by hash, not by intention: `tests/test_build_step.py::test_not_one_byte_of_the_shipped_ui_changed`.

Parent: [`../architecture/ROADMAP-0.8-TO-0.13.md`](../architecture/ROADMAP-0.8-TO-0.13.md).
Prerequisite recorded by: [`../adr/DECISIONS.md`](../adr/DECISIONS.md) #163, reopened by #167.
Evidence for everything below: [`../gates/v0.12.0-phase-0.md`](../gates/v0.12.0-phase-0.md).

---

## 0. Why this release exists, in three measured facts

**Fact A — no test executes `ui/app.js`.** Not "few". None. `app.js` was made unparseable by any
JavaScript engine and the full suite reported **1302 passed** (Phase 0 §2.3). Instrumented so that
any execution would leave a marker on disk, the suite produced no marker, while the same
instrumented file under `node` produced one immediately (§2.1–2.2). The three test files that
mention `app.js` read it as *text*.

**Fact B — the capability map is guarded by a string split.**

```python
block = app_js.split("const TABS", 1)[1].split("];", 1)[0]
```

`TABS` is the only client-side defence against an admin panel rendering for a viewer. A rewrite that
changed the *shape* of the map leaves that regex matching nothing, and
`test_admin_panels_are_gated_to_admin` has no vacuity check — it would report every admin panel
correctly gated against a map it had failed to read.

**Fact C — nothing guards principle 6.** A tracked `package.json`, three lockfiles, a
`vite.config.js` and a tracked `node_modules/` — **1302 passed** (§3). The one adjacent guard is
scoped to `src/netcorenoc/ui/`, which is the one directory a package manager would not use.

---

## 1. In scope

1. **A DOM harness** (`tests/domharness/`) that evaluates `ui/app.js` in a DOM, drives it against a
   stubbed HTTP surface **whose shapes are captured from the real server**, and lets a test assert
   what the user would see and what the client would send. Stdlib-only Node, no npm, no install
   step. Wired into `make qa`; reported separately by `make dom`.
2. **Five invariants** (`tests/test_ui_invariants.py`), each with a control, each stating what it
   does not cover.
3. **The `TABS` guard replaced** by per-role rendering cross-checked against `rbac.tables`, with
   the text-level guard **retained** and its reason written down (ADR #168).
4. **A principle-6 guard** (`tests/test_build_step.py`) derived from `git ls-files`, with a vacuity
   check and the tool-versus-product distinction stated.
5. **`../architecture/UI-0.13-DRAFT.md`** — every element tagged `v0.13.0: planned`, no
   implementation. The release's most durable output.
6. **The chain resequenced** (ADR #170): archetypes to v0.15.0, the cartridge to v0.14.0, both
   drafts retagged in place rather than deleted.

## 2. Explicitly out of scope, and why

| Not doing | Why |
|---|---|
| **Any change to `ui/`** | The safety net comes before the thing it protects. Asserted by hash. |
| **Any UI framework, vendored or written** | v0.12.0 **chooses** (ADR #171); v0.13.0 vendors. |
| **Archetypes** | A corpus that cannot decide one comparison cannot decide `k` of them (ADR #170). The draft is preserved and retagged. |
| **ONNX / the external cartridge** | Stays last. It is the largest trust decision in the chain. |
| **Any new route, capability or audit action** | This release adds no surface of any kind. |
| **Any migration or schema change** | Zero. The count stays `0001`–`0013`. |
| **Any new runtime dependency** | Five, unchanged since v0.2.0. Node is a **test** dependency (ADR #167). |
| **jsdom, Playwright, Puppeteer** | All require `npm install`, which is what principle 6 forbids and what Workstream 2 builds a guard against (ADR #167). |
| **Characterising layout** | Tab order, panel names, CSS classes, DOM structure, colours, copy. All of it is about to be deleted (ADR #168). |
| **Fixing anything the harness reveals** | A finding and a ROADMAP line. See §3. |

## 3. What the harness revealed and this release did not fix

Anti-overengineering rule 9: *no fix inside a move.* The harness's job is to notice things; noticing
them is not licence to change `ui/app.js`, which §1 forbids by hash.

* **The panel loaders have no capability check of their own.** Calling `renderPanel("audit")`
  directly as a viewer — what a deep link or a client-side router would do — issues no request, but
  for an **incidental** reason: `prunePanels` removed the container, so `clear(null)` throws before
  `api(...)` is reached. The outcome is right; the mechanism is a `TypeError`. v0.13.0 introduces
  routing, which is precisely when an accidental defence stops being available. Recorded in
  `../ROADMAP.md`, constrained in `../architecture/UI-0.13-DRAFT.md` §11, and asserted-with-its-reason
  in `tests/test_ui_invariants.py::test_a_panel_reached_without_its_capability_still_issues_no_request`.

**Declared intentional behaviour changes: zero.** The appliance behaves identically; only the test
suite grew.

## 4. The characterisation boundary

**Capture** — properties the replacement must honour:

| # | Invariant | Why it survives a rewrite |
|---|---|---|
| 1 | a role never renders a panel requiring a capability it lacks | security |
| 2 | a partial split sends exactly the ids the operator marked, and no others | the contract the v0.9.1 → v0.9.2 evidence chain rests on |
| 3 | a server-sent update mid-gesture does not destroy the click target | the v0.7.5 defect, by name |
| 4 | no render path writes unescaped data into the document | the reason `esc()` exists (F1) |
| 5 | a capability the client lacks produces **no request**, not a refused one | least privilege at the client |

**Do not capture** — tab order, panel names, CSS classes, DOM structure, colours, copy.

> If you are asserting a class name, you are describing what is about to be deleted.

## 5. What the harness does not cover, stated before anything green is reported

* **The entire visual layer.** No layout, no CSS cascade, no paint, no focus ring, no
  responsiveness. The harness DOM returns zeroes from `getBoundingClientRect()`.
* **The force-directed graph and the timeline SVG.** d3 is a recording double (ADR #167). These are
  the two largest render paths in the file and they are unexecuted by these tests.
* **Real browser semantics.** The harness DOM is a bounded, purpose-built implementation. Its
  fidelity is evidenced by its own conformance suite and by every invariant having been
  demonstrated red under an injected defect — not by a claim in a docstring.
* **Anything on a machine without Node.** The harness skips, loudly, with a reason naming the
  requirement. See §7.

## 6. Zero-config, unchanged

Nothing an operator does changes. No new environment variable, no new default, no new prompt, no
new file on disk. An operator upgrading from v0.11.0 has nothing to do and
[`../../MIGRATION.md`](../../MIGRATION.md) says exactly that.

## 7. The one-sentence test

> **Did a test executing `ui/app.js` in a DOM run, and how many?**

If the answer is "the suite was green", this release failed. A green suite over zero executed DOM
tests is worse than no harness, because it reads like coverage. `make dom` reports **executed**,
never collected; `run_scenario` refuses a result that carries no proof that `app.js` evaluated; and
the skip path is itself driven by a test that asserts it *reports*.
