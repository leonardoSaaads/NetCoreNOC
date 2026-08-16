/* Corpus: what capture is holding, in rows.
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
import { count, plural } from "../format.js";

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
      <${SectionHeading} title="What capture is holding"
        hint=${"Aggregates only — counts and timestamps, never a row. The corpus is captured " +
               "engine-side, where visibility scoping does not exist, which is why every route " +
               "that touches it is admin-only."} />

      ${empty ? html`<${Empty}
        title="The corpus is empty."
        will=${"Every operator verdict — a confirm, a split, a partial split — is captured here " +
               "with the evidence that was on screen when it was given. This is the input to " +
               "every claim the appliance makes about how well it groups."}
        meanwhile=${"Open a situation with two or more members and confirm or split it. The " +
                    "first row appears immediately; the pre-registered floors need 50 asserting " +
                    "bags across 30 incidents before a promotion can be considered."} />`
        : html`<div class="stat-row">
          ${entries.map(([key, value]) => html`
            <${Stat} key=${key} label=${key.replaceAll("_", " ")} value=${value} />`)}
        </div>`}

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

      <p class="hint">Since this process started, the background sweep has removed
        ${Object.entries(data.audit_swept || {}).map(([k, v]) => `${count(v)} ${k}`).join(", ")
          || "nothing"}. Changing a tier is on the <a href="#/settings">Settings</a> screen, where
        the change previews what it would destroy before it destroys it.</p>

      <p class="structural-note">The corpus <b>census</b> — what the promotion gate would decide
        on this corpus, stated in advance — is <code class="mono">make census</code>. It is a
        deterministic offline report that carries its own control and exits non-zero if that
        control comes back empty. It has no HTTP route and this console does not recompute it
        (draft §11.11). ${plural(entries.length, "aggregate")} are shown above.</p>
    </div>`;
  }
}
