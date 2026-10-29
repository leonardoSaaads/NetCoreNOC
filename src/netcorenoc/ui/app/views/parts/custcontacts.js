/* A customer's workbook: who to call, company by company, in the order they are called (ADR #473).
 *
 * Each company — the customer itself, its fibre provider (a Zayo), a vendor — is a card; inside
 * it, its people and desks by escalation level, the first to call at the top. An email is a
 * `mailto:` link and a phone a `tel:` one, so the engineer on shift calls from the page. A role
 * that may not see people is shown that a contact exists, and not its details (the server's
 * `hidden for your role`). Kept by people (`edit_workbook`).
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";
import { can } from "../../session.js";
import { Icon } from "../../icons.js";

const KINDS = [["customer", "The customer"], ["provider", "Provider"], ["vendor", "Vendor"],
  ["other", "Other"]];
const ORDINAL = ["", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th"];
const BLANK = { party: "", party_kind: "customer", escalation_level: 1, name: "", role: "", email: "",
  phone: "", hours: "", notes: "" };
const HIDDEN = "hidden for your role";
/** A company's mark: the customer is a building; a provider, a vendor or anyone else, a network. */
const PARTY_ICON = { customer: "building", provider: "network", vendor: "network", other: "network" };

function Reach({ contact }) {
  const out = [];
  if (contact.phone) {
    out.push(contact.phone === HIDDEN ? html`<span class="muted" key="p">phone ${HIDDEN}</span>`
      : html`<a key="p" href=${`tel:${contact.phone.replace(/[^0-9+]/g, "")}`}><${Icon} name="phone" /> ${contact.phone}</a>`);
  }
  if (contact.email) {
    out.push(contact.email === HIDDEN ? html`<span class="muted" key="e">email ${HIDDEN}</span>`
      : html`<a key="e" href=${`mailto:${contact.email}`}><${Icon} name="mail" /> ${contact.email}</a>`);
  }
  return html`<span class="wb-reach">${out}</span>`;
}

class ContactForm extends Component {
  constructor(props) {
    super(props);
    this.state = { f: { ...BLANK, ...(props.contact || {}), party: (props.contact || {}).party || props.party || "" },
      busy: false, error: null };
  }

  set(name, value) { this.setState({ f: { ...this.state.f, [name]: value } }); }

