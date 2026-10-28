/* NOC → Playbooks: the troubleshooting trees agents wrote, and how each has run (ADR #463).
 *
 * A list — name, what it matches, on or off, how often it ran and how it ended — and one playbook
 * opened below it as the tree it is: each step, what it does, and where each answer leads, down to
 * the leaves (resolve, wait, or the NOC queue). Switching one off is a person's; writing one is
 * the agents' (and anyone's, through the API — the tree is shown here as JSON too).
 */

import { html, Component, cx } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { can } from "../session.js";
import { relative, timeTitle } from "../format.js";
import { Icon } from "../icons.js";

const END = { resolve: "Resolve", pending: "Wait", queue: "NOC queue" };

function condition(cond) {
  const parts = [];
  if ("ok" in cond) parts.push(cond.ok ? "it worked" : "it failed");
  if (cond.contains) parts.push(`the answer contains “${cond.contains}”`);
  if (cond.not_contains) parts.push(`the answer lacks “${cond.not_contains}”`);
  if (cond.vendor) parts.push(`the element is ${cond.vendor}`);
  if (cond.number_after) {
    const bound = "below" in cond ? `below ${cond.below}` : `above ${cond.above}`;
    parts.push(`the number after “${cond.number_after}” is ${bound}`);
  }
  return parts.join(" and ");
}

function action(step) {
  if (!step.do) return "decide";
  const [name, args] = Object.entries(step.do)[0];
  if (name === "reach") return "reach the element";
  if (name === "ssh") return html`run <code>${args.command}</code>`;
  return html`SNMP GET <code>${args.oids.join(", ")}</code>`;
}

/** One step and everything below it, depth first — the tree as a nested list. */
function Step({ doc, name, seen, labels }) {
  const step = doc.steps[name];
  if (seen.has(name)) return html`<span class="muted">→ ${name}</span>`;
  const next = new Set([...seen, name]);
  if (step.end) {
    return html`<span class=${cx("pb-leaf", `pb-${step.end}`)}><b>${END[step.end]}</b>${step.end === "resolve"
      ? html` — ${labels.causes[step.cause] || step.cause}${step.clear_alarms ? ", clearing the alarms" : ""}`
      : step.end === "pending" ? html` — ${labels.pending[step.status] || step.status}` : ""}${step.note ? html` · ${step.note}` : ""}</span>`;
  }
  return html`<div class="pb-step"><span class="pb-name">${name}</span>: ${action(step)}
    <ul class="pb-branches">${step.next.map((b, i) => html`<li key=${i}>
      <span class="pb-if">${b.if ? `if ${condition(b.if)}` : "otherwise"}</span>
      <${Step} doc=${doc} name=${b.go} seen=${next} labels=${labels} />
    </li>`)}</ul>
  </div>`;
}

class PlaybookDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { p: null, error: null, busy: false };
  }

  componentDidMount() { this.read(); }

  async read() {
    try { this.setState({ p: await get(`/api/noc/playbooks/${this.props.id}`) }); }
    catch (error) { this.setState({ error: error.message }); }
  }

  async toggle(enabled) {
    this.setState({ busy: true });
    try {
      await post(`/api/noc/playbooks/${this.props.id}/enabled`, { enabled });
      await this.read();
      this.props.onChanged();
    } catch (error) { this.setState({ error: error.message }); }
    this.setState({ busy: false });
  }

  render(_props, { p, error, busy }) {
    if (error) return html`<p class="err" role="alert">${error}</p>`;
    if (!p) return html`<p class="muted">Opening the playbook…</p>`;
    return html`<section class="panel-block pb-detail" aria-label=${p.name}>
      <h3>${p.name} <span class="muted">v${p.version}</span></h3>
      ${p.description ? html`<p>${p.description}</p>` : null}
      ${can("playbooks.write") ? html`<label class="check-row"><input type="checkbox" checked=${p.enabled}
        disabled=${busy} onChange=${(e) => this.toggle(e.currentTarget.checked)} /> Runs on matching cases</label>` : null}
      <h4>The tree <span class="muted">— timeout ${p.timeout_s} s</span></h4>
      <div class="pb-tree"><${Step} doc=${p.document} name=${p.document.start} seen=${new Set()}
        labels=${this.props.labels} /></div>
      <h4>Recent runs</h4>
      ${p.recent_runs.length ? html`<table class="data"><thead><tr><th scope="col">Case</th>
        <th scope="col">Started</th><th scope="col">Outcome</th><th scope="col">Steps</th></tr></thead>
        <tbody>${p.recent_runs.map((r) => html`<tr key=${r.id}>
          <td><a class="tap" href=${`#/cases/${r.situation_id}`}>#${r.situation_id}</a></td>
          <td title=${timeTitle(r.started_at)}>${relative(r.started_at)}</td>
          <td>${r.outcome || "running"}</td>
          <td>${r.trace.map((t) => `${t.step}${t.ok ? "" : " ✗"}`).join(" → ")}</td></tr>`)}</tbody></table>`
        : html`<p class="muted">Not run yet.</p>`}
      <details><summary>As JSON</summary><pre class="pb-json">${JSON.stringify(p.document, null, 2)}</pre></details>
    </section>`;
  }
}

export class Playbooks extends Loader {
  constructor(props) {
    super(props);
    this.what = "the playbooks";
    this.loadingLabel = "Reading the playbooks";
  }

  async load() { return get("/api/noc/playbooks"); }

  routeKey() { return "playbooks"; }

  open(id) { this.props.navigate(id == null ? "#/playbooks" : `#/playbooks/${id}`); }

  view(data) {
    const selected = Number((this.props.params || [])[0]) || null;
    if (!data.playbooks.length) {
      return html`<${Empty} title="No playbooks yet"
        will="An agent writes one after it resolves a case; the next matching case runs it before the NOC is called." />`;
    }
    return html`<div class="playbooks">
      <div class="table-scroll list-view"><table class="data"><thead><tr>
        <th scope="col">Playbook</th><th scope="col">Matches</th><th scope="col">State</th>
        <th scope="col">Runs</th><th scope="col" class="wide-only">Last run</th></tr></thead>
        <tbody>${data.playbooks.map((p) => html`<tr key=${p.id} class=${cx(selected === p.id && "row-selected")}>
          <td><button type="button" class="linkish pb-open" onClick=${() => this.open(p.id)}>
            <span class="pb-glyph"><${Icon} name="playbook" /></span>${p.name}</button></td>
          <td>${[...(p.match.vendors || []), ...(p.match.trap_oids || []), ...(p.match.severities || [])].join(", ")}</td>
          <td>${p.enabled ? html`<span class="run-state run-ok">On</span>` : html`<span class="run-state run-off">Off</span>`}</td>
          <td>${p.runs}</td>
          <td class="wide-only" title=${timeTitle(p.last_run_at)}>${p.last_run_at ? relative(p.last_run_at) : "—"}</td>
        </tr>`)}</tbody></table></div>
      ${selected ? html`<${PlaybookDetail} key=${selected} id=${selected} labels=${data}
        onChanged=${() => this.reload()} />` : null}
    </div>`;
  }
}
