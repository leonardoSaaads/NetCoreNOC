/* NOC → Routines: each customer's troubleshooting, written once and run in one call (ADR #492).
 *
 * Formerly Playbooks (ADR #463). A routine is a tree of read-only checks — reach the element, run a
 * command over SSH, read an SNMP value — and what each answer means: check the next thing, resolve,
 * wait, or send the case to the NOC. Inspired by Ansible: declarative steps, a fixed set of modules,
 * nothing evaluated. What is new:
 *
 *   * **a routine is a customer's** — a provider's fault is worked its own way, so a routine may be
 *     for one customer (and is chosen before one for every customer);
 *   * **a routine has a tier** — the least tier of agent that may see and run it; a tier-1 agent
 *     never learns a tier-2 routine exists;
 *   * **an agent runs one on demand** (`noc_run_playbook`): one call instead of a session of
 *     commands, and the routine's conclusion comes back as advice.
 *
 * The screen: routines grouped by customer, filtered by customer, tier and a word; one opened as
 * its steps; and, for a lead, a guided form to write one (`parts/routineform.js`). A lead's screen
 * (ADR #470): anyone else is told so before anything is read.
 */

import { html, Component, cx } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { can } from "../session.js";
import { count, relative, timeTitle } from "../format.js";
import { Icon } from "../icons.js";
import { readNocAccess } from "../roster.js";
import { RoutineForm } from "./parts/routineform.js";
import { Toggle } from "./parts/vaultui.js";

const END = { resolve: "Resolve", pending: "Wait", queue: "NOC queue" };
const TIER_WORD = { 1: "Any agent", 2: "Tier 2 and above", 3: "Tier 3 and above", 4: "Tier 4 only" };

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
  if (name === "reach") return html`<${Icon} name="signal" /> Reach the element`;
  if (name === "ssh") return html`<${Icon} name="terminal" /> Run <code>${args.command}</code>`;
  return html`<${Icon} name="code" /> SNMP GET <code>${args.oids.join(", ")}</code>`;
}

/** One step and everything below it, depth first — the tree as a flow of cards. */
function Step({ doc, name, seen, labels }) {
  const step = doc.steps[name];
  if (seen.has(name)) return html`<span class="muted">↩ back to “${name}”</span>`;
  const next = new Set([...seen, name]);
  if (step.end) {
    return html`<span class=${cx("pb-leaf", `pb-${step.end}`)}><b>${END[step.end]}</b>${step.end === "resolve"
      ? html` — ${labels.causes[step.cause] || step.cause}${step.clear_alarms ? ", clearing the alarms" : ""}`
      : step.end === "pending" ? html` — ${labels.pending[step.status] || step.status}` : ""}</span>`;
  }
  return html`<div class="rt-step"><span class="rt-step-do">${action(step)}</span>
    <ul class="pb-branches">${step.next.map((b, i) => html`<li key=${i}>
      <span class="pb-if">${b.if ? `if ${condition(b.if)}` : "otherwise"}</span>
      <${Step} doc=${doc} name=${b.go} seen=${next} labels=${labels} />
    </li>`)}</ul>
  </div>`;
}

const DO_WORD = { reach: "Reach", ssh: "SSH", snmp_get: "SNMP" };

/** A run's path in words: each check it made and whether it worked (never the step's inner id). */
function pathText(doc, trace) {
  return trace.map((t) => {
    const step = doc.steps[t.step];
    const word = step && step.do ? DO_WORD[Object.keys(step.do)[0]] || t.step : t.step;
    return `${word} ${t.ok ? "✓" : "✗"}`;
  }).join(" → ");
}

function matchText(m) {
  return [...(m.vendors || []), ...(m.severities || []), ...(m.trap_oids || []).map((o) => `trap ${o}`)].join(" · ");
}

export function TierBadge({ tier }) {
  return html`<span class=${cx("rt-tier", `rt-tier-${tier}`)} title=${TIER_WORD[tier]}>T${tier}</span>`;
}

class RoutineDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { p: null, error: null, busy: false };
  }

  componentDidMount() { this.read(); }

  async read() {
    const first = !this.state.p;
    try {
      this.setState({ p: await get(`/api/noc/playbooks/${this.props.id}`) });
      // Opened from a card above: bring it into view, as a drawer would be.
      if (first && this.base && this.base.scrollIntoView) this.base.scrollIntoView({ block: "nearest" });
    }
    catch (error) { this.setState({ error: error.detail || error.message }); }
  }

  async toggle(enabled) {
    this.setState({ busy: true });
    try {
      await post(`/api/noc/playbooks/${this.props.id}/enabled`, { enabled });
      await this.read();
      this.props.onChanged();
    } catch (error) { this.setState({ error: error.detail || error.message }); }
    this.setState({ busy: false });
  }

  render({ labels, onEdit }, { p, error, busy }) {
    if (error) return html`<p class="err" role="alert">${error}</p>`;
    if (!p) return html`<p class="muted">Opening the routine…</p>`;
    return html`<section class="panel-block rt-detail" aria-label=${p.name}>
      <header class="rt-detail-head">
        <span class="rt-glyph"><${Icon} name="playbook" /></span>
        <div><h3>${p.name}</h3>
          <p class="rt-meta"><${TierBadge} tier=${p.tier} />
            <span class="badge">${p.customer || "Every customer"}</span>
            <span class="muted">v${p.version} · ${TIER_WORD[p.tier]}</span></p></div>
        ${can("playbooks.write") ? html`<span class="rt-detail-acts">
          <${Toggle} on=${p.enabled} disabled=${busy} label=${p.enabled ? "On" : "Off"}
            hint="Off: no case runs it, and no agent may" onToggle=${() => this.toggle(!p.enabled)} />
          <button type="button" onClick=${() => onEdit(p)}><${Icon} name="wrench" /> Edit</button></span>` : null}
      </header>
      ${p.description ? html`<p class="rt-desc">${p.description}</p>` : null}
      <p class="rt-when"><b>When</b> ${matchText(p.match) || "—"} <span class="muted">· stops after ${p.timeout_s} s</span></p>
      <div class="pb-tree"><${Step} doc=${p.document} name=${p.document.start} seen=${new Set()} labels=${labels} /></div>
      <h4>Recent runs</h4>
      ${p.recent_runs.length ? html`<div class="table-scroll"><table class="data compact-table"><thead><tr>
        <th scope="col">Case</th><th scope="col">Started</th><th scope="col">By</th><th scope="col">Outcome</th>
        <th scope="col" class="wide-only">Steps</th></tr></thead>
        <tbody>${p.recent_runs.map((r) => html`<tr key=${r.id}>
          <td><a href=${`#/cases/${r.situation_id}`}>#${r.situation_id}</a></td>
          <td title=${timeTitle(r.started_at)}>${relative(r.started_at)}</td>
          <td>${r.started_by ? r.started_by : html`<span class="muted">automation</span>`}</td>
          <td>${r.outcome || "running"}</td>
          <td class="wide-only">${pathText(p.document, r.trace)}</td></tr>`)}</tbody></table></div>`
        : html`<p class="muted">Not run yet.</p>`}
      <details><summary>As JSON</summary><pre class="pb-json">${JSON.stringify(p.document, null, 2)}</pre></details>
    </section>`;
  }
}

export class Routines extends Loader {
  constructor(props) {
    super(props);
    this.what = "the routines";
    this.loadingLabel = "Reading the routines";
    this.state = { ...this.state, customer: "", tier: 0, q: "", editing: null };
  }

  async load() {
    const access = await readNocAccess();
    if (access && !access.lead) return { closed: access.explanations.lead };
    return get("/api/noc/playbooks");
  }

  routeKey() { return "routines"; }

  open(id) { this.props.navigate(id == null ? "#/routines" : `#/routines/${id}`); }

  saved(view) {
    this.setState({ editing: null });
    this.reload();
    if (view && view.id) this.open(view.id);
  }

  view(data) {
    if (data.closed) return html`<${Empty} title="Routines are for the NOC's leads" will=${data.closed} />`;
    const { customer, tier, q, editing } = this.state;
    const selected = Number((this.props.params || [])[0]) || null;
    const writer = can("playbooks.write");
    if (editing) {
      return html`<${RoutineForm} routine=${editing === "new" ? null : editing} labels=${data}
        onCancel=${() => this.setState({ editing: null })} onSaved=${(v) => this.saved(v)} />`;
    }
    const needle = q.trim().toLowerCase();
    const rows = data.playbooks.filter((p) => (!customer || String(p.customer_id ?? "") === customer)
      && (!tier || p.tier === tier)
      && (!needle || `${p.name} ${matchText(p.match)}`.toLowerCase().includes(needle)));
    const groups = new Map();
    for (const p of rows) {
      const key = p.customer || "Every customer";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(p);
    }
    return html`<div class="routines">
      <div class="rt-bar">
        <select aria-label="Customer" value=${customer} onChange=${(e) => this.setState({ customer: e.currentTarget.value })}>
          <option value="">All customers</option>
          ${data.customers.map((c) => html`<option key=${c.id} value=${String(c.id)}>${c.name}</option>`)}
        </select>
        <div class="seg" role="radiogroup" aria-label="Tier">
          ${[0, ...data.tiers].map((t) => html`<button key=${t} type="button" role="radio" aria-checked=${tier === t}
            class=${cx("seg-btn", tier === t && "on")} onClick=${() => this.setState({ tier: t })}>${t ? `T${t}` : "All tiers"}</button>`)}
        </div>
        <input type="search" placeholder="Find a routine" aria-label="Find a routine" value=${q}
          onInput=${(e) => this.setState({ q: e.currentTarget.value })} />
        ${writer ? html`<button type="button" class="primary rt-new" onClick=${() => this.setState({ editing: "new" })}>
          <${Icon} name="playbook" /> New routine</button>` : null}
      </div>
      ${data.playbooks.length ? null : html`<${Empty} title="No routines yet"
        will="Write the checks a customer's fault needs once; agents run them in one call and the next case is checked before anyone is called." />`}
      ${[...groups.entries()].map(([name, list]) => html`<section key=${name} class="rt-group">
        <h3 class="rt-group-head"><${Icon} name=${name === "Every customer" ? "globe" : "building"} /> ${name}
          <span class="tab-count">${count(list.length)}</span></h3>
        <div class="rt-cards">${list.map((p) => html`<button key=${p.id} type="button"
            class=${cx("rt-card", selected === p.id && "is-on", !p.enabled && "is-off")} onClick=${() => this.open(p.id)}>
          <span class="rt-card-top"><${TierBadge} tier=${p.tier} /><b>${p.name}</b>
            <span class=${cx("run-state", p.enabled ? "run-ok" : "run-off")}>${p.enabled ? "On" : "Off"}</span></span>
          <span class="rt-card-when">${matchText(p.match) || "—"}</span>
          <span class="rt-card-foot"><span>${count(p.checks)} ${p.checks === 1 ? "check" : "checks"}</span><span>${count(p.runs || 0)} runs</span>
            <span title=${timeTitle(p.last_run_at)}>${p.last_run_at ? relative(p.last_run_at) : "never run"}</span></span>
        </button>`)}</div>
      </section>`)}
      ${rows.length || !data.playbooks.length ? null : html`<p class="muted">No routine matches these filters.</p>`}
      ${selected ? html`<${RoutineDetail} key=${selected} id=${selected} labels=${data}
        onEdit=${(p) => this.setState({ editing: p })} onChanged=${() => this.reload()} />` : null}
    </div>`;
  }
}

/** `#/playbooks`, kept for links and bookmarks: the same screen under its new name. */
export function PlaybooksAlias(props) { return html`<${Routines} ...${props} />`; }
