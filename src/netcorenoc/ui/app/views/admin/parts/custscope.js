/* Which brokers and which NOC customers one person reaches (ADR #508): the person editor's
 * "Brokers & customers" tab.
 *
 * Two planes, two choices — each Every / Only these / None: a person may work Claro's NOC and see
 * none of its brokers, or watch two brokers and have no NOC at all. A viewer, an editor and an admin
 * alike; an admin limited this way administers its own brokers and customers and nothing of the
 * appliance's (the server takes those capabilities away). The **super admin** is never limited: the
 * tab says so and offers nothing to change. The server writes it as `broker:`/`noc:` selectors in
 * the visibility policy, in the same commit as a new account.
 */

import { html, Component, cx } from "../../../dom.js";
import { get } from "../../../api.js";
import { Icon } from "../../../icons.js";
import { plural } from "../../../format.js";

/** The brokers and customers, read once for every editor that opens. */
let estate = null;

async function load() {
  if (!estate) {
    const [customers, brokers] = await Promise.all([get("/api/noc/customers"), get("/api/brokers")]);
    estate = { customers: customers.customers || [], brokers: brokers.brokers || [] };
  }
  return estate;
}

/** A person's reach in a list row: nothing when unlimited, else what it is limited to. */
export function AccessBadge({ user }) {
  if (user.owner) {
    return html`<span class="badge badge-owner" title="Every broker and every customer, always">
      <${Icon} name="crown" /> Super admin</span>`;
  }
  const a = user.access || {};
  if (a.brokers == null && a.customers == null) return null;
  const part = (list, one, none) => (list == null ? `every ${one}` : list.length ? plural(list.length, one) : none);
  return html`<span class="badge badge-scope" title="What this person reaches">
    <${Icon} name="lock" /> ${part(a.brokers, "broker", "no broker")} · ${part(a.customers, "customer", "no NOC")}</span>`;
}

/** Every / Only these / None over one list, with its checklist when narrowed. */
function Plane({ title, icon, lead, items, value, onChange, everyLabel, noneLabel, describe }) {
  const mode = value == null ? "all" : value.length || value.only ? "some" : "none";
  const set = (next) => onChange(next);
  const toggle = (id) => {
    const now = new Set(value || []);
    if (now.has(id)) now.delete(id); else now.add(id);
    const list = [...now].sort((a, b) => a - b);
    list.only = true;  // "Only these" with every box cleared stays "Only these", and says so
    set(list);
  };
  return html`<section class="plane-scope">
    <h4><${Icon} name=${icon} /> ${title}</h4>
    <p class="muted acct-lead">${lead}</p>
    <div class="seg" role="radiogroup" aria-label=${title}>
      <button type="button" role="radio" aria-checked=${mode === "all"} class=${cx("seg-btn", mode === "all" && "on")}
        onClick=${() => set(null)}>${everyLabel}</button>
      <button type="button" role="radio" aria-checked=${mode === "some"} class=${cx("seg-btn", mode === "some" && "on")}
        data-role="only-these" onClick=${() => { const l = [...(value || [])]; l.only = true; set(l); }}>Only these</button>
      <button type="button" role="radio" aria-checked=${mode === "none"} class=${cx("seg-btn", mode === "none" && "on")}
        onClick=${() => set([])}>${noneLabel}</button>
    </div>
    ${mode === "some" ? html`<ul class="cust-scope-list">
      ${items.map((it) => {
        const on = value.includes(it.id);
        return html`<li key=${it.id}><label class=${cx("cust-scope-row", on && "on")}>
          <input type="checkbox" checked=${on} onChange=${() => toggle(it.id)} />
          <span class="cust-scope-name"><b>${it.name}</b><small class="muted">${describe(it)}</small></span>
        </label></li>`;
      })}
    </ul>
    ${value.length ? null : html`<p class="warn-text">Choose at least one, or ${noneLabel}.</p>`}` : null}
  </section>`;
}

export class AccessScope extends Component {
  constructor(props) {
    super(props);
    this.state = { estate: null, error: null };
  }

  async componentDidMount() {
    try { this.setState({ estate: await load() }); }
    catch (error) { this.setState({ error: error.detail || error.message }); }
  }

  render({ value, owner, onChange }, { estate, error }) {
    if (owner) {
      return html`<div class="owner-note">
        <span class="owner-mark"><${Icon} name="crown" /></span>
        <div><b>Super admin — every broker and every customer, always.</b>
          <p class="muted">At least one account must reach everything, so this one's access cannot be
            limited, its role cannot change and it cannot be deleted — by anyone, themselves included.</p></div>
      </div>`;
    }
    if (error) return html`<p class="err" role="alert">${error}</p>`;
    if (!estate) return html`<p class="muted" aria-busy="true">Reading the brokers and customers…</p>`;
    const byId = Object.fromEntries(estate.customers.map((c) => [c.id, c.name]));
    const v = value || { brokers: null, customers: null };
    return html`<div class="access-scope">
      <${Plane} title="Brokers" icon="network" value=${v.brokers}
        lead="The networks whose situations, map, timeline and elements this person sees in the Broker plane."
        everyLabel="Every broker" noneLabel="No broker" items=${estate.brokers}
        describe=${(b) => `customer: ${byId[b.organization_id] || b.customer || "—"}`}
        onChange=${(brokers) => onChange({ ...v, brokers })} />
      <${Plane} title="NOC customers" icon="building" value=${v.customers}
        lead="The customers whose cases, book, routines and credentials this person reaches in the NOC plane."
        everyLabel="Every customer" noneLabel="No NOC" items=${estate.customers}
        describe=${(c) => `${plural(c.elements || 0, "element")}${c.open_cases ? ` · ${plural(c.open_cases, "open case")}` : ""}`}
        onChange=${(customers) => onChange({ ...v, customers })} />
      <p class="muted acct-lead">Nothing outside these is shown to them — not a name, not a count. An admin
        limited here administers these brokers and customers only; the appliance's own settings, people
        and audit stay with the super admin and admins with no limit.</p>
    </div>`;
  }
}
