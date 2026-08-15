/* The audit log, and its verification state.
 *
 * ## What the chain can and cannot be told (draft §5.3)
 *
 * The rows are hash-linked, so a modification is detectable. `python -m netcorenoc audit verify`
 * walks the chain and reports the first broken link; there is no HTTP route for that walk, so
 * this screen says *how* to run it rather than showing a verdict it cannot compute. **It offers
 * no repair, because there is none** — a chain whose break could be repaired from a console
 * would not be evidence of anything.
 *
 * Pruning deletes rows. The chain is hash-linked, so a prune is *visible* but not *reversible*,
 * which is why it previews first and says exactly that.
 */

import { html, Component } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, Empty, DataTable, TimeCell, SectionHeading, cell } from "../widgets.js";
import { count, plural } from "../format.js";
import { can } from "../session.js";
import { Destructive } from "../destructive.js";

export class Audit extends Loader {
  constructor(props) {
    super(props);
    this.what = "the audit log";
    this.loadingLabel = "Reading the audit log";
  }

  async load() { return get("/api/audit?limit=200"); }

  view(rows) {
    return html`<div class="auditview">
      <${ChainState} />
      ${can("audit.export") ? html`<${Export} />` : null}
      ${can("audit.prune") ? html`<${Prune} onDone=${this.reload} />` : null}
      <${SectionHeading} title="Recent entries"
        hint="Newest first, capped at 200. Every row carries who, when, from where, and the outcome." />
      ${rows.length ? html`<${DataTable} columns=${COLUMNS} rows=${rows.map(toRow)} />`
        : html`<${Empty}
            title="The audit log is empty."
            will=${"A row is written for every change: a sign-in, a configuration change, a " +
                   "policy edit, a scorer retune, a promotion, and every denied attempt at a " +
                   "sensitive capability."}
            meanwhile=${"An empty log on a running appliance means nothing has been changed yet " +
                        "— which is the expected state of a zero-config product nobody has had " +
                        "to configure."} />`}
    </div>`;
  }
}

const COLUMNS = [
  { key: "when", label: "when" },
  { key: "actor", label: "actor" },
  { key: "role", label: "role" },
  { key: "action", label: "action" },
  { key: "object", label: "object" },
  { key: "outcome", label: "outcome" },
];

function toRow(entry, index) {
  return {
    key: `${entry.ts}-${index}`,
    tone: entry.outcome === "denied" ? "alarm" : null,
    cells: {
      when: cell(html`<${TimeCell} ts=${entry.ts} />`),
      actor: entry.actor,
      role: entry.role || "—",
      action: html`<code class="mono">${entry.action}</code>`,
      object: `${entry.object_type || ""}${entry.object_id ? `/${entry.object_id}` : ""}`,
      outcome: html`<span class=${`outcome outcome-${entry.outcome}`}>${entry.outcome}</span>`,
    },
  };
}

function ChainState() {
  return html`<section class="panel-block param-structural">
    <${SectionHeading} title="Chain integrity"
      hint=${"Every row carries a hash of the row before it, so an edited or deleted row breaks " +
             "the chain at a detectable point."} />
    <p class="structural-note"><b>Verification runs offline, not from this console.</b>
      <code>python -m netcorenoc audit verify</code> walks the chain and reports the first broken
      link. There is no route for it and this screen does not invent one: a verdict computed by
      the same process that writes the rows would be worth less than the walk itself.</p>
    <p class="hint">There is no repair. A broken chain is a fact about what happened to this
      database, and a console that offered to fix it would be offering to destroy the evidence.</p>
  </section>`;
}

class Export extends Component {
  constructor(props) {
    super(props);
    this.state = { busy: false, error: null };
  }

  async run() {
    this.setState({ busy: true, error: null });
    try {
      const ndjson = await get("/api/audit/export");
      const blob = new globalThis.Blob([ndjson], { type: "application/x-ndjson" });
      const url = globalThis.URL.createObjectURL(blob);
      const anchor = globalThis.document.createElement("a");
      anchor.setAttribute("href", url);
      anchor.setAttribute("download", "audit.ndjson");
      anchor.click();
      globalThis.URL.revokeObjectURL(url);
      this.setState({ busy: false });
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render(_props, { busy, error }) {
    return html`<section class="panel-block">
      <${SectionHeading} title="Export"
        hint="The whole log as NDJSON, one row per line, in chain order. Exporting is itself audited." />
      <button type="button" disabled=${busy} onClick=${() => this.run()}>
        ${busy ? "Exporting…" : "Export NDJSON"}
      </button>
      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </section>`;
  }
}

/** `POST /api/audit/prune` — a route with no UI surface in v0.12.0 (draft §4). */
class Prune extends Component {
  render({ onDone }) {
    return html`<${Destructive}
      title="Prune the audit log"
      hint=${"Deletes rows older than the retention bound. The chain is hash-linked, so a prune " +
             "is visible in the chain but is not reversible."}
      confirmLabel="Prune now"
      previewLabel="Preview what would be pruned"
      consequence=${"Audit rows are deleted permanently. The rows that remain still verify " +
                    "against each other; the deleted ones cannot be recovered from this appliance."}
      preview=${async () => {
        // The route applies; there is no preview mode on it. Say so rather than inventing a
        // count this console cannot compute — a fabricated preview is worse than none.
        return {
          measured: false,
          message:
            "This route has no preview mode: the appliance does not offer a count of what a " +
            "prune would remove without performing it. What is shown above is every row this " +
            "console can currently read. Pruning removes rows older than the configured audit " +
            "retention, which is on the Settings screen.",
        };
      }}
      apply=${async () => post("/api/audit/prune", {})}
      onDone=${onDone} />`;
  }
}
