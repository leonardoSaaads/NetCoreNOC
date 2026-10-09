/* Recovery by email, on the sign-in card (v0.30.0, ADR #445).
 *
 * Two forms. **Forgot** asks for a username or an address and shows the server's answer — one
 * sentence whatever was typed, so the screen is no more an account oracle than the route is.
 * **Reset** is what the emailed link opens: `#/reset/<token>`. The token is read once and taken out
 * of the address bar at once, so it is not left in the history or on a shared screen; the new
 * password gets the same confirmation, meter and reveal as every other password form (`password.js`).
 */

import { html, Component } from "./dom.js";
import { post, ApiError } from "./api.js";
import { PasswordInput, PasswordMeter, pairProblem } from "./password.js";

const RESET = /^#\/reset\/([A-Za-z0-9_-]{20,128})$/;

/** The token in `#/reset/<token>`, removed from the address bar as it is read; null otherwise. */
export function resetToken() {
  const where = globalThis.location;
  const match = where ? RESET.exec(where.hash || "") : null;
  if (!match) return null;
  try { globalThis.history.replaceState(null, "", where.pathname + where.search); }
  catch { /* a harness without history: the token simply stays */ }
  return match[1];
}

/** A sign-in card's heading: the wordmark (the only one on a phone, where the hero is gone). */
export function CardHead({ title, hint }) {
  return html`<header class="login-head">
    <h1>Net<span>CoreNOC</span></h1>
    <h2>${title}</h2>
    ${hint ? html`<p class="hint">${hint}</p>` : null}
  </header>`;
}

export class ForgotForm extends Component {
  constructor(props) {
    super(props);
    this.state = { login: "", busy: false, answer: "", error: "" };
  }

  async submit(event) {
    event.preventDefault();
    if (!this.state.login.trim() || this.state.busy) return;
    this.setState({ busy: true, error: "" });
    try {
      const out = await post("/api/password-reset", { login: this.state.login.trim() });
      this.setState({ busy: false, answer: out.status });
    } catch (error) {
      this.setState({ busy: false, error: error instanceof ApiError && error.status === 429
        ? "Too many requests from here. Try again later." : "The request could not be sent." });
    }
  }

  render({ onBack }, { login, busy, answer, error }) {
    return html`<form class="login-card" onSubmit=${(e) => this.submit(e)} autocomplete="off">
      <${CardHead} title="Reset your password"
        hint="We will email a link to the address on your account." />
      ${answer ? html`<p class="ok-note" role="status" id="forgotAnswer">${answer}</p>` : html`
        <label for="fl">Username or email</label>
        <input id="fl" autocomplete="username" autocapitalize="none" spellcheck=${false}
          value=${login} onInput=${(e) => this.setState({ login: e.target.value })} />
        <button type="submit" class="primary" disabled=${busy || !login.trim()}>
          ${busy ? "Sending…" : "Email me a link"}</button>`}
      ${error ? html`<div class="err" role="alert">${error}</div>` : null}
      <p class="login-foot"><button type="button" class="linkish" onClick=${() => onBack("")}>
        Back to sign in</button></p>
    </form>`;
  }
}

export class ResetForm extends Component {
  constructor(props) {
    super(props);
    this.state = { next: "", confirm: "", busy: false, error: "" };
  }

  async submit(event) {
    event.preventDefault();
    const problem = pairProblem(this.state.next, this.state.confirm);
    if (problem) { this.setState({ error: problem }); return; }
    this.setState({ busy: true, error: "" });
    try {
      const out = await post("/api/password-reset/confirm",
        { token: this.props.token, new_password: this.state.next });
      this.props.onBack(out.status);
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  render({ onBack }, { next, confirm, busy, error }) {
    return html`<form class="login-card" onSubmit=${(e) => this.submit(e)} autocomplete="off">
      <${CardHead} title="Choose a new password"
        hint="Every session of the account is signed out when it changes." />
      <${PasswordInput} id="rp1" label="New password" autocomplete="new-password"
        describedBy="rp1-meter" value=${next} onInput=${(v) => this.setState({ next: v })} />
      <${PasswordMeter} id="rp1-meter" value=${next} />
      <${PasswordInput} id="rp2" label="New password again" autocomplete="new-password"
        value=${confirm} onInput=${(v) => this.setState({ confirm: v })} />
      <button type="submit" class="primary" disabled=${busy}>${busy ? "Saving…" : "Set password"}</button>
      ${error ? html`<div class="err" role="alert">${error}</div>` : null}
      <p class="login-foot"><button type="button" class="linkish" onClick=${() => onBack("")}>
        Back to sign in</button></p>
    </form>`;
  }
}
