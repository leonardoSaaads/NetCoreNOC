/* Resetting someone's password or authenticator — People & access, a person's Security tab.
 *
 * Split out of `views/parts/people.js` (ADR #490) when the person editor took Your account's shape
 * and passed the module ceiling. **A password is never shown, only replaced** (ADR #453): "Reset
 * password…" asks the server for a random one-time password, displayed once, which the person must
 * change at their next sign-in. Forgetting an authenticator (ADR #482) makes their next sign-in set
 * up a new one.
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";

/** An administrator's one power over someone's password: replace it, and hand over the new one. */
export class PasswordReset extends Component {
  constructor(props) {
    super(props);
    this.state = { open: false, busy: false, issued: null, copied: false, error: null };
  }

  async reset() {
    this.setState({ busy: true, error: null });
    try {
      const out = await post(`/api/users/${this.props.user.id}/password-reset`, {});
      this.setState({ busy: false, issued: out.temporary_password });
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  async copy() {
    try {
      await globalThis.navigator.clipboard.writeText(this.state.issued);
      this.setState({ copied: true });
    } catch { /* the value is selectable in the field */ }
  }

  render({ user, onDone }, { open, busy, issued, copied, error }) {
    if (issued) {
      return html`<div class="token-issued pw-reset" role="status">
        <p><b>Give ${user.username} this one-time password now.</b> It will not be shown again;
          they must choose their own when they sign in.</p>
        <div class="token-value">
          <input readonly value=${issued} aria-label="One-time password" autocomplete="off"
            onFocus=${(e) => e.currentTarget.select()} />
          <button type="button" class="primary" onClick=${() => this.copy()}>${copied ? "Copied" : "Copy"}</button>
        </div>
        <button type="button" onClick=${onDone}>Done</button>
      </div>`;
    }
    if (!open) {
      return html`<button type="button" class="linkish" onClick=${() => this.setState({ open: true })}>
        Reset password…</button>`;
    }
    return html`<div class="pw-reset panel-block" role="group" aria-label="Reset password">
      <p>A random one-time password replaces ${user.username}'s. Their sessions end now, any reset
        link already emailed stops working, and they choose their own password at the next sign-in.
        No one — an admin included — can see the current password.</p>
      <div class="fb">
        <button type="button" class="warn" disabled=${busy} onClick=${() => this.reset()}>
          ${busy ? "Resetting…" : `Reset ${user.username}'s password`}</button>
        <button type="button" disabled=${busy} onClick=${() => this.setState({ open: false })}>Cancel</button>
      </div>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
    </div>`;
  }
}

/** An administrator forgets someone's authenticator (ADR #482): they lost the phone and the codes. */
export class TwoFactorReset extends Component {
  constructor(props) {
    super(props);
    this.state = { open: false, busy: false, done: false, error: null };
  }

  async reset() {
    this.setState({ busy: true, error: null });
    try {
      await post(`/api/users/${this.props.user.id}/two-factor/reset`, {});
      this.setState({ busy: false, done: true, open: false });
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  render({ user }, { open, busy, done, error }) {
    if (done) {
      return html`<p class="ok-note" role="status">${user.username}'s authenticator is forgotten. Their
        sessions ended; they set up a new one at their next sign-in.</p>`;
    }
    if (!open) {
      return html`<button type="button" class="linkish" disabled=${!user.two_factor}
        title=${user.two_factor ? "" : "No authenticator set up yet"}
        onClick=${() => this.setState({ open: true })}>Reset two-factor…</button>`;
    }
    return html`<div class="pw-reset panel-block" role="group" aria-label="Reset two-factor">
      <p>For someone who lost both the phone and the recovery codes. Their sessions end now and the
        next sign-in asks them to scan a new QR code.</p>
      <div class="fb">
        <button type="button" class="warn" disabled=${busy} onClick=${() => this.reset()}>
          ${busy ? "Resetting…" : `Reset ${user.username}'s two-factor`}</button>
        <button type="button" disabled=${busy} onClick=${() => this.setState({ open: false })}>Cancel</button>
      </div>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
    </div>`;
  }
}
