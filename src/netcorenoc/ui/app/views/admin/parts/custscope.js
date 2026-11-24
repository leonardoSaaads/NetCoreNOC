/* Which customers one person serves (ADR #499): the Customers tab of the person editor.
 *
 * "Every customer", or "Only these": a person limited to Claro sees Claro's brokers — every element
 * Claro owns, in every broker — and Claro's side of the NOC (its book, cases, routines and the
 * credentials it is named a custodian of), and nothing of any other customer. The server writes it
 * as `org:<id>` in the visibility policy, in the same commit as a new account; a role the server
 * says cannot be narrowed (`scopable: false`) shows why instead of the choice.
 */

import { html, Component, cx } from "../../../dom.js";
import { get } from "../../../api.js";
import { Icon } from "../../../icons.js";
import { plural } from "../../../format.js";

/** The customers and their brokers, read once for every editor that opens. */
let estate = null;

async function load() {
  if (!estate) {
    const [customers, brokers] = await Promise.all([get("/api/noc/customers"), get("/api/brokers")]);
    estate = { customers: customers.customers || [], brokers: brokers.brokers || [] };
  }
  return estate;
}

/** What a person's customers read as, in a list row or a header. */
export function CustomerBadge({ organizations, names }) {
  if (!organizations) return null;
  const label = organizations.length === 1 && names[organizations[0]]
    ? `${names[organizations[0]]} only` : plural(organizations.length, "customer");
  return html`<span class="badge badge-scope" title="Sees only these customers' brokers and NOC">
    <${Icon} name="lock" /> ${label}</span>`;
}

/** The names a badge reads customers by, once the estate is loaded. */
export async function customerNames() {
  try {
    const { customers } = await load();
    return Object.fromEntries(customers.map((c) => [c.id, c.name]));
  } catch { return {}; }
}

export class CustomerScope extends Component {
  constructor(props) {
    super(props);
    this.state = { estate: null, error: null };
  }

  async componentDidMount() {
    try { this.setState({ estate: await load() }); }
    catch (error) { this.setState({ error: error.detail || error.message }); }
  }

  toggle(id) {
    const now = new Set(this.props.value || []);
    if (now.has(id)) now.delete(id); else now.add(id);
    this.props.onChange([...now].sort((a, b) => a - b));
  }

  render({ value, fixed, roleName, onChange }, { estate, error }) {
    if (fixed) {
      return html`<p class="muted acct-lead">The ${roleName} role always sees every customer: an
        appliance must stay administrable. Give the Editor or Viewer role to limit someone.</p>`;
    }
    if (error) return html`<p class="err" role="alert">${error}</p>`;
    if (!estate) return html`<p class="muted" aria-busy="true">Reading the customers…</p>`;
    const limited = value != null;
    return html`<div class="cust-scope">
      <div class="seg" role="radiogroup" aria-label="Customers">
        <button type="button" role="radio" aria-checked=${!limited} class=${cx("seg-btn", !limited && "on")}
          onClick=${() => onChange(null)}>Every customer</button>
        <button type="button" role="radio" aria-checked=${limited} class=${cx("seg-btn", limited && "on")}
          data-role="only-these" onClick=${() => onChange(limited ? value : [])}>Only these customers</button>
      </div>
      <p class="muted acct-lead">${limited
        ? "They see these customers' brokers and their side of the NOC — cases, book, routines and credentials — and nothing of any other customer."
        : "No limit by customer: what they see is their role's, and the visibility policy's."}</p>
      ${limited ? html`<ul class="cust-scope-list">
        ${estate.customers.map((c) => {
          const theirs = estate.brokers.filter((b) => b.organization_id === c.id);
          const on = value.includes(c.id);
          return html`<li key=${c.id}><label class=${cx("cust-scope-row", on && "on")}>
            <input type="checkbox" checked=${on} onChange=${() => this.toggle(c.id)} />
            <span class="cust-scope-name"><b>${c.name}</b>
              <small class="muted">${theirs.length
                ? theirs.map((b) => b.name).join(" · ") : "no broker yet"} · NOC book${c.open_cases
                ? ` · ${plural(c.open_cases, "open case")}` : ""}</small></span>
          </label></li>`;
        })}
      </ul>
      ${value.length ? null : html`<p class="warn-text">Choose at least one customer.</p>`}` : null}
    </div>`;
  }
}
