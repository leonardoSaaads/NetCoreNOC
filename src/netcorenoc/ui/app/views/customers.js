/* NOC → Customers: the customers the NOC serves, and each one's book (ADR #473).
 *
 * A NOC serves several customers, each its own way. `#/customers` lists them, a card each, with
 * what its book holds; `#/customers/{id}/{part}` opens one at a part of its book — the record page
 * Salesforce draws for an account, in this console's words:
 *
 *   * **Instructions** — how the customer wants the NOC to act, kept by people — beside the
 *     **memory** the NOC's agents keep of its network as they work;
 *   * **Workbook** — who to call, company by company, in the order they are called;
 *   * **Additional docs** — what it handed over for troubleshooting, and the text read from each;
 *   * **Circuits** — each circuit and its path, hop by hop.
 *
 * What a caller may change comes from the server (`actions`); a customer is an organization, which
 * an admin adds here (`organizations.write`) and puts elements in from each element's page.
 */

import { html, Component, cx } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { can } from "../session.js";
import { plural, relative, timeTitle } from "../format.js";
import { refreshRoster } from "../roster.js";
import { Icon } from "../icons.js";
import { BOOK } from "./parts/nocui.js";
import { BookText } from "./parts/custbook.js";
import { Workbook } from "./parts/custcontacts.js";
import { CustomerDocs } from "./parts/custdocs.js";
import { Circuits } from "./parts/circuits.js";

export class Customers extends Loader {
  constructor(props) {
    super(props);
    this.what = "the NOC's customers";
    this.loadingLabel = "Reading the customers";
  }

  async load() { return get("/api/noc/customers"); }

  /** Opening a customer is its own read (`CustomerRecord`); the list is read once. */
  routeKey() { return "customers"; }

  view(data) {
    const [id, part, sub] = this.props.params || [];
    if (Number(id)) {
      return html`<${CustomerRecord} key=${id} id=${Number(id)} part=${part || "instructions"}
        sub=${sub} query=${this.props.query} navigate=${this.props.navigate} />`;
    }
    return html`<div class="customers">
      ${can("organizations.write") ? html`<${AddCustomer} onAdded=${() => { refreshRoster(); this.reload(); }} />` : null}
      ${data.customers.length ? html`<ul class="cust-grid">
        ${data.customers.map((c) => html`<li key=${c.id}><${CustomerCard} c=${c} /></li>`)}
      </ul>` : html`<${Empty} title="No customers yet"
        will="Each customer the NOC serves appears here with its instructions, workbook, documents and circuits."
        meanwhile="An admin adds a customer here, then puts its elements in it from each element's page." />`}
    </div>`;
  }
}

function CustomerCard({ c }) {
  const href = `#/customers/${c.id}`;
  const parts = [
    ["instructions", c.has_instructions ? "Written" : "None yet", c.has_instructions],
    ["workbook", plural(c.contacts, "contact"), c.contacts > 0],
    ["docs", plural(c.docs, "document"), c.docs > 0],
    ["circuits", plural(c.circuits, "circuit"), c.circuits > 0],
  ];
  return html`<article class="cust-card">
    <a class="cust-card-head" href=${href}>
      <span class="record-icon"><${Icon} name="building" /></span>
      <span class="cust-card-name"><b>${c.name}</b>
        <span class="muted">${plural(c.elements, "element")}${c.has_memory ? " · has a memory" : ""}</span></span>
      ${c.open_cases ? html`<span class="cust-open">${plural(c.open_cases, "open case")}</span>` : null}
    </a>
    <ul class="cust-parts">
      ${parts.map(([part, said, held], i) => html`<li key=${part}>
        <a href=${`${href}/${part}`} class=${cx(!held && "muted")}>
          <${Icon} name=${BOOK[i][2]} /><span>${BOOK[i][1]}</span><b>${said}</b></a></li>`)}
    </ul>
  </article>`;
}

/** An admin's: a new customer is a new organization (`POST /api/organizations`). */
class AddCustomer extends Component {
  constructor(props) {
    super(props);
    this.state = { open: false, name: "", busy: false, error: null };
  }

