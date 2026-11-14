/* Your account: who you are here, how you sign in, and what you can do (ADRs #401, #482).
 *
 * A header with the photo, the name and the role, then three tabs (`?tab=`, so a link can open one):
 *
 *   * **Profile** — the name the console shows, and the address a password-reset link goes to (a
 *     new address takes the current password: a session alone must not redirect the next reset).
 *   * **Security** — the password (changing it signs out every session, this one included) and
 *     two-factor sign-in (ADR #482): when it was set up and last used, how many recovery codes are
 *     left, new codes, and moving it to a new phone. Both of the last two ask for the password and
 *     a current code; a session alone is not enough.
 *   * **Permissions** — the role and what it lets you do, by category, as counts; the full list is
 *     one click away. It is the set the SERVER resolved for this session (`/api/me`).
 */

import { html, Component, cx } from "../dom.js";
import { get, post, del } from "../api.js";
import { session, setSession, scopeSummary } from "../session.js";
import { PasswordInput, PasswordMeter, pairProblem } from "../password.js";
import { PhotoPicker, uploadPhoto } from "../avatar.js";
import { relative, timeTitle } from "../format.js";
import { Icon } from "../icons.js";
import { CodeInput, RecoveryCodes } from "../twofactor.js";
import { CATEGORIES } from "./parts/capgrid.js";

async function refreshSession() {
  setSession(await get("/api/me"));
}

const TABS = [["profile", "Profile", "account"], ["security", "Security", "shield"],
  ["permissions", "Permissions", "key"]];

class Header extends Component {
  constructor(props) {
    super(props);
    this.state = { status: null };
  }

  async photo(blob) {
    this.setState({ status: null });
    try {
      if (blob) await uploadPhoto("/api/me/avatar", blob);
      else await del("/api/me/avatar");
      await refreshSession();
      this.setState({ status: { ok: true, text: blob ? "Photo saved." : "Photo removed." } });
    } catch (error) {
      this.setState({ status: { ok: false, text: error.detail || error.message } });
    }
  }

  render(_props, { status }) {
    const me = session();
    const tf = me.twoFactor;
    return html`<section class="acct-header">
      <${PhotoPicker} person=${{ id: me.userId, digest: me.avatar, name: me.displayName, username: me.user }}
        size=${88} label="Your photo" onChange=${(blob) => this.photo(blob)} />
      <div class="acct-who">
        <h2>${me.displayName || me.user}</h2>
        <p class="acct-meta"><span class="muted">@${me.user}</span>
          <span class=${`role-pill role-${me.role}`}>${me.role}</span>
          ${tf && tf.enrolled ? html`<span class="acct-2fa-on"><${Icon} name="shield" /> Two-factor on</span>` : null}
          ${me.email ? html`<span class="muted"><${Icon} name="mail" /> ${me.email}</span>` : null}</p>
        ${status ? html`<p class=${status.ok ? "ok-note" : "err"} role="status">${status.text}</p>` : null}
      </div>
    </section>`;
  }
}

class Profile extends Component {
  constructor(props) {
    super(props);
    this.state = { name: session().displayName || "", email: session().email || "", password: "",
      status: null, busy: false };
  }

  async save(event) {
    event.preventDefault();
    this.setState({ busy: true, status: null });
    try {
      await post("/api/me/profile", { display_name: this.state.name.trim() || null,
        email: this.state.email.trim() || null, current_password: this.state.password || null });
      await refreshSession();
      this.setState({ busy: false, password: "", status: { ok: true, text: "Saved." } });
    } catch (error) {
      this.setState({ busy: false, status: { ok: false, text: error.detail || error.message } });
    }
  }

