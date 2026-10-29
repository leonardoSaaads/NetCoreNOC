/* NOC → Secrets → People & access: who holds what in the NOC beyond their role (ADRs #456, #470).
 * Admins only (`secrets.grants`).
 *
 * One row per person and per program, and one switch per grant, each saying what it opens:
 *
 * * **Vault custodian** — may store, read and change credentials. Every admin is one, by role; an
 *   editor is one only when an admin switches it on here.
 * * **NOC lead** — may open the NOC's KPIs and its playbooks (ADR #470). Every admin is one, by
 *   role; an editor only when named here.
 * * **Agent** (a service token) — may ask for a lease on a credential a custodian switched agent
 *   access on for, and read the KPIs and playbooks. It never receives a secret; switching it off
 *   ends its leases at once.
 *
 * Switching a grant on takes a step-up; switching one off never does — withdrawing access must
 * never be the hard direction.
 */

import { html, Component } from "../../dom.js";
import { get, post } from "../../api.js";
import { Avatar } from "../../avatar.js";
import { Icon } from "../../icons.js";
import { StepUp } from "./stepup.js";
import { Toggle } from "./vaultui.js";

const WHAT = {
  custodian: ["Vault custodian", "They may then store, read and change every credential in the vault."],
  lead: ["NOC lead", "They may then open the NOC's KPIs and read and write its playbooks."],
  agent: ["Agent", "It may then lease credentials whose agent access is on — never read them — and read the KPIs and playbooks."],
};

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

  /** A grant's switch: on asks for a step-up; off happens at once; an admin's is by role. */
  grant(subject, name, role, on, byRole) {
    const [label] = WHAT[role];
    if (byRole) return html`<span class="grant-by-role" title="Every admin holds it"><${Icon} name="check" /> ${label}</span>`;
    return html`<${Toggle} on=${on} label=${label}
      hint=${on ? `Switch ${label.toLowerCase()} off for ${name}` : `Make ${name} a ${label.toLowerCase()}`}
      onToggle=${() => (on ? this.withdraw(subject, role)
        : this.setState({ pending: { subject, role, name } }))} />`;
  }

  render({ secondFactor }, { data, error, pending, note }) {
    if (error) return html`<p class="err" role="alert">${error}</p>`;
    if (!data) return html`<p class="muted">Reading who holds what…</p>`;
    if (pending) {
      const [label, consequence] = WHAT[pending.role];
      return html`<${StepUp} title=${`Make ${pending.name} ${pending.role === "agent" ? "an" : "a"} ${label.toLowerCase()}`}
        action="Confirm" secondFactor=${secondFactor} consequence=${consequence}
        onConfirm=${(s) => this.set(pending.subject, pending.role, true, s)}
        onCancel=${() => this.setState({ pending: null })} />`;
    }
    return html`<div class="noc-grants">
      ${note ? html`<p class="err" role="alert">${note}</p>` : null}
      <section class="panel-block">
        <h3><${Icon} name="users" /> People</h3>
        <p class="hint">Admins hold both by role. Name an editor to give them the vault, the NOC's
          measures and playbooks, or both. Switching on asks you to confirm who you are.</p>
        <ul class="grant-list">${data.people.map((p) => {
          const name = p.display_name || p.username;
          return html`<li key=${p.ref} class="grant-row">
            <${Avatar} id=${null} name=${p.display_name} username=${p.username} size=${32} />
            <span class="person-id"><b>${name}</b><span class="muted">${p.username}</span></span>
            <span class=${`role-pill role-${p.role}`}>${p.role}</span>
            <span class="grant-switches">
              ${this.grant(p.ref, name, "custodian", p.custodian, p.by_role)}
              ${this.grant(p.ref, name, "lead", p.lead, p.by_role)}
            </span>
          </li>`;
        })}</ul>
      </section>
      <section class="panel-block">
        <h3><${Icon} name="bot" /> Agents and programs</h3>
        <p class="hint">Service tokens that may ask for a lease. An agent hired under Agents is one
          already; any other token is created under People & access → Service tokens.</p>
        ${data.tokens.length ? html`<ul class="grant-list">${data.tokens.map((t) => html`<li key=${t.ref} class="grant-row">
          <span class="grant-token" aria-hidden="true"><${Icon} name="tokens" /></span>
          <span class="person-id"><b>${t.name}</b><span class="muted">${t.purpose || "no purpose given"}</span></span>
          <span class=${`role-pill role-${t.role}`}>${t.role}</span>
          <span class="grant-switches">${this.grant(t.ref, t.name, "agent", t.agent, false)}</span>
        </li>`)}</ul>` : html`<p class="muted">No live service tokens.</p>`}
      </section>
    </div>`;
  }
}