  slug() { return this.state.name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64); }

  async add(event) {
    event.preventDefault();
    this.setState({ busy: true, error: null });
    try {
      await post("/api/organizations", { name: this.state.name.trim(), slug: this.slug() });
      this.setState({ open: false, name: "", busy: false });
      this.props.onAdded();
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render(_, { open, name, busy, error }) {
    if (!open) {
      return html`<div class="cust-bar"><button type="button" class="primary"
        onClick=${() => this.setState({ open: true })}><${Icon} name="building" /> Add a customer</button></div>`;
    }
    return html`<form class="panel-block cust-add" onSubmit=${(e) => this.add(e)}>
      <label for="cust-name">Customer name</label>
      <div class="cust-add-row">
        <input id="cust-name" maxlength="120" required value=${name} placeholder="Akamai, TIM Brasil…"
          onInput=${(e) => this.setState({ name: e.currentTarget.value })} />
        <button type="submit" class="primary" disabled=${busy || !this.slug()}>${busy ? "Adding…" : "Add"}</button>
        <button type="button" onClick=${() => this.setState({ open: false })}>Cancel</button>
      </div>
      <p class="hint">Its handle will be <span class="mono">${this.slug() || "…"}</span>. Put its elements in it from each element's page.</p>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
    </form>`;
  }
}

/** One customer, at one part of its book. */
class CustomerRecord extends Component {
  constructor(props) {
    super(props);
    this.state = { c: null, error: null };
    this.reload = this.reload.bind(this);
  }

  componentDidMount() { this.reload(); }

  async reload() {
    try {
      this.setState({ c: await get(`/api/noc/customers/${this.props.id}`), error: null });
    } catch (error) {
      this.setState({ error: error.status === 404 ? "No such customer, or none of its elements is yours to see." : error.message });
    }
  }

  changed() { this.reload(); refreshRoster(); }

  highlights(c) {
    const by = (who, at) => (at ? html`<span title=${timeTitle(at)}>${relative(at)}${who ? ` · ${who}` : ""}</span>` : html`<span class="muted">never</span>`);
    return html`<dl class="highlights">
      <div><dt>Instructions</dt><dd>${by(c.updated_by, c.instructions ? c.updated_at : null)}</dd></div>
      <div><dt>NOC memory</dt><dd>${by(c.memory_by, c.memory_at)}</dd></div>
      <div><dt>Workbook</dt><dd>${plural(c.workbook.reduce((n, p) => n + p.contacts.length, 0), "contact")} · ${plural(c.workbook.length, "company", "companies")}</dd></div>
      <div><dt>Documents</dt><dd>${plural(c.docs.length, "file")}</dd></div>
      <div><dt>Circuits</dt><dd>${c.circuits}</dd></div>
      <div><dt>Open cases</dt><dd class=${cx(c.open_cases && "cust-open")}>${c.open_cases}</dd></div>
    </dl>`;
  }

  render({ part, sub, query, navigate }, { c, error }) {
    const back = html`<a class="linkish" href="#/customers">← Customers</a>`;
    if (error) return html`<div>${back}<p class="err" role="alert">${error}</p></div>`;
    if (!c) return html`<p class="muted">Opening the customer…</p>`;
    const go = (to) => navigate(`#/customers/${c.id}/${to}`);
    const shown = BOOK.some(([id]) => id === part) ? part : "instructions";
    return html`<article class="customer-record" aria-labelledby="cust-heading">
      ${back}
      <header class="record-head">
        <span class="record-icon"><${Icon} name="building" /></span>
        <div class="record-title">
          <p class="record-kind">Customer <span class="mono">${c.slug}</span></p>
          <h2 id="cust-heading">${c.name}</h2>
        </div>
        <p class="muted cust-head-note">${plural(c.element_count, "element")} · what its agents read before acting on its network</p>
      </header>
      ${this.highlights(c)}
      <div class="tabs-line" role="tablist" aria-label=${`${c.name}'s book`}>
        ${BOOK.map(([id, label, icon]) => html`<button type="button" role="tab" key=${id}
          aria-selected=${shown === id} class=${cx("tab-line", shown === id && "on")}
          onClick=${() => go(id)}><${Icon} name=${icon} /> ${label}</button>`)}
      </div>
      ${shown === "instructions" ? html`<${BookText} c=${c} onChanged=${() => this.changed()} />` : null}
      ${shown === "workbook" ? html`<${Workbook} c=${c} onChanged=${() => this.changed()} />` : null}
      ${shown === "docs" ? html`<${CustomerDocs} c=${c} onChanged=${() => this.changed()} />` : null}
      ${shown === "circuits" ? html`<${Circuits} c=${c} circuitId=${Number(sub) || null} query=${query}
        navigate=${navigate} onChanged=${() => this.changed()} />` : null}
    </article>`;
  }
}
