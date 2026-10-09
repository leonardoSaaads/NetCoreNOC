/* Your account: who you are here, your password, and what you can do (v0.25.0, ADR #401).
 *
 * Three cards, in the order a person comes here for them:
 *
 *   * **Profile** — your photo, the name the console shows beside it and — v0.30.0 — the address a
 *     password-reset link goes to. Each saves on its own; the username is the sign-in.
 *   * **Security** — change your password (`POST /api/password`, `self.read`). It signs out every
 *     session this account holds, including this one, and the card says so before the click. The
 *     two-factor statement lives here since v0.30.0: it is about this account's sign-in, and on
 *     the sign-in card it made the first-run card taller than a laptop screen (ADR #446).
 *   * **Access** — your role and what it lets you do, by category, as counts; the full list is one
 *     click away instead of a wall of identifiers. It is the set the SERVER resolved for this
 *     session (`/api/me`), a display of it and never a second copy.
 */

import { html, Component } from "../dom.js";
import { get, post, del } from "../api.js";
import { session, setSession, scopeSummary } from "../session.js";
import { PasswordInput, PasswordMeter, pairProblem } from "../password.js";
import { PhotoPicker, uploadPhoto } from "../avatar.js";
import { InfoTip } from "../info.js";
import { Icon } from "../icons.js";
import { CATEGORIES } from "./parts/capgrid.js";

async function refreshSession() {
  setSession(await get("/api/me"));
}

class Profile extends Component {
  constructor(props) {
    super(props);
    this.state = { name: session().displayName || "", email: session().email || "", status: null,
      busy: false };
  }

  async photo(blob) {
    this.setState({ busy: true, status: null });
    try {
      if (blob) await uploadPhoto("/api/me/avatar", blob);
      else await del("/api/me/avatar");
      await refreshSession();
      this.setState({ busy: false, status: { ok: true, text: blob ? "Photo saved." : "Photo removed." } });
    } catch (error) {
      this.setState({ busy: false, status: { ok: false, text: error.detail || error.message } });
    }
  }

  async saveName(event) {
    event.preventDefault();
    this.setState({ busy: true, status: null });
    try {
      await post("/api/me/profile", { display_name: this.state.name.trim() || null,
        email: this.state.email.trim() || null });
      await refreshSession();
      this.setState({ busy: false, status: { ok: true, text: "Saved." } });
    } catch (error) {
      this.setState({ busy: false, status: { ok: false, text: error.detail || error.message } });
    }
  }

  render(_props, { name, email, status, busy }) {
    const me = session();
    const dirty = name.trim() !== (me.displayName || "") || email.trim() !== (me.email || "");
    return html`<section class="panel-block acct-profile">
      <${PhotoPicker} person=${{ id: me.userId, digest: me.avatar, name: me.displayName, username: me.user }}
        size=${104} label="Your photo" onChange=${(blob) => this.photo(blob)} />
      <form class="acct-id" onSubmit=${(e) => this.saveName(e)}>
        <label class="pe-field"><span>Name</span>
          <input value=${name} maxlength="80" placeholder=${me.user}
            onInput=${(e) => this.setState({ name: e.currentTarget.value })} /></label>
        <label class="pe-field"><span>Recovery email
          <${InfoTip} label="About the recovery email">Where a password-reset link is sent when you
            use “Forgot your password?” on the sign-in screen. Only works once an admin has set up
            email (Settings → Email).<//></span>
          <input type="email" value=${email} maxlength="254" placeholder="you@example.com"
            autocomplete="email" onInput=${(e) => this.setState({ email: e.currentTarget.value })} />
        </label>
        <p class="acct-handle"><span class="muted">@${me.user}</span>
          <span class=${`role-pill role-${me.role}`}>${me.role}</span>
          <button type="submit" class="primary" disabled=${busy || !dirty}>Save</button></p>
        ${status ? html`<p class=${status.ok ? "ok-note" : "err"} role="status">${status.text}</p>` : null}
      </form>
    </section>`;
  }
}

