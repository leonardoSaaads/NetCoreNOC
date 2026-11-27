/* A customer's head: its name (renamed in place), the brokers that point to it, and the three
 * parts of its book the NOC operates by — Access, Email and Routines — drawn larger than the rest
 * (ADR #507).
 *
 * The owner's words: Access is where the logins to the customer's hosts are kept, Email is how
 * every case talks to the customer, and Routines are the heart of the NOC — the troubleshooting
 * trees agents run and improve. So they are not three more tabs in a row: each is a card with what
 * it is for and what it holds now, and choosing one opens it below like any other tab.
 */

import { html, Component, cx } from "../../../dom.js";
import { get, post } from "../../../api.js";
import { Icon } from "../../../icons.js";
import { can } from "../../../session.js";
import { plural } from "../../../format.js";

/** The three parts the NOC operates by: id, title, icon, what it is for. */
export const KEY_PARTS = [
  ["access", "Access", "vault", "The logins to its hosts — SSH, SNMP, Telnet, SFTP, APIs — sealed in the vault."],
  ["email", "Email", "mail", "The mailbox every case writes from and reads the customer's replies in."],
  ["routines", "Routines", "playbook", "Troubleshooting trees agents run in one call and improve, case after case."],
];

/** Which brokers point to this customer, linked; read once per customer page. */
export class CustomerBrokers extends Component {
  constructor(props) {
    super(props);
    this.state = { brokers: null };
  }

  async componentDidMount() {
    try {
      const all = (await get("/api/brokers")).brokers || [];
      this.setState({ brokers: all.filter((b) => b.organization_id === this.props.c.id) });
    } catch { this.setState({ brokers: [] }); }
  }

  render({ c }, { brokers }) {
    if (brokers === null) return null;
    return html`<p class="cust-brokers">
      <${Icon} name="network" />
      ${brokers.length
        ? html`<span>${plural(brokers.length, "broker")} ${brokers.length === 1 ? "points" : "point"} here:</span>
          ${brokers.map((b) => html`<a key=${b.id} class="chip-static" href=${`#/brokers/${b.id}`}>${b.name}</a>`)}`
        : html`<span class="muted">No broker points to ${c.name} yet — choose it as the customer on a
            broker's page.</span>`}
    </p>`;
  }
}

/** The customer's name, renamed in place by an admin (`POST /api/organizations/{id}/name`). */
export class CustomerName extends Component {
  constructor(props) {
    super(props);
    this.state = { editing: false, name: props.c.name, busy: false, error: null };
  }

  async save(event) {
    event.preventDefault();
    this.setState({ busy: true, error: null });
    try {
      await post(`/api/organizations/${this.props.c.id}/name`, { name: this.state.name.trim() });
      this.setState({ editing: false, busy: false });
      this.props.onRenamed();
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  render({ c }, { editing, name, busy, error }) {
    if (!editing) {
      return html`<h2 id="cust-heading" class="cust-name-line">${c.name}
        ${c.is_default ? html`<span class="badge" title="Every broker that points nowhere else points here">default</span>` : null}
        ${can("organizations.write") ? html`<button type="button" class="btn-ghost cust-rename"
          aria-label=${`Rename ${c.name}`} title="Rename"
          onClick=${() => this.setState({ editing: true, name: c.name })}><${Icon} name="edit" /></button>` : null}
      </h2>`;
    }
    return html`<form class="cust-rename-form" onSubmit=${(e) => this.save(e)}>
      <label for="cust-rename" class="visually-hidden">New name for ${c.name}</label>
      <input id="cust-rename" maxlength="120" required value=${name}
        onInput=${(e) => this.setState({ name: e.currentTarget.value })} />
      <button type="submit" class="primary" disabled=${busy || !name.trim()}>${busy ? "Saving…" : "Rename"}</button>
      <button type="button" onClick=${() => this.setState({ editing: false, error: null })}>Cancel</button>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
    </form>`;
  }
}

/** The three operated parts as cards that open their tab: what each is for, what it holds now. */
export function KeyParts({ c, shown, go }) {
  const said = {
    access: c.access_count == null ? "Open to see its credentials" : plural(c.access_count, "credential"),
    email: c.mail_ready ? `Sending as ${c.mail_sender}` : "Not set up yet",
    routines: c.routine_count == null ? "Open to see its routines" : plural(c.routine_count, "routine"),
  };
  return html`<nav class="cust-keys" aria-label="What the NOC operates by">
    ${KEY_PARTS.map(([id, title, icon, what]) => html`<button type="button" key=${id}
      class=${cx("cust-key", `cust-key-${id}`, shown === id && "on")} aria-pressed=${shown === id}
      onClick=${() => go(id)}>
      <span class="cust-key-icon"><${Icon} name=${icon} /></span>
      <span class="cust-key-text"><b>${title}</b><span>${what}</span>
        <em class=${cx(id === "email" && !c.mail_ready && "cust-key-todo")}>${said[id]}</em></span>
    </button>`)}
  </nav>`;
}
