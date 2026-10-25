/* A credential, new or changed: what it is, where it works, what it lets its holder do (ADR #455).
 *
 * The form draws itself from the catalogue the server sends (`catalogue.kinds`): the fields of
 * each kind, which of them are secret, which are required, the default port and the protocol
 * versions. Nothing about a kind is written here, so the form cannot offer a field the server
 * would refuse or miss one it requires.
 *
 * **Secret fields are never pre-filled.** Changing a credential's name, host or access level keeps
 * the sealed secret (the server re-seals it under the new metadata); replacing the secret is a
 * separate, explicit choice. Either way a change asks for a step-up first.
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";
import { PasswordInput } from "../../password.js";
import { StepUp } from "./stepup.js";

let formSeq = 0;

function blank(kind) {
  return Object.fromEntries((kind ? kind.fields : []).map((f) => [f.name, ""]));
}

export class SecretForm extends Component {
  constructor(props) {
    super(props);
    formSeq += 1;
    this.prefix = `sf-${formSeq}`;
    const s = props.secret;
    const kinds = props.catalogue.kinds;
    const kind = kinds.find((k) => k.id === (s ? s.kind : "ssh_password")) || kinds[0];
    this.state = {
      kind: kind.id, name: s ? s.name : "", description: s ? s.description : "",
      access_level: s ? s.access_level : "view_only", host: s ? s.host : "",
      port: s ? String(s.port) : "", protocol_version: s ? s.protocol_version : "",
      monitor: s ? s.monitor : true, fields: blank(kind), replace: !s,
      stepping: false, busy: false, error: null,
    };
  }

  kind() {
    return this.props.catalogue.kinds.find((k) => k.id === this.state.kind);
  }

  setKind(id) {
    const kind = this.props.catalogue.kinds.find((k) => k.id === id);
    this.setState({ kind: id, fields: blank(kind), protocol_version: "", replace: true });
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

  render({ catalogue, secret, onCancel, secondFactor }, st) {
    const kind = this.kind();
    const p = this.prefix;
    if (st.stepping) {
      return html`<${StepUp} title=${`Save changes to “${secret.name}”`} action="Save changes"
        consequence=${st.replace ? "The stored secret is replaced, and every lease on it ends."
          : "The secret is kept, re-sealed if where it works changed."}
        secondFactor=${secondFactor} onConfirm=${(s) => this.confirmed(s)}
        onCancel=${() => this.setState({ stepping: false })} />`;
    }
    return html`<form class="secret-form panel-block" onSubmit=${(e) => this.submit(e)} autocomplete="off">
      <h3>${secret ? `Change “${secret.name}”` : "New credential"}</h3>
      <div class="pe-fields">
        <div class="pe-field"><label for=${`${p}-kind`}>Type</label>
          <select id=${`${p}-kind`} value=${st.kind} onChange=${(e) => this.setKind(e.currentTarget.value)}>
            ${catalogue.kinds.map((k) => html`<option key=${k.id} value=${k.id}>${k.label}</option>`)}
          </select></div>
        <div class="pe-field"><label for=${`${p}-access`}>Access it grants</label>
          <select id=${`${p}-access`} value=${st.access_level}
            onChange=${(e) => this.setState({ access_level: e.currentTarget.value })}>
            ${catalogue.access_levels.map((a) => html`<option key=${a.id} value=${a.id}>${a.label}</option>`)}
          </select></div>
        <div class="pe-field"><label for=${`${p}-name`}>Name</label>
          <input id=${`${p}-name`} value=${st.name} required maxlength="80" placeholder="core-1 SSH (root)"
            onInput=${(e) => this.setState({ name: e.currentTarget.value })} /></div>
        <div class="pe-field"><label for=${`${p}-host`}>Host</label>
          <input id=${`${p}-host`} value=${st.host} required maxlength="253" placeholder="10.0.0.1 or core-1.example.net"
            spellcheck=${false} onInput=${(e) => this.setState({ host: e.currentTarget.value })} /></div>
        <div class="pe-field"><label for=${`${p}-port`}>Port</label>
          <input id=${`${p}-port`} value=${st.port} inputmode="numeric" maxlength="5" placeholder=${String(kind.port)}
            onInput=${(e) => this.setState({ port: e.currentTarget.value.replace(/[^0-9]/g, "") })} /></div>
        ${kind.versions.length ? html`<div class="pe-field"><label for=${`${p}-ver`}>Protocol version</label>
          <select id=${`${p}-ver`} value=${st.protocol_version || kind.versions[0]}
            onChange=${(e) => this.setState({ protocol_version: e.currentTarget.value })}>
            ${kind.versions.map((v) => html`<option key=${v} value=${v}>${v}</option>`)}
          </select></div>` : null}
        <div class="pe-field secret-desc"><label for=${`${p}-desc`}>Description (optional)</label>
          <input id=${`${p}-desc`} value=${st.description} maxlength="300"
            onInput=${(e) => this.setState({ description: e.currentTarget.value })} /></div>
      </div>
      <label class="check-row"><input type="checkbox" checked=${st.monitor}
        onChange=${(e) => this.setState({ monitor: e.currentTarget.checked })} />
        Check that the endpoint answers, every few minutes${kind.checkable ? "" : " (not possible for this type)"}</label>
      ${secret ? html`<label class="check-row"><input type="checkbox" checked=${st.replace}
        onChange=${(e) => this.setState({ replace: e.currentTarget.checked, fields: blank(kind) })} />
        Replace the stored secret</label>` : null}
      ${st.replace ? html`<fieldset class="secret-fields">
        <legend>The credential — sealed twice, never shown in a list</legend>
        <div class="pe-fields">${kind.fields.map((f) => this.field(f))}</div>
      </fieldset>` : null}
      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${st.busy}>
          ${st.busy ? "Saving…" : secret ? "Continue" : "Store credential"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}
