/* People & access — the Service tokens tab (v0.25.0, ADR #404).
 *
 * A service token is how a program — Grafana, a script, a ticketing system — reads or acts on the
 * API without a person signing in. It carries a role like a user does, can be narrowed like a user
 * can (its own entry in the capability policy), and is shown **once**: the appliance keeps only its
 * hash. The screen is built around those three facts — create with a name, a purpose and a role;
 * copy the value from the one place it ever appears, with a working example beside it; revoke.
 *
 * v0.30.0: and what to do with it — a three-step start and the API reference (`apiref.js`), read
 * from the appliance's own OpenAPI schema and narrowed to what a chosen role or token may call.
 */

import { html, Component, cx } from "../../dom.js";
import { post, del } from "../../api.js";
import { InfoTip } from "../../info.js";
import { Destructive } from "../../destructive.js";
import { can } from "../../session.js";
import { relative, timeTitle } from "../../format.js";
import { CapGrid, sameSet } from "./capgrid.js";
import { RoleSwitch, accessOf } from "./people.js";
import { ApiReference, QuickStart } from "./apiref.js";

class Issued extends Component {
  constructor(props) {
    super(props);
    this.state = { copied: false };
  }

  async copy() {
    try {
      await globalThis.navigator.clipboard.writeText(this.props.token.token);
      this.setState({ copied: true });
    } catch { /* the value is selectable in the field */ }
  }

  render({ token, onDone }, { copied }) {
    const host = globalThis.location ? globalThis.location.origin : "https://<appliance>";
    return html`<div class="token-issued" role="status">
      <p><b>Copy the token for “${token.name}” now.</b> It will not be shown again.</p>
      <div class="token-value">
        <input readonly value=${token.token} aria-label="Token value"
          onFocus=${(e) => e.currentTarget.select()} />
        <button type="button" class="primary" onClick=${() => this.copy()}>${copied ? "Copied" : "Copy"}</button>
      </div>
      <p class="muted">Use it as a bearer token — kept in a variable, not in a script:</p>
      <pre class="token-example">export NETCORENOC_TOKEN='${token.token.slice(0, 6)}…'
curl -H "Authorization: Bearer $NETCORENOC_TOKEN" ${host}/api/me</pre>
      <button type="button" onClick=${onDone}>Done</button>
    </div>`;
  }
}

export class TokensPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { name: "", purpose: "", role: "viewer", busy: false, error: null, issued: null,
      open: null, caps: null };
  }

  async create(event) {
    event.preventDefault();
    const { name, purpose, role } = this.state;
    this.setState({ busy: true, error: null });
    try {
      const issued = await post("/api/tokens", { name: name.trim(), purpose: purpose.trim() || null, role });
      this.setState({ busy: false, issued, name: "", purpose: "" });
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  async saveAccess(token, caps) {
    const base = accessOf(this.props.rbac, token.role, null);
    const custom = !sameSet(caps, base.value);
    await post("/api/rbac/subject", { subject: `token:${token.id}`,
      capabilities: custom ? [...caps].sort() : null });
    this.setState({ open: null, caps: null });
    this.props.onChanged();
  }

  render({ tokens, rbac }, { name, purpose, role, busy, error, issued, open, caps }) {
    const active = tokens.filter((t) => !t.revoked);
    return html`<div class="tokens">
      ${issued ? html`<${Issued} token=${issued} onDone=${() => this.setState({ issued: null })} />` : null}
      <${QuickStart} />
      <form class="token-form panel-block" onSubmit=${(e) => this.create(e)} autocomplete="off">
        <h3>New token <${InfoTip} label="What is a service token?">A credential for a program —
          Grafana, a script, a ticketing system — to use the API without a person signing in. It
          acts with the role you choose, is shown once and can be revoked at any time.<//></h3>
        <div class="token-fields">
          <label class="pe-field"><span>Name</span>
            <input value=${name} required maxlength="64" placeholder="grafana"
              onInput=${(e) => this.setState({ name: e.currentTarget.value })} /></label>
          <label class="pe-field token-purpose"><span>Purpose</span>
            <input value=${purpose} maxlength="200" placeholder="Dashboards on the NOC wall"
              onInput=${(e) => this.setState({ purpose: e.currentTarget.value })} /></label>
          <div class="pe-field"><span>Role</span>
            <${RoleSwitch} value=${role} name="Token role" onChange=${(r) => this.setState({ role: r })} /></div>
          <button type="submit" class="primary" disabled=${busy}>${busy ? "Creating…" : "Create token"}</button>
        </div>
        ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      </form>
      <ul class="token-list">
        ${tokens.map((t) => {
          const access = accessOf(rbac, t.role, `token:${t.id}`);
          const editing = open === t.id && access;
          return html`<li key=${t.id} class=${cx("token-row", t.revoked && "revoked")}>
            <div class="token-main">
              <span class="token-icon" aria-hidden="true">⚿</span>
              <span class="person-id"><b>${t.name}</b>
                <span class="muted">${t.purpose || "no purpose given"}</span></span>
              <span class=${`role-pill role-${t.role}`}>${t.role}</span>
              <span class="muted" title=${timeTitle(t.last_used_at)}>${t.revoked ? "revoked"
                : t.last_used_at ? `used ${relative(t.last_used_at)}` : "never used"}</span>
              <span class="muted" title=${timeTitle(t.created_at)}>by ${t.created_by || "—"}</span>
              ${t.revoked ? null : html`<span class="token-acts">
                ${access && can("rbac.write") ? html`<button type="button" class="linkish"
                  onClick=${() => this.setState({ open: editing ? null : t.id, caps: access.value })}>
                  Access${access.custom ? " · custom" : ""}</button>` : null}
                <${Destructive} compact title=${`Revoke ${t.name}`} previewLabel="Revoke…"
                  confirmLabel=${`Revoke ${t.name}`}
                  consequence=${"Anything using this token loses access at once; its value cannot be recovered."}
                  preview=${async () => ({ measured: false, message: "Exactly this credential." })}
                  apply=${async () => del(`/api/tokens/${t.id}`)} onDone=${this.props.onChanged} />
              </span>`}
            </div>
            ${editing ? html`<div class="token-access">
              <${CapGrid} all=${rbac.all_capabilities} ceiling=${access.ceiling} baseline=${access.baseline}
                minimum=${rbac.minimum_role} value=${caps} roleName=${t.role}
                onChange=${(next) => this.setState({ caps: next })} />
              <div class="roles-foot">
                <button type="button" class="primary" onClick=${() => this.saveAccess(t, caps)}>Save access</button>
                <button type="button" onClick=${() => this.setState({ open: null })}>Cancel</button>
              </div>
            </div>` : null}
          </li>`;
        })}
      </ul>
      ${tokens.length ? html`<p class="muted">${active.length} active of ${tokens.length}.</p>`
        : html`<p class="muted">No tokens yet.</p>`}
      <${ApiReference} rbac=${rbac} tokens=${tokens} />
    </div>`;
  }
}