  async save(event) {
    event.preventDefault();
    const { c, contact } = this.props;
    const f = this.state.f;
    const body = { party: f.party, party_kind: f.party_kind, escalation_level: Number(f.escalation_level),
      name: f.name, role: f.role, email: f.email === HIDDEN ? "" : f.email,
      phone: f.phone === HIDDEN ? "" : f.phone, hours: f.hours, notes: f.notes };
    this.setState({ busy: true, error: null });
    try {
      if (contact) await post(`/api/noc/customers/${c.id}/contacts/${contact.id}`, { ...body, version: contact.version });
      else await post(`/api/noc/customers/${c.id}/contacts`, body);
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ c, contact, onCancel }, { f, busy, error }) {
    const p = `wb-${c.id}-${contact ? contact.id : "new"}`;
    const field = (name, label, attrs = {}) => html`<div class="pe-field">
      <label for=${`${p}-${name}`}>${label}</label>
      <input id=${`${p}-${name}`} value=${f[name]} ...${attrs}
        onInput=${(e) => this.set(name, e.currentTarget.value)} /></div>`;
    return html`<form class="panel-block wb-form" onSubmit=${(e) => this.save(e)}>
      <h3>${contact ? `Change ${contact.name}` : "Add a contact"}</h3>
      <div class="pe-fields">
        ${field("party", "Company", { required: true, maxlength: 120, list: `${p}-parties`, placeholder: "Akamai, Zayo…" })}
        <datalist id=${`${p}-parties`}>${c.workbook.map((w) => html`<option key=${w.party} value=${w.party} />`)}</datalist>
        <div class="pe-field"><label for=${`${p}-kind`}>Which is</label>
          <select id=${`${p}-kind`} value=${f.party_kind} onChange=${(e) => this.set("party_kind", e.currentTarget.value)}>
            ${KINDS.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}</select></div>
        <div class="pe-field"><label for=${`${p}-level`}>Called</label>
          <select id=${`${p}-level`} value=${String(f.escalation_level)}
            onChange=${(e) => this.set("escalation_level", Number(e.currentTarget.value))}>
            ${ORDINAL.slice(1).map((label, i) => html`<option key=${label} value=${String(i + 1)}>${label}</option>`)}</select></div>
        ${field("name", "Name", { required: true, maxlength: 120, placeholder: "A person, or a desk" })}
        ${field("role", "Role", { maxlength: 120, placeholder: "NOC, duty manager…" })}
        ${field("phone", "Phone", { maxlength: 60, type: "tel" })}
        ${field("email", "Email", { maxlength: 254, type: "email" })}
        ${field("hours", "When", { maxlength: 120, placeholder: "24x7, business hours…" })}
        <div class="pe-field case-wide"><label for=${`${p}-notes`}>Notes</label>
          <textarea id=${`${p}-notes`} rows="2" maxlength="2000" value=${f.notes}
            onInput=${(e) => this.set("notes", e.currentTarget.value)}></textarea></div>
      </div>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${busy}>${busy ? "Saving…" : contact ? "Save" : "Add the contact"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}

export class Workbook extends Component {
  constructor(props) {
    super(props);
    this.state = { editing: null, removing: null, busy: false, error: null };
  }

  may() { return can("cases.work") && (this.props.c.actions || []).includes("edit_workbook"); }

  async remove(contact) {
    this.setState({ busy: true, error: null });
    try {
      await post(`/api/noc/customers/${this.props.c.id}/contacts/${contact.id}/delete`, {});
      this.setState({ busy: false, removing: null });
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  row(contact) {
    const { removing, busy } = this.state;
    return html`<li class="wb-row" key=${contact.id}>
      <span class="wb-level" title=${`Called ${ORDINAL[contact.escalation_level]}`}>${ORDINAL[contact.escalation_level]}</span>
      <div class="wb-who">
        <b>${contact.name}</b>${contact.role ? html`<span class="muted"> · ${contact.role}</span>` : null}
        <${Reach} contact=${contact} />
        ${contact.hours || contact.notes ? html`<span class="wb-note muted">${[contact.hours, contact.notes].filter(Boolean).join(" — ")}</span>` : null}
      </div>
      ${this.may() ? html`<span class="wb-acts">
        ${removing === contact.id ? html`<span class="wb-confirm">Remove ${contact.name}?${" "}
            <button type="button" class="danger" disabled=${busy} onClick=${() => this.remove(contact)}>Remove</button>
            <button type="button" onClick=${() => this.setState({ removing: null })}>Keep</button></span>`
          : html`<button type="button" class="linkish" onClick=${() => this.setState({ editing: contact })}>Change</button>
            <button type="button" class="linkish danger-link" onClick=${() => this.setState({ removing: contact.id })}>Remove</button>`}
      </span>` : null}
    </li>`;
  }

  render({ c, onChanged }, { editing, error }) {
    const done = () => { this.setState({ editing: null }); onChanged(); };
    const close = () => this.setState({ editing: null });
    return html`<div class="workbook">
      <div class="part-bar">
        <p class="hint">Who to call for ${c.name}, company by company — the first to call at the top of each.</p>
        ${this.may() && !editing ? html`<button type="button" class="primary"
          onClick=${() => this.setState({ editing: "new" })}><${Icon} name="phone" /> Add a contact</button>` : null}
      </div>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      ${editing ? html`<${ContactForm} key=${editing === "new" ? "new" : editing.id} c=${c}
        contact=${editing === "new" ? null : editing} onDone=${done} onCancel=${close} />` : null}
      ${c.workbook.length ? html`<div class="wb-grid">${c.workbook.map((party) => html`<section
          class="panel-block wb-party" key=${party.party} aria-label=${party.party}>
        <div class="wb-party-head">
          <span class="record-icon wb-mark"><${Icon} name=${PARTY_ICON[party.party_kind] || PARTY_ICON.other} /></span>
          <h3>${party.party}</h3>
          <span class="chip-static">${(KINDS.find(([k]) => k === party.party_kind) || KINDS[3])[1]}</span>
        </div>
        <ol class="wb-list">${party.contacts.map((contact) => this.row(contact))}</ol>
      </section>`)}</div>` : html`<p class="book-empty">No contacts yet.${this.may() ? " Add the customer's NOC and its providers', in the order they are called." : ""}</p>`}
    </div>`;
  }
}
