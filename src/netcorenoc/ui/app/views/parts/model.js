/* What is deciding, in the three classes a scorer can be — the v0.14.0 half of the scorer screen.
 *
 * `UI-0.13-DRAFT.md` §8 planned a placeholder for this screen and specified its honesty:
 *
 *   > **The placeholder's honesty requirement**: not a greyed-out field. A **statement naming the
 *   > release that enables it and the reason** — *"Tree ensembles run out of process behind the
 *   > worker harness, which is v0.14.0. Not a limitation of the model: a limitation of what may run
 *   > inside this process."*
 *
 * **That sentence is false and this release is why.** Tree ensembles run *inside* this process, in
 * pure Python, with no worker harness and no new dependency — DECISIONS #183 supersedes the ROADMAP
 * line that said otherwise. So the placeholder is not rendered with corrected wording; there is
 * nothing left to place-hold, and the screen shows the model instead.
 *
 * ## The three classes, and why they are three
 *
 * A scorer's parameters are read differently depending on where they came from, and presenting all
 * three the same way would be the screen's own version of the defect below.
 *
 *   1. **`additive` — typed by an admin.** Five numbers in a form, tunable, hardening-only,
 *      previewable. `scorer.js` owns that form and this module does not duplicate it.
 *   2. **`logistic` — fitted from labels.** Coefficients, not settings. There is no form because
 *      there is nothing an admin should type: the numbers are a *result*, and a screen offering to
 *      edit them would be inviting someone to overwrite evidence with an opinion.
 *   3. **`tree` / `forest` / `gradient_boosting` — fitted, with hyperparameters.** The
 *      hyperparameters are chosen; the model is fitted. Both are inside `params_document` and
 *      therefore inside `params_hash`, which §8 registers as a constraint: *"two models with the
 *      same hash and different hyperparameters are indistinguishable, and the provenance v0.11.0
 *      built becomes fiction."*
 *
 * ## F60, which is why this module exists at all
 *
 * `GET /api/scorer` used to report the **coded additive defaults** whenever a model version was
 * active — `scorer_config` and `model_version` are mutually exclusive by a database CHECK, so with
 * an artefact running there is no config row and the route fell back. The console rendered that
 * fallback under the heading *"Active configuration"*. An operator with a promoted tree would have
 * read five weights that decided nothing, with nothing on the screen to say so.
 *
 * It is a **display** defect and it predates the tree kinds: a promoted `logistic` champion produced
 * it too. It needed a release whose subject was the model family to be noticed.
 */

import { html } from "../../dom.js";
import { SectionHeading, DataTable } from "../../widgets.js";
import { score, count, absolute, relative, timeTitle } from "../../format.js";

/** Kinds fitted from labelled evidence. Nothing here is typed by an admin. */
const FITTED = ["logistic", "tree", "forest", "gradient_boosting"];

/** The hyperparameters each kind carries, in the order an operator reads them. */
const HYPERS = {
  tree: ["criterion", "max_depth", "min_samples_leaf", "threshold"],
  forest: ["n_estimators", "mtry", "max_depth", "min_samples_leaf", "seed", "threshold"],
  gradient_boosting: ["n_rounds", "learning_rate", "max_depth", "min_samples_leaf", "threshold"],
  logistic: ["threshold"],
};

const LABELS = {
  criterion: "split criterion",
  max_depth: "maximum depth",
  min_samples_leaf: "minimum samples per leaf",
  n_estimators: "trees in the forest",
  mtry: "features considered per split",
  n_rounds: "boosting rounds",
  learning_rate: "learning rate (shrinkage)",
  seed: "draw seed",
  threshold: "link when the score exceeds this",
};

const DESCRIPTION = {
  additive:
    "Five numbers an admin typed. Every one of them is tunable below, hardening-only, and " +
    "previewable before it is applied.",
  logistic:
    "Coefficients fitted from labelled evidence. There is no form because there is nothing to " +
    "type: these are a result, not a setting.",
  tree:
    "One decision tree, fitted from labelled evidence at the hyperparameters below. Every " +
    "hyperparameter that changes the trained model is inside the parameter document, and " +
    "therefore inside the fingerprint.",
  forest:
    "An average over several trees, each fitted on a bootstrap sample. The draw seed is inside " +
    "the parameter document, so the fit is reproducible from the artefact alone.",
  gradient_boosting:
    "A sequence of small trees, each fitted on what the previous ones got wrong, summed with a " +
    "shrinkage constant.",
  custom:
    "A scorer this build does not recognise. Its identity and fingerprint are shown; nothing " +
    "else about it is claimed.",
};

