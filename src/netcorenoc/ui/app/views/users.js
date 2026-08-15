/* User accounts, their roles, and the role change that had no UI (draft §4).
 *
 * `POST /api/users/{uid}/role` existed and was unreachable, so changing someone's role meant
 * deleting the account and recreating it — which loses the account's identity in the audit log
 * and forces a password reset for a change that should be one click. It also **revokes that
 * user's sessions** server-side, which the screen says before the click rather than after.
 */

import { html, Component } from "../dom.js";
import { get, post, del } from "../api.js";
import { Loader, Empty, DataTable, SectionHeading, cell } from "../widgets.js";
import { plural } from "../format.js";
import { session } from "../session.js";
import { Destructive } from "../destructive.js";

const ROLES = ["viewer", "editor", "admin"];

export class Users extends Loader {
  constructor(props) {
    super(props);
    this.what = "the user list";
    this.loadingLabel = "Reading accounts";
  }

  async load() { return get("/api/users"); }

  view(users) {
    const me = session();
    const columns = [
      { key: "username", label: "user" },
      { key: "role", label: "role" },
      { key: "state", label: "state" },
      { key: "actions", label: "" },
    ];
    const rows = users.map((user) => ({
      key: user.id,
      tone: user.disabled ? "quiet" : null,
      cells: {
        username: user.username === me.user
          ? html`<span>${user.username} <span class="muted">(you)</span></span>`
          : user.username,
        role: cell(html`<td><${RoleControl} user=${user} onChanged=${this.reload} /></td>`),
        state: user.disabled ? "disabled" : "active",
        actions: cell(html`<td>${user.username === me.user
          ? html`<span class="muted" title="The appliance refuses this too, not only the console.">
              you cannot delete your own account</span>`
          : html`<${DeleteUser} user=${user} onDone=${this.reload} />`}</td>`),
      },
    }));

    return html`<div class="usersview">
      <p class="hint">${plural(users.length, "account")}. A role is a ceiling, not a grant:
        Governance can take capabilities away from a role but can never add one it does not
        already hold.</p>
      ${users.length ? html`<${DataTable} columns=${columns} rows=${rows} />`
        : html`<${Empty} title="No accounts." will="Accounts you create appear here."
                 meanwhile="The bootstrap admin is created on first start and must change its password." />`}
      <${NewUser} onCreated=${this.reload} />
    </div>`;
  }
}

class RoleControl extends Component {
  constructor(props) {
    super(props);
    this.state = { pending: null, busy: false, error: null };
  }

  async apply(role) {
    this.setState({ busy: true, error: null });
    try {
      await post(`/api/users/${this.props.user.id}/role`, { role });
      this.setState({ busy: false, pending: null });
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render({ user }, { pending, busy, error }) {
    return html`<div class="role-control">
      <label class="visually-hidden" for=${`role-${user.id}`}>Role for ${user.username}</label>
      <select id=${`role-${user.id}`} value=${pending ?? user.role} disabled=${busy}
              onChange=${(e) => this.setState({ pending: e.target.value })}>
        ${ROLES.map((role) => html`<option key=${role} value=${role}>${role}</option>`)}
      </select>
      ${pending && pending !== user.role ? html`<span class="inline-confirm">
        <span class="hint">Changing this signs ${user.username} out of every session.</span>
        <button type="button" disabled=${busy} onClick=${() => this.apply(pending)}>
          ${busy ? "Changing…" : `Change to ${pending}`}
        </button>
        <button type="button" onClick=${() => this.setState({ pending: null })}>Cancel</button>
      </span>` : null}
      ${error ? html`<span class="err">${error.detail || error.message}</span>` : null}
    </div>`;
  }
}

function DeleteUser({ user, onDone }) {
  return html`<${Destructive}
    title=${`Delete ${user.username}`}
    hint="Removes the account and revokes its sessions."
    previewLabel="Delete…"
    confirmLabel=${`Delete ${user.username}`}
    consequence=${`The account "${user.username}" is removed and every session it holds is ` +
                  `revoked immediately. Audit rows already attributed to it are kept — the log ` +
                  `is append-only and deleting a user does not rewrite history.`}
    preview=${async () => ({
      measured: false,
      message: "Deleting an account has no count to preview: it removes exactly this one account " +
               "and its sessions. Its audit history is not touched.",
    })}
    apply=${async () => del(`/api/users/${user.id}`)}
    onDone=${onDone} />`;
}

class NewUser extends Component {
  constructor(props) {
    super(props);
    this.state = { username: "", password: "", role: "viewer", busy: false, error: null };
    this.submit = this.submit.bind(this);
  }

  async submit(event) {
    event.preventDefault();
    this.setState({ busy: true, error: null });
    try {
      await post("/api/users", {
        username: this.state.username, password: this.state.password, role: this.state.role,
      });
      this.setState({ username: "", password: "", busy: false });
      this.props.onCreated();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render(_props, { username, password, role, busy, error }) {
    return html`<section class="panel-block">
      <${SectionHeading} title="Add an account"
        hint="Passwords are 12–128 characters. The appliance stores a hash and never the password." />
      <form class="row-form" onSubmit=${this.submit} autocomplete="off">
        <label class="visually-hidden" for="nu">Username</label>
        <input id="nu" placeholder="username" value=${username}
               onInput=${(e) => this.setState({ username: e.target.value })} />
        <label class="visually-hidden" for="np">Password</label>
        <input id="np" type="password" placeholder="password (12+)" value=${password}
               onInput=${(e) => this.setState({ password: e.target.value })} />
        <label class="visually-hidden" for="nr">Role</label>
        <select id="nr" value=${role} onChange=${(e) => this.setState({ role: e.target.value })}>
          ${ROLES.map((r) => html`<option key=${r} value=${r}>${r}</option>`)}
        </select>
        <button type="submit" disabled=${busy}>${busy ? "Adding…" : "Add user"}</button>
      </form>
      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </section>`;
  }
}
