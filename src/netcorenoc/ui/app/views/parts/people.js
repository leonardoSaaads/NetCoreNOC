/* People & access — the Users tab: the list, and one editor for a person (v0.25.0, ADR #403).
 *
 * The editor is the whole person on one form: photo, name, username, password (new accounts), role,
 * and what they may do. Choosing a role checks that role's capabilities; unchecking one makes the
 * person's access *custom*, which is stored as that person's entry in the capability policy — a
 * narrowing, versioned and audited like every policy change, and undone by "Use role default".
 *
 * **The last admin** (F79): the server says which account is the only enabled admin
 * (`sole_admin`), and this editor then offers neither a role change nor deletion for it. That is an
 * affordance; `routes/admin.py` refuses both whatever this renders.
 */

import { html, Component, cx } from "../../dom.js";
import { post, del } from "../../api.js";
import { Avatar, PhotoPicker, uploadPhoto } from "../../avatar.js";
import { InfoTip } from "../../info.js";
import { Destructive } from "../../destructive.js";
import { session, can, passwordPolicy } from "../../session.js";
import { CapGrid, sameSet, isFixed } from "./capgrid.js";
import { relative, timeTitle } from "../../format.js";

export const ROLES = [["viewer", "Viewer"], ["editor", "Editor"], ["admin", "Admin"]];

/**
 * A person's or token's effective access, from the policy the server reported. `fixed` is true for a
 * person whose role no policy narrows (#443) — a token of that role may still be narrowed.
 */
export function accessOf(rbac, role, ref) {
  if (!rbac) return null;
  const ceiling = new Set(rbac.ceiling[role] || []);
  const baseline = new Set(rbac.resolved[role] || []);
  const fixed = isFixed(rbac, role) && !String(ref || "").startsWith("token:");
  const own = fixed ? null : (rbac.subjects.principals || {})[ref];
  const value = own ? new Set([...baseline].filter((c) => own.includes(c))) : baseline;
  return { ceiling, baseline, value, custom: !!own, fixed };
}

/** The sentence a fixed role's grid stands under, so it reads as a fact and not a broken control. */
export function FixedNote({ role }) {
  return html`<p class="note-line fixed-note"><span>
    <b>The ${role} role always holds every capability</b>, so this appliance can always be
    administered. To give someone less, give them the Editor role and narrow that.</span></p>`;
}

export function RoleSwitch({ value, onChange, disabled, name }) {
  return html`<div class="seg" role="radiogroup" aria-label=${name || "Role"}>
    ${ROLES.map(([key, word]) => html`<button type="button" key=${key} role="radio"
      aria-checked=${value === key} class=${cx("seg-btn", value === key && "on")}
      disabled=${disabled} onClick=${() => onChange(key)}>${word}</button>`)}
  </div>`;
}

export class PeopleList extends Component {
  constructor(props) {
    super(props);
    this.state = { q: "" };
  }

  render({ users, rbac, onOpen }, { q }) {
    const me = session();
    const needle = q.trim().toLowerCase();
    const shown = users.filter((u) => !needle || [u.username, u.display_name, u.role]
      .some((v) => v && v.toLowerCase().includes(needle)));
    return html`<div class="people">
      <div class="people-bar">
        <input type="search" placeholder="Search people" aria-label="Search people" value=${q}
          onInput=${(e) => this.setState({ q: e.currentTarget.value })} />
        <button type="button" class="primary" onClick=${() => onOpen("new")}>Add user</button>
      </div>
      <ul class="people-list">
        ${shown.map((u) => {
          const access = accessOf(rbac, u.role, `user:${u.id}`);
          return html`<li key=${u.id}>
            <button type="button" class="person-row" onClick=${() => onOpen(u.id)}>
              <${Avatar} id=${u.id} digest=${u.avatar} name=${u.display_name} username=${u.username} size=${40} />
              <span class="person-id">
                <b>${u.display_name || u.username}</b>
                <span class="muted">@${u.username}${u.username === me.user ? " · you" : ""}</span>
              </span>
              <span class=${`role-pill role-${u.role}`}>${u.role}</span>
              <span class="person-access">${access == null ? null : access.custom
                ? html`<span class="badge badge-warn">custom · ${access.value.size}</span>`
                : html`<span class="muted">role default</span>`}</span>
              <span class="person-meta muted" title=${timeTitle(u.created_at)}>
                ${u.must_change_password ? "must set password" : `added ${relative(u.created_at)}`}</span>
            </button>
          </li>`;
        })}
      </ul>
      ${shown.length ? null : html`<p class="muted">No one matches.</p>`}
    </div>`;
  }
}

/** Create or edit one person. `user` is null for a new account. */
export class PersonEditor extends Component {
  constructor(props) {
    super(props);
    const u = props.user;
    const role = u ? u.role : "viewer";
    const access = accessOf(props.rbac, role, u ? `user:${u.id}` : null);
    this.state = {
      name: u ? u.display_name || "" : "", email: u ? u.email || "" : "", username: "", password: "",
      role,
      caps: access ? access.value : null, photo: undefined, busy: false, error: null,
    };
  }

  setRole(role) {
    const access = accessOf(this.props.rbac, role, null);
    this.setState({ role, caps: access ? access.value : null });
  }