/** Parse the artefact's document, or return null. A malformed document is never rendered as data. */
function parsed(version) {
  if (!version || !version.params_document) return null;
  try {
    const out = JSON.parse(version.params_document);
    return out && typeof out === "object" ? out : null;
  } catch {
    return null;
  }
}

/** How large the fitted structure is, in the terms each kind is actually made of. */
function structure(kind, document) {
  if (!document) return null;
  if (kind === "tree") return `${count((document.nodes || []).length)} node(s)`;
  if (kind === "forest") {
    const trees = document.trees || [];
    const nodes = trees.reduce((total, one) => total + (one || []).length, 0);
    return `${count(trees.length)} tree(s), ${count(nodes)} node(s) in total`;
  }
  if (kind === "gradient_boosting") {
    const trees = document.trees || [];
    const nodes = trees.reduce((total, one) => total + (one || []).length, 0);
    return `${count(trees.length)} round(s), ${count(nodes)} node(s) in total`;
  }
  return null;
}

/**
 * The banner above everything else on the scorer screen: **who is deciding right now.**
 *
 * Rendered for every kind including `additive`, so the answer to *"what is grouping my alarms"* is
 * in the same place whatever the answer is. A screen that showed this only for the unusual case
 * would be teaching an operator that the usual case needs no checking.
 */
export function Running({ running, degraded, degradedReason }) {
  const kind = running.kind;
  const version = running.model_version;
  const document = parsed(version);
  return html`<section class="panel-block param-structural">
    <${SectionHeading} title="What is deciding"
      hint=${"The scorer the engine is grouping with right now. Not what is configured — what is " +
             "running."} />
    <p>
      <b class="mono">${kind}</b>
      <span class="muted"> — ${running.scorer_id}, contract ${running.contract_version}</span>
    </p>
    <p class="hint">${DESCRIPTION[kind] || DESCRIPTION.custom}</p>
    <p class="mono">fingerprint ${running.params_hash}</p>

    ${degraded
      ? html`<p class="warnbox"><b>Degraded:</b> ${degradedReason} — running on the built-in
          defaults. What is shown above is what is running; the stored configuration is not.</p>`
      : null}

    ${version
      ? html`<${Artefact} version=${version} document=${document} />`
      : FITTED.includes(kind)
        ? html`<p class="hint">A fitted scorer is running with no model version behind it, which
            this build cannot produce. Its fingerprint above is the only provenance available.</p>`
        : null}

    ${!running.tunable
      ? html`<p class="warnbox"><b>The five parameters below are not what is deciding.</b> A model
          version is active, so the additive configuration is stored and inactive. Retuning it
          changes nothing until the active model version is rolled back.</p>`
      : null}
  </section>`;
}

/** The artefact's provenance and its hyperparameters, read from `params_document`. */
function Artefact({ version, document }) {
  const keys = HYPERS[version.kind] || [];
  const rows = keys
    .filter((key) => document && document[key] !== undefined)
    .map((key) => ({
      key,
      cells: {
        name: LABELS[key] || key,
        value: html`<code class="mono">${format(document[key])}</code>`,
      },
    }));
  const shape = structure(version.kind, document);
  return html`<div class="artefact">
    <h4>The artefact</h4>
    <p class="hint">Registered through the CLI. There is no HTTP route that creates a model
      version, which is deliberate: the thing that could put a new model in front of traffic is
      not reachable from the network.</p>
    <p class="mono">model version #${version.id} · ${version.kind} · ${version.params_hash}</p>
    <p class="hint">
      ${`Registered by ${version.created_by || "—"} at `}
      <span title=${timeTitle(version.created_at)}>${absolute(version.created_at)}</span>
      ${` (${relative(version.created_at)}). `}
      ${version.challenger_run_id == null
        ? html`<b>No challenger run behind it</b> — an artefact in that state can be registered but
            never applied.`
        : html`Challenger run ${count(version.challenger_run_id)}.`}
    </p>
    ${shape ? html`<p class="hint">Structure: ${shape}.</p>` : null}
    ${rows.length
      ? html`<${DataTable} columns=${[
          { key: "name", label: "hyperparameter" },
          { key: "value", label: "value" },
        ]} rows=${rows} />`
      : html`<p class="hint">This artefact's document carries no hyperparameter this build knows
          how to name. The fingerprint above still covers every byte of it.</p>`}
    ${document && document.base_value !== undefined
      ? html`<p class="hint">${"Attribution base value "}
          <code class="mono">${score(document.base_value, 6)}</code>${" "}
          — the model's mean output over
          the registered background set. Contributions sum to the score <b>minus</b> this, which is
          what makes the decomposition exact rather than approximate.</p>`
      : null}
  </div>`;
}

/** Numbers as numbers, strings as strings; nothing is coerced into a shape it is not. */
function format(value) {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : score(value, 6);
  return String(value);
}
