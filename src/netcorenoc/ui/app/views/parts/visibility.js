/* People & access — the Visibility tab: which network elements viewers and editors see
 * (v0.25.0; the scope half of the v0.7.0 Governance screen, ADR #403).
 *
 * The scope policy is a list of selectors per role or person, so it stays a document: a small,
 * reviewable JSON with its history and a restore per version. The one sentence that must never be
 * hidden is kept on the screen, not behind a tip: scoping is presentation, not tenant isolation.
 *
 * v0.30.0: worked examples beside the editor (`scopeexamples.js`) — the common shapes as complete
 * documents, the selector forms, and the ids a principal entry names.
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";
import { InfoTip } from "../../info.js";
import { can } from "../../session.js";
import { plural, relative, timeTitle } from "../../format.js";
import { ScopeExamples } from "./scopeexamples.js";

const BLANK = JSON.stringify({ version: 1, roles: {}, principals: {} }, null, 2);

export class VisibilityPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { text: props.scope.active ? props.scope.active.document : BLANK, error: null,
      busy: false };
  }

  async write(body) {
    this.setState({ busy: true, error: null });
    try {
      await post("/api/scope", body);
      this.setState({ busy: false });
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  apply() {
    let document_;
    try { document_ = JSON.parse(this.state.text); }
    catch (parseError) { this.setState({ error: `Not valid JSON: ${parseError.message}` }); return; }
    this.write({ document: document_, note: "" });
  }

  render({ scope, users, tokens }, { text, error, busy }) {
    const writable = can("scope.write");
    return html`<div class="visibility">
      <p class="warnbox">Visibility is a presentation control, <b>not tenant isolation</b>.
        <${InfoTip} label="Why">Correlation still learns across every element, and a situation may
          still form across a boundary someone cannot see; its hidden members are shown as a count.<//></p>
      ${scope.malformed ? html`<p class="err" role="alert">The stored scope could not be read
        (${scope.malformed_reason}); viewers and editors see nothing until it is fixed or cleared.</p>` : null}
      <div class="roles-cards">
        ${["viewer", "editor"].map((role) => {
          const ids = (scope.resolved_ne_ids || {})[role] || [];
          return html`<div key=${role} class="role-card static"><b>${role}</b>
            <span>${scope.configured ? `${ids.length} of ${scope.ne_count}` : `all ${plural(scope.ne_count, "element")}`}</span></div>`;
        })}
        <div class="role-card static"><b>admin</b><span>everything, always</span></div>
      </div>
      <label class="pe-field"><span>Policy
        <${InfoTip} label="Selectors">Per role or person (${"user:<id>"}): ${"ne:<id>"}, an address,
          a CIDR such as 10.1.0.0/16, or a glob such as 10.0.*. Labels never match. Examples below.<//></span>
        <textarea class="govjson" rows="8" value=${text} readonly=${!writable}
          onInput=${(e) => this.setState({ text: e.currentTarget.value })}></textarea></label>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      ${writable ? html`<div class="roles-foot">
        <button type="button" class="primary" disabled=${busy} onClick=${() => this.apply()}>Apply</button>
        ${scope.configured ? html`<button type="button" disabled=${busy}
          onClick=${() => this.write({ clear: true })}>Show everything</button>` : null}
      </div>` : null}
      <${ScopeExamples} users=${users} tokens=${tokens} writable=${writable}
        onUse=${(next) => this.setState({ text: next, error: null })} />
      ${(scope.history || []).length ? html`<ol class="roles-history">
        ${scope.history.map((h) => html`<li key=${h.id}>
          <span class="mono">v${h.id}</span><span>${h.note || "scope"}</span>
          <span class="muted" title=${timeTitle(h.created_at)}>${h.created_by} · ${relative(h.created_at)}</span>
          ${h.active ? html`<span class="badge">active</span>` : writable ? html`<button type="button"
            class="linkish" onClick=${() => this.write({ policy_id: h.id })}>Restore</button>` : null}
        </li>`)}
      </ol>` : null}
    </div>`;
  }
}
