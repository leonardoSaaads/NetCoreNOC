/* A credential, new or changed: what it is, where it works, what it lets its holder do (ADRs #455,
 * #471).
 *
 * **New** starts with what it is: a card per kind — its mark, its name, what it is for, its port —
 * because a person knows "I want to SSH into the core" before they know a field name. Then three
 * short parts, in the order they are thought of: where it works; the secret itself (sealed twice,
 * never shown in a list); what it lets its holder do, as cards that say it. An AI provider's key is
 * added from its own tab (`aiproviders.js`), but changed here like any other credential: its host
 * fixed where the provider's API is, its access meaningless and hidden.
 *
 * The form draws itself from the catalogue the server sends (`catalogue.kinds`): the fields of each
 * kind, which are secret, which are required, the default port and the protocol versions. Nothing
 * about a kind's fields is written here, so the form cannot offer a field the server would refuse
 * or miss one it requires.
 *
 * **Secret fields are never pre-filled.** Changing a credential's name, host or access level keeps
 * the sealed secret (the server re-seals it under the new metadata); replacing the secret is a
 * separate, explicit choice. Either way a change asks for a step-up first.
 */

import { html, Component, cx } from "../../dom.js";
import { post } from "../../api.js";
import { PasswordInput } from "../../password.js";
import { StepUp } from "./stepup.js";
import { AccessCards, KINDS, KindMark, providerOf } from "./vaultui.js";

let formSeq = 0;

function blank(kind) {
  return Object.fromEntries((kind ? kind.fields : []).map((f) => [f.name, ""]));
}

/** The device kinds as cards: what a person picks first. */
export function KindPicker({ kinds, onPick, onCancel }) {
  return html`<section class="kind-picker panel-block" aria-labelledby="kind-picker-heading">
    <h3 id="kind-picker-heading">What does this credential open?</h3>
    <div class="kind-grid">${kinds.map((k) => html`<button type="button" key=${k.id} class="kind-card"
        onClick=${() => onPick(k.id)}>
      <${KindMark} kind=${k.id} size=${36} />
      <span class="kind-card-text"><b>${k.label}</b>
        <span>${(KINDS[k.id] || KINDS.other).help}</span>
        <span class="muted mono">${k.transport.toUpperCase()} ${k.port}</span></span>
    </button>`)}</div>
    <div class="stepup-acts"><button type="button" onClick=${onCancel}>Cancel</button></div>
  </section>`;
}

export class SecretForm extends Component {
  constructor(props) {
    super(props);
    formSeq += 1;
    this.prefix = `sf-${formSeq}`;
    const s = props.secret;
    const kind = s ? this.find(s.kind) : null;
    this.state = {
      kind: kind ? kind.id : null, name: s ? s.name : "", description: s ? s.description : "",
      access_level: s ? s.access_level : "view_only", host: s ? s.host : "",
      port: s ? String(s.port) : "", protocol_version: s ? s.protocol_version : "",
      monitor: s ? s.monitor : true, fields: blank(kind), replace: !s,
      stepping: false, busy: false, error: null,
    };
  }

  find(id) { return this.props.catalogue.kinds.find((k) => k.id === id) || null; }

  kind() { return this.find(this.state.kind); }

  /** The provider an AI key is for, from the catalogue, or null. */
  provider() {
    const id = providerOf(this.state.kind);
    return id ? (this.props.catalogue.providers || []).find((p) => p.id === id) || null : null;
  }

  setKind(id) {
    const kind = this.find(id);
    this.setState({ kind: id, fields: blank(kind), protocol_version: "", replace: true, error: null });
  }

  body() {
    const st = this.state;
    return {
      name: st.name.trim(), description: st.description.trim(), kind: st.kind,
      access_level: st.access_level, host: st.host.trim(),
      port: st.port ? Number(st.port) : null, protocol_version: st.protocol_version,
      monitor: st.monitor, fields: st.replace ? st.fields : null,
    };
  }