  render(_props, { name, email, password, status, busy }) {
    const me = session();
    const emailChanged = email.trim() !== (me.email || "");
    const dirty = name.trim() !== (me.displayName || "") || emailChanged;
    return html`<form class="panel-block acct-card" onSubmit=${(e) => this.save(e)}>
      <h3><${Icon} name="account" /> Profile</h3>
      <div class="acct-fields">
        <label class="pe-field"><span>Display name</span>
          <input value=${name} maxlength="80" placeholder=${me.user}
            onInput=${(e) => this.setState({ name: e.currentTarget.value })} />
          <small class="muted">Shown beside your photo. Your username stays <b>${me.user}</b>.</small></label>
        <label class="pe-field"><span>Recovery email</span>
          <input type="email" value=${email} maxlength="254" placeholder="you@example.com"
            autocomplete="email" onInput=${(e) => this.setState({ email: e.currentTarget.value })} />
          <small class="muted">Where “Forgot your password?” sends a reset link, once an admin set up email.</small></label>
        ${emailChanged ? html`<label class="pe-field"><span>Current password</span>
          <input type="password" value=${password} required autocomplete="current-password"
            onInput=${(e) => this.setState({ password: e.currentTarget.value })} />
          <small class="muted">Needed to change where reset links go.</small></label>` : null}
      </div>
      <div class="acct-actions">
        ${status ? html`<span class=${status.ok ? "ok-note" : "err"} role="status">${status.text}</span>` : null}
        <button type="submit" class="primary" disabled=${busy || !dirty}>${busy ? "Saving…" : "Save changes"}</button>
      </div>
    </form>`;
  }
}

class Password extends Component {
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
      this.setState({ busy: false, current: "", next: "", confirm: "", outcome: { ok: true,
        message: "Password changed. Every session was signed out — sign in again." } });
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, message: error.detail || error.message } });
    }
  }

  render(_props, { current, next, confirm, busy, outcome }) {
    return html`<section class="panel-block acct-card">
      <h3><${Icon} name="lock" /> Password</h3>
      <p class="muted acct-lead">Changing it signs out every session of this account, this one included.</p>
      <form class="stack" onSubmit=${(e) => this.submit(e)} autocomplete="off">
        <${PasswordInput} id="pwCurrent" label="Current password" autocomplete="current-password"
          value=${current} onInput=${(v) => this.setState({ current: v })} />
        <${PasswordInput} id="pwNext" label="New password" autocomplete="new-password"
          describedBy="pwNext-meter" value=${next} onInput=${(v) => this.setState({ next: v })} />
        <${PasswordMeter} id="pwNext-meter" value=${next} />
        <${PasswordInput} id="pwConfirm" label="New password again" autocomplete="new-password"
          value=${confirm} onInput=${(v) => this.setState({ confirm: v })} />
        <div class="acct-actions"><button type="submit" disabled=${busy}>${busy ? "Changing…" : "Change password"}</button></div>
      </form>
      ${outcome ? html`<p class=${outcome.ok ? "ok-note" : "err"} role="alert">${outcome.message}</p>` : null}
    </section>`;
  }
}

/** The password and a current code, for the two acts that change the authenticator. */
class Confirm extends Component {
  constructor(props) {
    super(props);
    this.state = { password: "", code: "", busy: false, error: "" };
  }

  async submit(event) {
    event.preventDefault();
    this.setState({ busy: true, error: "" });
    try {
      await this.props.onConfirm({ password: this.state.password, code: this.state.code });
    } catch (error) {
      this.setState({ busy: false, code: "", error: error.detail || error.message });
    }
  }

  render({ title, action, danger, onCancel }, { password, code, busy, error }) {
    return html`<form class="tf-confirm" onSubmit=${(e) => this.submit(e)} autocomplete="off">
      <p class="tf-confirm-title"><b>${title}</b></p>
      <${PasswordInput} id="tfPw" label="Password" autocomplete="current-password" value=${password}
        onInput=${(v) => this.setState({ password: v })} />
      <label for="tfNow">Code from your authenticator</label>
      <${CodeInput} id="tfNow" value=${code} disabled=${busy} onInput=${(v) => this.setState({ code: v })} />
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <div class="acct-actions">
        <button type="button" onClick=${onCancel}>Cancel</button>
        <button type="submit" class=${danger ? "danger" : "primary"} disabled=${busy || !password || code.length < 6}>
          ${busy ? "Checking…" : action}</button>
      </div>
    </form>`;
  }
}

class TwoFactor extends Component {
  constructor(props) {
    super(props);
    this.state = { open: null, codes: null, gone: false };
  }

  async newCodes(body) {
    const out = await post("/api/me/recovery-codes", body);
    await refreshSession();
    this.setState({ open: null, codes: out.recovery_codes });
  }

