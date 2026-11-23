/* A customer's Routines (ADR #501): its own troubleshooting trees, beside its book.
 *
 * Instructions say what the customer wants; Routines are how its faults are worked — the checks an
 * agent runs in one call (`noc_run_playbook`) before anyone is called. This tab lists this
 * customer's routines and the shared ones that also run on its cases, opens one as its tree, its
 * runs and **how it improved** (every version, who wrote it and why), and lets a lead write a new
 * one for this customer. It is a lead's (ADR #470): anyone else is told so before anything is read.
 */

import { html, Component, cx } from "../../dom.js";
import { get } from "../../api.js";
import { Empty } from "../../widgets.js";
import { can } from "../../session.js";
import { count, plural, relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";
import { readNocAccess } from "../../roster.js";
import { RoutineForm } from "./routineform.js";
import { RoutineDetail, TierBadge, matchText } from "../routines.js";

function Card({ p, on, onOpen }) {
  return html`<button type="button" class=${cx("rt-card", on && "is-on", !p.enabled && "is-off")} onClick=${onOpen}>
    <span class="rt-card-top"><${TierBadge} tier=${p.tier} /><b>${p.name}</b>
      <span class=${cx("run-state", p.enabled ? "run-ok" : "run-off")}>${p.enabled ? "On" : "Off"}</span></span>
    <span class="rt-card-when">${matchText(p.match) || "—"}</span>
    <span class="rt-card-foot"><span>v${p.version}</span><span>${plural(p.checks, "check")}</span><span>${count(p.runs || 0)} runs</span>
      <span title=${timeTitle(p.last_run_at)}>${p.last_run_at ? relative(p.last_run_at) : "never run"}</span></span>
  </button>`;
}

export class CustomerRoutines extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, closed: null, editing: null };
  }

  componentDidMount() { this.read(); }

  async read() {
    try {
      const access = await readNocAccess();
      if (access && !access.lead) { this.setState({ closed: access.explanations.lead }); return; }
      this.setState({ data: await get(`/api/noc/playbooks?customer_id=${this.props.c.id}`), error: null });
    } catch (error) { this.setState({ error: error.detail || error.message }); }
  }

  open(id) { this.props.navigate(`#/customers/${this.props.c.id}/routines${id == null ? "" : `/${id}`}`); }

  saved(view) {
    this.setState({ editing: null });
    this.read();
    if (view && view.id) this.open(view.id);
  }

  render({ c, routineId }, { data, error, closed, editing }) {
    if (closed) return html`<${Empty} title="Routines are for the NOC's leads" will=${closed} />`;
    if (error) return html`<p class="err" role="alert">${error}</p>`;
    if (!data) return html`<p class="muted" aria-busy="true">Reading ${c.name}'s routines…</p>`;
    if (editing) {
      return html`<${RoutineForm} routine=${editing === "new" ? null : editing} customerId=${c.id} labels=${data}
        onCancel=${() => this.setState({ editing: null })} onSaved=${(v) => this.saved(v)} />`;
    }
    const own = data.playbooks.filter((p) => p.customer_id === c.id);
    const shared = data.playbooks.filter((p) => p.customer_id == null);
    const runs = data.playbooks.reduce((n, p) => n + (p.runs || 0), 0);
    const writer = can("playbooks.write");
    const group = (title, icon, list, none) => html`<section class="rt-group">
      <h3 class="rt-group-head"><${Icon} name=${icon} /> ${title} <span class="tab-count">${count(list.length)}</span></h3>
      ${list.length ? html`<div class="rt-cards">${list.map((p) => html`<${Card} key=${p.id} p=${p}
        on=${routineId === p.id} onOpen=${() => this.open(p.id)} />`)}</div>` : html`<p class="muted">${none}</p>`}
    </section>`;
    return html`<div class="cust-routines">
      <div class="cust-routines-head">
        <p class="muted">${plural(own.length, "routine")} of ${c.name}'s, ${plural(shared.length, "shared routine")} ·
          ${" "}${plural(runs, "run")}. An agent runs one in a single call; each version is kept, with why it changed.</p>
        <a class="linkish" href="#/routines">All routines</a>
        ${writer ? html`<button type="button" class="primary" onClick=${() => this.setState({ editing: "new" })}>
          <${Icon} name="playbook" /> New routine for ${c.name}</button>` : null}
      </div>
      ${group(`${c.name}'s routines`, "building", own, writer
        ? `None yet: write the checks ${c.name}'s faults need once, and the next case is checked before anyone is called.`
        : `None yet for ${c.name}.`)}
      ${group("Shared — every customer", "globe", shared, "No shared routine.")}
      ${routineId ? html`<${RoutineDetail} key=${routineId} id=${routineId} labels=${data}
        onEdit=${(p) => this.setState({ editing: p })} onChanged=${() => this.read()} />` : null}
    </div>`;
  }
}