  async save(event) {
    event.preventDefault();
    const { user, rbac } = this.props;
    const { name, email, username, password, role, caps, photo } = this.state;
    this.setState({ busy: true, error: null });
    try {
      let id = user && user.id;
      if (!user) {
        id = (await post("/api/users", { username: username.trim(), password, role,
          display_name: name.trim() || null })).id;
      } else if (role !== user.role) {
        await post(`/api/users/${id}/role`, { role });
      }
      // A new account's name went with its creation; its address, and any edit, go here.
      const changed = user
        ? name.trim() !== (user.display_name || "") || email.trim() !== (user.email || "")
        : !!email.trim();
      if (changed) {
        await post(`/api/users/${id}/profile`, { display_name: name.trim() || null,
          email: email.trim() || null });
      }
      if (photo) await uploadPhoto(`/api/users/${id}/avatar`, photo);
      else if (photo === null && user && user.avatar) await del(`/api/users/${id}/avatar`);
      const base = accessOf(rbac, role, null);
      if (rbac && caps && can("rbac.write") && !base.fixed) {
        const custom = !sameSet(caps, base.value);
        const had = !!(rbac.subjects.principals || {})[`user:${id}`];
        if (custom || had) {
          await post("/api/rbac/subject", { subject: `user:${id}`,
            capabilities: custom ? [...caps].sort() : null });
        }
      }
      this.props.onSaved();
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  render({ user, rbac, onClose, onSaved }, { name, email, username, password, role, caps, busy, error }) {
    const sole = user && user.sole_admin;
    const base = accessOf(rbac, role, null);
    const policy = passwordPolicy();
    const person = user ? { id: user.id, digest: user.avatar, name: user.display_name,
      username: user.username } : { name, username };
    return html`<form class="person-editor panel-block" onSubmit=${(e) => this.save(e)} autocomplete="off">
      <header class="pe-head">
        <h3>${user ? user.display_name || user.username : "New user"}</h3>
        <button type="button" class="linkish" onClick=${onClose}>Close</button>
      </header>
      <div class="pe-grid">
        <${PhotoPicker} person=${person} size=${88} label="User photo"
          onChange=${(blob) => this.setState({ photo: blob })} />
        <div class="pe-fields">
          <label class="pe-field"><span>Name</span>
            <input value=${name} maxlength="80" placeholder="Full name"
              onInput=${(e) => this.setState({ name: e.currentTarget.value })} /></label>
          <label class="pe-field"><span>Recovery email
            <${InfoTip} label="Recovery email">Where a password-reset link goes. Optional; needs
              Settings → Email.<//></span>
            <input type="email" value=${email} maxlength="254" placeholder="name@example.com"
              onInput=${(e) => this.setState({ email: e.currentTarget.value })} /></label>
          ${user ? html`<p class="pe-user muted">@${user.username}</p>` : html`
            <label class="pe-field"><span>Username</span>
              <input value=${username} required maxlength="64" autocapitalize="off" spellcheck=${false}
                onInput=${(e) => this.setState({ username: e.currentTarget.value })} /></label>
            <label class="pe-field"><span>Password
              ${policy ? html`<${InfoTip} label="Password rule">${policy.min}–${policy.max}
                characters. They set their own at first sign-in.<//>` : null}</span>
              <input type="password" value=${password} required autocomplete="new-password"
                onInput=${(e) => this.setState({ password: e.currentTarget.value })} /></label>`}
          <div class="pe-field"><span>Role
            <${InfoTip} label="Roles">A role is the most a person can ever hold. Uncheck
              capabilities below to give this person less.${user ? " Changing it signs them out." : ""}<//></span>
            <${RoleSwitch} value=${role} disabled=${sole} onChange=${(r) => this.setRole(r)} />
            ${sole ? html`<span class="hint">The only admin: add another admin first.</span>` : null}
          </div>
        </div>
      </div>
      ${rbac && caps ? html`<div class="pe-access">
        <div class="pe-access-head">
          <h4>Access</h4>
          ${base.fixed || sameSet(caps, base.value) ? html`<span class="muted">${role} default</span>` : html`
            <span class="badge badge-warn">custom</span>
            <button type="button" class="linkish" onClick=${() => this.setState({ caps: base.value })}>
              Use role default</button>`}
        </div>
        ${base.fixed ? html`<${FixedNote} role=${role} />` : null}
        <${CapGrid} all=${rbac.all_capabilities} ceiling=${base.ceiling} baseline=${base.baseline}
          minimum=${rbac.minimum_role} value=${base.fixed ? base.value : caps} roleName=${role}
          readOnly=${base.fixed || !can("rbac.write")} onChange=${(next) => this.setState({ caps: next })} />
      </div>` : null}
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <footer class="pe-foot">
        <button type="submit" class="primary" disabled=${busy}>${busy ? "Saving…" : user ? "Save" : "Create user"}</button>
        <button type="button" onClick=${onClose}>Cancel</button>
        ${user && !sole && user.username !== session().user ? html`<${Destructive} compact
          title=${`Delete ${user.username}`} previewLabel="Delete…"
          confirmLabel=${`Delete ${user.username}`}
          consequence=${"The account and its sessions are removed now. Its audit history is kept."}
          preview=${async () => ({ measured: false, message: "Exactly this account." })}
          apply=${async () => del(`/api/users/${user.id}`)} onDone=${onSaved} />` : null}
      </footer>
    </form>`;
  }
}
