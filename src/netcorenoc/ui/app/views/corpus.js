/* Corpus: what capture is holding, in rows — and what it is for (v0.27.0: the judge reads it).
 *
 * `GET /api/dataset/retention` had no UI surface in v0.12.0 (draft §4), so an admin without shell
 * access to the appliance could not see what the feedback corpus cost or what window they
 * actually had. Zero-config means the default is good, not that the operator is blind about what
 * the appliance is storing.
 *
 * The tiers are editable on the Settings screen, where the destructive-preview machinery lives.
 * This screen reads; it does not offer a second place to destroy the same rows.
 */

import { html } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty, DataTable, SectionHeading, Stat } from "../widgets.js";
import { Bars } from "../compare.js";
import { corpusFact, count } from "../format.js";

/**
 * The row counts that are **counts of the same thing** and can therefore share an axis
 * (v0.16.6, DECISIONS #305).
 *
 * `stats` mixes two kinds: row counts, and two epoch timestamps plus a day count
 * (`sink_newest`, `sink_oldest`, `sink_window_days`). Charting all of them on one axis would put
 * `1 789 236 376` beside `1 868` and make every bar but one invisible — a chart that is pretty and
 * wrong about the very thing this screen exists to report. So the bars take the row counts only and
 * the tiles keep everything, which is the honest split rather than a prettier one.
 */
const ROW_COUNT_KEYS = (key) => !key.startsWith("sink_");

export class Corpus extends Loader {
  constructor(props) {
    super(props);
    this.what = "the corpus figures";
    this.loadingLabel = "Reading what capture is holding";
  }

  async load() { return get("/api/dataset/retention"); }

  view(data) {
    const stats = data.stats || {};
    const entries = Object.entries(stats);
    const empty = entries.every(([, value]) => !value);

    return html`<div class="corpusview">
      <div class="settings-intro">
        <p><b>The corpus is what the models learn from on this site.</b> Every time an alarm is
          grouped, the appliance keeps the evidence it scored; every time an operator confirms,
          corrects, moves, merges or answers a proposal, that judgement is kept beside it.</p>
        <p>The judge uses these labels every five minutes to re-rank the models, and site training
          uses them to adapt one. Nothing here is a gate: the models decide from the first trap.</p>
      </div>
      <${SectionHeading} title="What capture is holding"
        hint=${"Counts and dates only — never a row. Capture happens inside the engine, where " +
               "visibility scoping does not exist, which is why this screen is admin-only."} />

      ${empty ? html`<${Empty}
        title="Nothing captured yet."
        will=${"Every grouping the appliance makes is captured with its evidence, and every " +
               "operator judgement on it is kept beside it."}
        meanwhile=${"Confirm, split, move or merge a situation, or answer a Pending proposal: " +
                    "the judge reads the label within five minutes."} />`
        : html`<div class="stat-row">
          ${entries.map(([key, value]) => {
            const fact = corpusFact(key, value);
            return html`<${Stat} key=${key} label=${fact.label} value=${fact.text} title=${fact.title} />`;
          })}
        </div>
        <${Bars} title="What capture is holding, by rows" unit="rows"
          rows=${entries
            .filter(([key, value]) => ROW_COUNT_KEYS(key) && typeof value === "number")
            .sort((a, b) => b[1] - a[1])
            .map(([key, value]) => ({
              key,
              label: corpusFact(key, value).label,
              value,
              tone: value ? null : "warn",
            }))}
          source="aggregates only, never a row"
          note="row counts only; the capture window's dates are in the tiles above" />`}

      <p class="hint">Capture is <b>${data.capture_enabled ? "on" : "off"}</b>.</p>

      <${SectionHeading} title="Retention tiers"
        hint=${"Three tiers with different jobs. The audit tier is the outer edge of the data's " +
               "life and is the destructive one; the training tier SELECTS, so lowering it " +
               "destroys nothing."} />
      <${DataTable} columns=${[
        { key: "tier", label: "tier" },
        { key: "value", label: "bound", numeric: true },
        { key: "what", label: "what it does" },
      ]} rows=${[
        ["sink", `${count(data.sink_days)} days / ${count(data.sink_rows)} rows`,
         "The raw capture buffer. Bounded by whichever limit is reached first."],
        ["training", `${count(data.training_days)} days`,
         "How far back a model may read. Lowering it narrows selection and deletes nothing."],
        ["audit", `${count(data.audit_days)} days`,
         "The outer bound on the data's life. Lowering it DELETES, and there is no rollback."],
      ].map(([tier, value, what]) => ({
        key: tier,
        tone: tier === "audit" ? "alarm" : null,
        cells: { tier: html`<b>${tier}</b>`, value, what },
      }))} />

      <p class="hint">Since this process started, the background sweep has removed${" "}
        ${Object.entries(data.audit_swept || {}).map(([k, v]) => `${count(v)} ${k}`).join(", ")
          || "nothing"}. Tiers are changed on <a href="#/settings?tab=system">Settings → System</a>,
        which previews what a change would delete before it deletes it.</p>
    </div>`;
  }
}