  async submit(event) {
    event.preventDefault();
    if (this.props.secret) { this.setState({ stepping: true, error: null }); return; }
    this.setState({ busy: true, error: null });
    try {
      const saved = await post("/api/noc/secrets", this.body());
      this.setState({ busy: false, fields: blank(this.kind()) });
      this.props.onSaved(saved);
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  async confirmed(stepup) {
    const saved = await post(`/api/noc/secrets/${this.props.secret.id}`, { ...this.body(), stepup });
    this.setState({ stepping: false, fields: blank(this.kind()) });
    this.props.onSaved(saved);
  }

  field(f) {
    const id = `${this.prefix}-${f.name}`;
    const value = this.state.fields[f.name] || "";
    const set = (v) => this.setState({ fields: { ...this.state.fields, [f.name]: v } });
    const label = `${f.label}${f.required ? "" : " (optional)"}`;
    if (f.secret && !f.multiline) {
      return html`<${PasswordInput} key=${f.name} id=${id} label=${label} value=${value}
        autocomplete="new-password" onInput=${set} />`;
    }
    let control;
    if (f.choices.length) {
      control = html`<select id=${id} value=${value} required=${f.required}
        onChange=${(e) => set(e.currentTarget.value)}>
        <option value="">Choose…</option>
        ${f.choices.map((c) => html`<option key=${c} value=${c}>${c}</option>`)}
      </select>`;
    } else if (f.multiline) {
      control = html`<textarea id=${id} rows="6" maxlength=${f.max_len} spellcheck=${false}
        class=${f.secret ? "mono secret-text" : "mono"} value=${value}
        onInput=${(e) => set(e.currentTarget.value)}></textarea>`;
    } else {
      control = html`<input id=${id} value=${value} maxlength=${f.max_len} autocomplete="off"
        spellcheck=${false} onInput=${(e) => set(e.currentTarget.value)} />`;
    }
    return html`<div class="pe-field" key=${f.name}><label for=${id}>${label}</label>${control}</div>`;
  }

  where(kind, provider) {
    const st = this.state;
    const p = this.prefix;
    const fixed = provider && !provider.own_host;
    return html`<fieldset class="sf-part">
      <legend><span class="sf-step">1</span> Where it works</legend>
      <div class="pe-fields">
        <div class="pe-field"><label for=${`${p}-name`}>Name</label>
          <input id=${`${p}-name`} value=${st.name} required maxlength="80"
            placeholder=${provider ? `${provider.label} — production` : "core-1 SSH (root)"}
            onInput=${(e) => this.setState({ name: e.currentTarget.value })} /></div>
        ${fixed ? html`<div class="pe-field"><span>Endpoint</span>
          <p class="mono sf-fixed">${provider.host}:${provider.port}</p></div>` : html`
        <div class="pe-field"><label for=${`${p}-host`}>Host</label>
          <input id=${`${p}-host`} value=${st.host} required maxlength="253"
            placeholder=${provider ? provider.host : "10.0.0.1 or core-1.example.net"}
            spellcheck=${false} onInput=${(e) => this.setState({ host: e.currentTarget.value })} /></div>
        <div class="pe-field"><label for=${`${p}-port`}>Port</label>
          <input id=${`${p}-port`} value=${st.port} inputmode="numeric" maxlength="5" placeholder=${String(kind.port)}
            onInput=${(e) => this.setState({ port: e.currentTarget.value.replace(/[^0-9]/g, "") })} /></div>`}
        ${kind.versions.length > 1 ? html`<div class="pe-field"><label for=${`${p}-ver`}>Protocol version</label>
          <select id=${`${p}-ver`} value=${st.protocol_version || kind.versions[0]}
            onChange=${(e) => this.setState({ protocol_version: e.currentTarget.value })}>
            ${kind.versions.map((v) => html`<option key=${v} value=${v}>${v}</option>`)}
          </select></div>` : null}
        <div class="pe-field secret-desc"><label for=${`${p}-desc`}>Description (optional)</label>
          <input id=${`${p}-desc`} value=${st.description} maxlength="300"
            onInput=${(e) => this.setState({ description: e.currentTarget.value })} /></div>
      </div>
    </fieldset>`;
  }

  render({ catalogue, secret, onCancel, secondFactor }, st) {
    if (st.stepping) {
      return html`<${StepUp} title=${`Save changes to “${secret.name}”`} action="Save changes"
        consequence=${st.replace ? "The stored secret is replaced, and every lease on it ends."
          : "The secret is kept, re-sealed if where it works changed."}
        secondFactor=${secondFactor} onConfirm=${(s) => this.confirmed(s)}
        onCancel=${() => this.setState({ stepping: false })} />`;
    }
    const kind = this.kind();
    if (!kind) {
      return html`<${KindPicker} kinds=${catalogue.kinds.filter((k) => k.group !== "ai")}
        onPick=${(id) => this.setKind(id)} onCancel=${onCancel} />`;
    }
    const provider = this.provider();
    const p = this.prefix;
    return html`<form class="secret-form panel-block" onSubmit=${(e) => this.submit(e)} autocomplete="off">
      <div class="sf-head">
        <${KindMark} kind=${kind.id} size=${40} />
        <div><h3>${secret ? `Change “${secret.name}”` : `New ${kind.label}`}</h3>
          <p class="muted">${provider ? `The key ${provider.label} issues for its API.`
            : (KINDS[kind.id] || KINDS.other).help}</p></div>
        ${secret ? null : html`<button type="button" class="linkish"
          onClick=${() => this.setState({ kind: null })}>Change type</button>`}
      </div>
      ${this.where(kind, provider)}
      <fieldset class="sf-part">
        <legend><span class="sf-step">2</span> The secret — sealed twice, never shown in a list</legend>
        ${secret ? html`<label class="check-row"><input type="checkbox" checked=${st.replace}
          onChange=${(e) => this.setState({ replace: e.currentTarget.checked, fields: blank(kind) })} />
          <span>Replace the stored secret</span></label>` : null}
        ${st.replace ? html`<div class="pe-fields">${kind.fields.map((f) => this.field(f))}</div>`
          : html`<p class="muted">The stored secret is kept.</p>`}
      </fieldset>
      ${provider ? null : html`<fieldset class="sf-part">
        <legend><span class="sf-step">3</span> What it lets its holder do</legend>
        <${AccessCards} levels=${catalogue.access_levels} value=${st.access_level} name=${`${p}-access`}
          onChange=${(access_level) => this.setState({ access_level })} />
      </fieldset>`}
      <label class="check-row"><input type="checkbox" checked=${st.monitor}
        onChange=${(e) => this.setState({ monitor: e.currentTarget.checked })} />
        <span>Check that the endpoint answers, every few minutes${kind.checkable ? "" : " (not possible for this type)"}</span></label>
      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class=${cx("stepup-acts", "sf-acts")}>
        <button type="submit" class="primary" disabled=${st.busy}>
          ${st.busy ? "Saving…" : secret ? "Continue" : "Store credential"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}
