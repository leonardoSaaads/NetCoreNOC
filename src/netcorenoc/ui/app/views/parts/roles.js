/* People & access — the Roles tab: what each role holds by default (v0.25.0, ADR #403).
 *
 * The capability policy used to be edited as JSON. It is the same policy — a role's entry narrows
 * that role — drawn as the grid a person's access uses, one role at a time, with how many of the
 * role's ceiling it keeps. "Shipped default" removes the role's entry, so it holds its whole
 * ceiling again. Every save is a new version; the history keeps them all and any can be restored.
 */

import { html, Component, cx } from "../../dom.js";
import { post } from "../../api.js";
import { InfoTip } from "../../info.js";
import { can } from "../../session.js";
import { relative, timeTitle } from "../../format.js";
import { CapGrid, sameSet, isFixed } from "./capgrid.js";
import { ROLES, FixedNote } from "./people.js";

export class RolesPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { role: "editor", caps: null, busy: false, error: null, history: false };
  }

  current(role) {
    return new Set(this.props.rbac.resolved[role] || []);
  }

  pick(role) { this.setState({ role, caps: null, error: null }); }

  async save(capabilities) {
    const { role } = this.state;
    this.setState({ busy: true, error: null });
    try {
      await post("/api/rbac/subject", { subject: `role:${role}`, capabilities });
      this.setState({ busy: false, caps: null });
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  async restore(policyId) {
    await post("/api/rbac", { policy_id: policyId });
    this.props.onChanged();
  }

  render({ rbac }, { role, caps, busy, error, history }) {
    const fixed = isFixed(rbac, role);
    const canWrite = can("rbac.write");
    const writable = canWrite && !fixed;
    const ceiling = new Set(rbac.ceiling[role] || []);
    const saved = this.current(role);
    const value = caps || saved;
    const dirty = caps && !sameSet(caps, saved);
    const narrowed = !!(rbac.subjects.roles || {})[role];
    return html`<div class="roles">
      <div class="roles-cards" role="tablist" aria-label="Roles">
        ${ROLES.map(([key, word]) => {
          const held = (rbac.resolved[key] || []).length;
          const top = (rbac.ceiling[key] || []).length;
          return html`<button type="button" key=${key} role="tab" aria-selected=${role === key}
            class=${cx("role-card", role === key && "on")} onClick=${() => this.pick(key)}>
            <b>${word}</b><span>${held} of ${top}</span>
            ${(rbac.subjects.roles || {})[key] && !isFixed(rbac, key)
              ? html`<span class="badge badge-warn">narrowed</span>` : null}
          </button>`;
        })}
        <${InfoTip} label="About roles">Each role holds at most its ceiling; here you can give every
          person of a role less. A person's own access (Users tab) can narrow further, never widen.<//>
      </div>
      ${rbac.malformed ? html`<p class="err" role="alert">The stored policy could not be read
        (${rbac.malformed_reason}); everyone holds their role's full ceiling until it is fixed.</p>` : null}
      ${fixed ? html`<${FixedNote} role=${role} />` : null}
      <${CapGrid} all=${rbac.all_capabilities} ceiling=${ceiling} baseline=${null}
        minimum=${rbac.minimum_role} value=${value} roleName=${role} readOnly=${!writable}
        onChange=${(next) => this.setState({ caps: next })} />
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      ${canWrite ? html`<div class="roles-foot">
        ${writable ? html`<button type="button" class="primary" disabled=${busy || !dirty}
          onClick=${() => this.save(sameSet(value, ceiling) ? null : [...value].sort())}>Save ${role}</button>` : null}
        ${dirty ? html`<button type="button" onClick=${() => this.setState({ caps: null })}>Discard</button>` : null}
        ${narrowed ? html`<button type="button" class="linkish" disabled=${busy}
          onClick=${() => this.save(null)}>Shipped default</button>` : null}
        <button type="button" class="linkish roles-history-toggle"
          onClick=${() => this.setState({ history: !history })}>${history ? "Hide history" : "History"}</button>
      </div>` : null}
      ${history ? html`<ol class="roles-history">
        ${(rbac.history || []).map((h) => html`<li key=${h.id}>
          <span class="mono">v${h.id}</span>
          <span>${h.note || "policy"}</span>
          <span class="muted" title=${timeTitle(h.created_at)}>${h.created_by} · ${relative(h.created_at)}</span>
          ${h.active ? html`<span class="badge">active</span>` : canWrite ? html`<button type="button"
            class="linkish" onClick=${() => this.restore(h.id)}>Restore</button>` : null}
        </li>`)}
        ${(rbac.history || []).length ? null : html`<li class="muted">No changes yet: every role holds its ceiling.</li>`}
      </ol>` : null}
    </div>`;
  }
}