  async move(body) {
    await post("/api/me/two-factor/reset", body);
    this.setState({ open: null, gone: true });
    globalThis.setTimeout(() => setSession(null), 2500);
  }

  render(_props, { open, codes, gone }) {
    const me = session();
    const tf = me.twoFactor || { enrolled: false, recovery_left: 0 };
    const left = tf.recovery_left || 0;
    return html`<section class="panel-block acct-card">
      <h3><${Icon} name="smartphone" /> Two-factor sign-in${" "}
        <span class=${cx("badge", tf.enrolled ? "badge-ok" : "badge-warn")}>${tf.enrolled ? "On" : "Off"}</span></h3>
      <p class="muted acct-lead">Every sign-in asks for a 6-digit code from your authenticator app after the password. Required for every account.</p>
      <dl class="acct-facts">
        <div><dt>Set up</dt><dd>${tf.enrolled_at ? html`<span title=${timeTitle(tf.enrolled_at)}>${relative(tf.enrolled_at)}</span>` : "—"}</dd></div>
        <div><dt>Last code</dt><dd>${tf.last_used_at ? html`<span title=${timeTitle(tf.last_used_at)}>${relative(tf.last_used_at)}</span>` : "—"}</dd></div>
        <div><dt>Recovery codes</dt><dd><span class=${cx("acct-left", left <= 2 && "is-low")}>${left} of 10 left</span>
          <span class="acct-bar" aria-hidden="true"><span style=${`width:${left * 10}%`}></span></span></dd></div>
      </dl>
      ${gone ? html`<p class="ok-note" role="status">Done. Every session was signed out; the next sign-in sets up your new phone.</p>` : null}
      ${codes ? html`<div class="tf-new-codes">
        <p class="ok-note" role="status">New recovery codes. The old ones no longer work — keep these somewhere safe.</p>
        <${RecoveryCodes} codes=${codes} account=${me.user} />
        <div class="acct-actions"><button type="button" onClick=${() => this.setState({ codes: null })}>I saved them</button></div>
      </div>` : null}
      ${open === "codes" ? html`<${Confirm} title="New recovery codes" action="Make new codes"
        onCancel=${() => this.setState({ open: null })} onConfirm=${(b) => this.newCodes(b)} />` : null}
      ${open === "move" ? html`<${Confirm} title="Move to a new phone" action="Forget this authenticator" danger=${true}
        onCancel=${() => this.setState({ open: null })} onConfirm=${(b) => this.move(b)} />` : null}
      ${open || codes || gone ? null : html`<div class="acct-actions">
        <button type="button" onClick=${() => this.setState({ open: "move" })}><${Icon} name="smartphone" /> Move to a new phone</button>
        <button type="button" class="primary" onClick=${() => this.setState({ open: "codes" })}><${Icon} name="key" /> New recovery codes</button>
      </div>`}
    </section>`;
  }
}

function Permissions() {
  const me = session();
  const held = me.capabilities;
  const scope = scopeSummary();
  return html`<section class="panel-block acct-card acct-access">
    <h3><${Icon} name="key" /> What you can do</h3>
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
  tab() {
    const asked = this.props.query && this.props.query.get("tab");
    return TABS.some(([key]) => key === asked) ? asked : "profile";
  }

  go(tab) {
    if (this.props.navigate) this.props.navigate(`#/account?tab=${tab}`);
    else this.forceUpdate();
  }

  render() {
    const tab = this.tab();
    return html`<div class="account-v3">
      <${Header} />
      <div class="access-tabs" role="tablist" aria-label="Your account">
        ${TABS.map(([key, label, icon]) => html`<button type="button" key=${key} role="tab"
          aria-selected=${tab === key} class=${cx("access-tab", tab === key && "on")}
          onClick=${() => this.go(key)}><${Icon} name=${icon} /> ${label}</button>`)}
      </div>
      ${tab === "profile" ? html`<${Profile} />` : null}
      ${tab === "security" ? html`<div class="acct-grid"><${TwoFactor} /><${Password} /></div>` : null}
      ${tab === "permissions" ? html`<${Permissions} />` : null}
    </div>`;
  }
}