class Security extends Component {
  constructor(props) {
    super(props);
    this.state = { current: "", next: "", confirm: "", busy: false, outcome: null };
  }

  async submit(event) {
    event.preventDefault();
    const problem = pairProblem(this.state.next, this.state.confirm);
    if (problem) { this.setState({ outcome: { ok: false, message: problem } }); return; }
    this.setState({ busy: true, outcome: null });
    try {
      await post("/api/password", { old_password: this.state.current, new_password: this.state.next });
      // F82: every session this account holds is revoked, this one included.
      this.setState({ busy: false, current: "", next: "", confirm: "", outcome: { ok: true,
        message: "Password changed. Every session was signed out — sign in again." } });
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, message: error.detail || error.message } });
    }
  }

  render(_props, { current, next, confirm, busy, outcome }) {
    return html`<section class="panel-block acct-security">
      <h3>Password <${InfoTip} label="About passwords">Stored as a hash, never as the password.
        Changing it signs out every session of this account, this one included.<//></h3>
      <p class="note-line"><${Icon} name="shield" /><span><b>Two-factor sign-in is not available
        yet.</b>${" "}It is on the roadmap and will be required for admin accounts when it arrives;
        until then the password is this account's only factor.</span></p>
      <form class="stack" onSubmit=${(e) => this.submit(e)} autocomplete="off">
        <${PasswordInput} id="pwCurrent" label="Current password" autocomplete="current-password"
          value=${current} onInput=${(v) => this.setState({ current: v })} />
        <${PasswordInput} id="pwNext" label="New password" autocomplete="new-password"
          describedBy="pwNext-meter" value=${next} onInput=${(v) => this.setState({ next: v })} />
        <${PasswordMeter} id="pwNext-meter" value=${next} />
        <${PasswordInput} id="pwConfirm" label="New password again" autocomplete="new-password"
          value=${confirm} onInput=${(v) => this.setState({ confirm: v })} />
        <button type="submit" disabled=${busy}>${busy ? "Changing…" : "Change password"}</button>
      </form>
      ${outcome ? html`<p class=${outcome.ok ? "ok-note" : "err"} role="alert">${outcome.message}</p>` : null}
    </section>`;
  }
}

function Access() {
  const me = session();
  const held = me.capabilities;
  const scope = scopeSummary();
  return html`<section class="panel-block acct-access">
    <h3>What you can do <${InfoTip} label="Where this comes from">Your role, narrowed by any access
      an admin set for you. Resolved by the server on every request.<//></h3>
    <p class="acct-summary"><span class=${`role-pill role-${me.role}`}>${me.role}</span>
      <b>${held.size}</b> capabilities${scope ? html` · <span title=${scope.title}>sees
      ${scope.neCount} network elements</span>` : ""}</p>
    <ul class="acct-cats">
      ${CATEGORIES.map(([name, caps]) => {
        const mine = caps.filter(([id]) => held.has(id));
        return html`<li key=${name}>
          <span class="acct-cat">${name}</span>
          <span class="acct-bar" aria-hidden="true"><span style=${`width:${Math.round((mine.length / caps.length) * 100)}%`}></span></span>
          <span class="acct-n">${mine.length}/${caps.length}</span>
        </li>`;
      })}
    </ul>
    <details class="acct-all">
      <summary>Show each capability</summary>
      ${CATEGORIES.map(([name, caps]) => {
        const mine = caps.filter(([id]) => held.has(id));
        return mine.length ? html`<div key=${name} class="acct-chips"><b>${name}</b>
          ${mine.map(([id, label]) => html`<span key=${id} class="chip-static" title=${id}>${label}</span>`)}</div>` : null;
      })}
    </details>
  </section>`;
}

export class Account extends Component {
  render() {
    return html`<div class="account-v2">
      <${Profile} />
      <div class="acct-grid">
        <${Security} />
        <${Access} />
      </div>
    </div>`;
  }
}
