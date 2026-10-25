/* Who holds the vault, and which programs are agents (ADR #456). Admins only.
 *
 * * **Custodians** are the people who may store, read and change credentials. Every admin is one,
 *   by role; an editor is one only when an admin names them here. Naming one takes a step-up;
 *   withdrawing never does.
 * * **Agents** are service tokens allowed to ask for a lease on a credential a custodian switched
 *   agent access on for. They never receive a secret. Withdrawing an agent ends its leases at once.
 */

import { html, Component } from "../../dom.js";
import { get, post } from "../../api.js";
import { StepUp } from "./stepup.js";

export class NocGrants extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, pending: null, note: null };
  }

  componentDidMount() { this.read(); }

  async read() {
    try {
      this.setState({ data: await get("/api/noc/grants"), error: null });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  async set(subject, role, enabled, stepup = null) {
    const body = { subject, role, enabled, ...(stepup ? { stepup } : {}) };
    await post("/api/noc/grants", body);
    this.setState({ pending: null, note: null });
    this.read();
  }

  async withdraw(subject, role) {
    try {
      await this.set(subject, role, false);
    } catch (error) {
      this.setState({ note: error.message });
    }
  }

  render({ secondFactor }, { data, error, pending, note }) {
    if (error) return html`<p class="err" role="alert">${error}</p>`;
    if (!data) return html`<p class="muted">Reading custodians and agents…</p>`;
    if (pending) {
      return html`<${StepUp} title=${`Name ${pending.name} ${pending.role === "agent" ? "an agent" : "a custodian"}`}
        action="Confirm" secondFactor=${secondFactor}
        consequence=${pending.role === "agent"
          ? "It may then lease credentials whose agent access is on — never read them."
          : "They may then store, read and change every credential in the vault."}
        onConfirm=${(s) => this.set(pending.subject, pending.role, true, s)}
        onCancel=${() => this.setState({ pending: null })} />`;
    }
    return html`<div class="noc-grants">
      ${note ? html`<p class="err" role="alert">${note}</p>` : null}
      <section class="panel-block">
        <h3>Custodians</h3>
        <p class="hint">People who may store, read and change credentials. Admins are custodians by
          role; name an editor to make them one.</p>
        <ul class="grant-list">${data.people.map((p) => html`<li key=${p.ref}>
          <span class="person-id"><b>${p.display_name || p.username}</b>
            <span class="muted">${p.username}</span></span>
          <span class=${`role-pill role-${p.role}`}>${p.role}</span>
          ${p.by_role ? html`<span class="muted">custodian by role</span>`
            : p.custodian ? html`<button type="button" class="linkish danger-link"
                onClick=${() => this.withdraw(p.ref, "custodian")}>Withdraw custody</button>`
            : html`<button type="button" class="linkish" onClick=${() => this.setState({
                pending: { subject: p.ref, role: "custodian", name: p.display_name || p.username } })}>
                Make custodian…</button>`}
        </li>`)}</ul>
      </section>
      <section class="panel-block">
        <h3>Agents</h3>
        <p class="hint">Service tokens that may ask for a lease. Create the token under People &
          access → Service tokens; a read-only (viewer) token is enough.</p>
        ${data.tokens.length ? html`<ul class="grant-list">${data.tokens.map((t) => html`<li key=${t.ref}>
          <span class="person-id"><b>${t.name}</b>
            <span class="muted">${t.purpose || "no purpose given"}</span></span>
          <span class=${`role-pill role-${t.role}`}>${t.role}</span>
          ${t.agent ? html`<button type="button" class="linkish danger-link"
              onClick=${() => this.withdraw(t.ref, "agent")}>Withdraw — ends its leases</button>`
            : html`<button type="button" class="linkish" onClick=${() => this.setState({
              pending: { subject: t.ref, role: "agent", name: t.name } })}>Make agent…</button>`}
        </li>`)}</ul>` : html`<p class="muted">No live service tokens.</p>`}
      </section>
    </div>`;
  }
}
