/* Settings → Site training: the in-product search that adapts the GAM to this site (ADR #413).
 *
 * Random search over a bounded space with successive halving, seeded and resumable, run out of
 * process by the slow loop — so it never holds the lock the receiver needs, and **Stop** takes
 * effect at the next round. Its result **joins the league** (v0.27.0, ADR #425) as one more model
 * the judge ranks: it decides only if it beats the champion on labels that arrived after it was
 * fitted. Nobody has to switch to it.
 *
 * The budget is the admin's: how many configurations, how many rounds the survivors get, and a
 * wall-clock ceiling. Every trial is kept and charted on Judge.
 */

import { html, Component } from "../../dom.js";
import { get, post } from "../../api.js";
import { Loading, Failed, SectionHeading, DataTable, TimeCell, cell } from "../../widgets.js";
import { can } from "../../session.js";

const BUDGET = [
  ["trials", "Configurations", "Drawn at random from the space; each starts on a short budget.", 2, 40],
  ["max_rounds", "Rounds for the survivors", "Successive halving keeps the best third at each rung.", 20, 600],
  ["minutes", "Wall-clock ceiling (minutes)", "The search stops here whatever is left.", 1, 240],
  ["seed", "Seed", "Same seed, same labels: same trials, same model.", 0, 2147483647],
];

export class SearchPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, busy: false, form: null, outcome: null };
    this.read = this.read.bind(this);
  }

  componentDidMount() { this.read(); }

  async read() {
    try {
      const data = await get("/api/search");
      const form = this.state.form || { ...data.defaults, seed: 0 };
      this.setState({ data, form, error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  async act(path, body) {
    this.setState({ busy: true, outcome: null });
    try {
      await post(path, body);
      this.setState({ busy: false });
      this.read();
    } catch (error) {
      this.setState({ busy: false, outcome: error });
    }
  }

  render(_props, { data, error, busy, form, outcome }) {
    if (error) return html`<${Failed} error=${error} retry=${this.read} what="the search" />`;
    if (!data) return html`<${Loading} label="Reading the search" />`;
    const running = data.runs.length && data.runs[0].status === "running";
    const writable = can("search.write");
    return html`<div class="stack">
      <div class="settings-intro">
        <p><b>Teach the models this network.</b> The pre-trained models learned from generated
          incidents. A site training run fits an adapted GAM on the groupings your operators have
          confirmed, corrected, accepted or rejected here, and adds it to the league.</p>
        <p>The adapted model then competes like any other: the judge lets it decide only if it is
          better on labels it has not seen. Nothing is switched by hand, and nothing waits for a
          minimum number of labels — with few labels, it simply cannot prove itself yet.</p>
      </div>
      <section class="panel-block param-mechanism">
        <${SectionHeading} title="Search budget"
          hint="Runs in the background on the maintenance loop, one round at a time: ingestion and the console are never blocked." />
        ${running ? html`<p class="warn-note" role="status">A search is running (started by
            ${data.runs[0].started_by}).</p>
          ${writable ? html`<button type="button" class="danger" disabled=${busy}
            onClick=${() => this.act("/api/search/stop", {})}>${busy ? "Stopping…" : "Stop the search"}</button>` : null}`
        : writable ? html`<form class="grid-form" onSubmit=${(e) => {
            e.preventDefault();
            this.act("/api/search", Object.fromEntries(BUDGET.map(([k]) => [k, Number(form[k])])));
          }}>
            ${BUDGET.map(([key, label, note, lo, hi]) => html`<div key=${key}>
              <label for=${`se-${key}`}>${label}</label>
              <input id=${`se-${key}`} type="number" min=${lo} max=${hi} step="1" value=${form[key]}
                onInput=${(e) => this.setState({ form: { ...form, [key]: e.target.value } })} />
              <p class="impact">${note}</p>
            </div>`)}
            <div class="wide">${data.blocked
              ? html`<p class="hint" role="status">A search cannot start: ${data.blocked}.</p>` : null}
              <button type="submit" disabled=${busy || Boolean(data.blocked)}>${busy ? "Starting…" : "Start a search"}</button></div>
          </form>`
        : html`<p class="hint">Only an admin can start or stop a search.</p>`}
        ${outcome ? html`<p class="err" role="alert">${outcome.detail || outcome.message}</p>` : null}
      </section>
      <${Runs} runs=${data.runs} />
    </div>`;
  }
}

function Runs({ runs }) {
  return html`<section class="panel-block">
    <${SectionHeading} title="Searches" hint="Every run and its outcome. The trials are charted on Judge." />
    <${DataTable} empty="No search has run on this appliance." columns=${[
      { key: "id", label: "#", numeric: true }, { key: "when", label: "started" },
      { key: "by", label: "by" }, { key: "status", label: "status" },
      { key: "rows", label: "rows", numeric: true }, { key: "verdict", label: "verdict" },
      { key: "note", label: "note" },
    ]} rows=${runs.map((r) => ({
      key: r.id,
      cells: {
        id: String(r.id),
        when: cell(html`<${TimeCell} ts=${r.started_at} />`),
        by: r.started_by,
        status: html`<b class="mono">${r.status}</b>`,
        rows: r.rows == null ? "—" : String(r.rows),
        verdict: r.judgement ? html`<b class="mono">${r.judgement.verdict}</b>` : "—",
        note: (r.judgement && r.judgement.reason) || r.note || "—",
      },
    }))} />
  </section>`;
}
