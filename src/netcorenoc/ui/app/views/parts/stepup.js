/* The step-up: your username, your password and — once two-factor sign-in exists — a code from
 * your authenticator app, asked again before the vault does anything that matters (ADR #456).
 *
 * The server decides; this collects. It is shown inline beside the act it authorises (reveal,
 * change, delete, agents on, a custodian named) and its values go out in that act's own request,
 * so nothing outlives the one thing it was typed for. The fields are remounted empty after every
 * attempt, right or wrong.
 *
 * ## The second factor is a placeholder, and looks like one
 *
 * The server says whether an account can use one (`secondFactor`, from the vault's own answer).
 * Until two-factor sign-in ships the code field is drawn **disabled**, with the sentence why —
 * never enabled and quietly ignored, which would teach an operator that six digits were checked.
 */

import { html, Component } from "../../dom.js";
import { PasswordInput } from "../../password.js";
import { session } from "../../session.js";

let fieldSeq = 0;
const EMPTY = { username: "", password: "", otp: "" };

/** The step-up as a request body: the server's field names, an empty code sent as null. */
export function stepupBody(value) {
  return { username: value.username.trim(), password: value.password, otp: value.otp.trim() || null };
}

/** Just the three fields, reporting every change — for a card that has its own buttons. */
export class StepUpFields extends Component {
  constructor(props) {
    super(props);
    fieldSeq += 1;
    this.ids = { user: `su-user-${fieldSeq}`, pw: `su-pw-${fieldSeq}`, otp: `su-otp-${fieldSeq}` };
    this.state = { ...EMPTY };
  }

  update(patch) {
    const next = { ...this.state, ...patch };
    this.setState(patch);
    this.props.onChange(next);
  }

  render({ secondFactor }, { username, password, otp }) {
    const enrolled = secondFactor && secondFactor !== "not-enrolled";
    return html`<div class="stepup-fields">
      <div class="pe-field">
        <label for=${this.ids.user}>Username</label>
        <input id=${this.ids.user} value=${username} required maxlength="64"
          autocomplete="username" autocapitalize="none" spellcheck=${false}
          onInput=${(e) => this.update({ username: e.currentTarget.value })} />
      </div>
      <${PasswordInput} id=${this.ids.pw} label="Password" value=${password}
        autocomplete="current-password" onInput=${(v) => this.update({ password: v })} />
      <div class="pe-field stepup-otp">
        <label for=${this.ids.otp}>Authenticator code</label>
        <input id=${this.ids.otp} value=${otp} inputmode="numeric" maxlength="8"
          autocomplete="one-time-code" disabled=${!enrolled}
          placeholder=${enrolled ? "123456" : "not enabled yet"}
          aria-describedby=${`${this.ids.otp}-note`}
          onInput=${(e) => this.update({ otp: e.currentTarget.value })} />
        <p class="hint stepup-note" id=${`${this.ids.otp}-note`}>${enrolled
          ? "From Google Authenticator, Authy or another authenticator app."
          : "Two-factor sign-in (Google Authenticator, Authy) is not available on this appliance " +
            "yet. When it is, this code will be required here too."}</p>
      </div>
    </div>`;
  }
}

/** The step-up as a form of its own: ``onConfirm(body)`` performs the act, and may throw. */
export class StepUp extends Component {
  constructor(props) {
    super(props);
    this.state = { value: { ...EMPTY }, attempt: 0, busy: false, error: null };
  }

  async submit(event) {
    event.preventDefault();
    this.setState({ busy: true, error: null });
    try {
      await this.props.onConfirm(stepupBody(this.state.value));
      this.setState({ busy: false });
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
    this.setState({ value: { ...EMPTY }, attempt: this.state.attempt + 1 });
  }

  render({ title, action, consequence, secondFactor, onCancel, danger }, state) {
    const { value, attempt, busy, error } = state;
    const me = session();
    return html`<form class="stepup panel-block" onSubmit=${(e) => this.submit(e)} autocomplete="off">
      <h4 class="stepup-title">${title}</h4>
      <p class="muted stepup-why">Confirm it is you${me ? ` (${me.user})` : ""}: your username and
        password again.${consequence ? ` ${consequence}` : ""}</p>
      <${StepUpFields} key=${attempt} secondFactor=${secondFactor}
        onChange=${(next) => this.setState({ value: next })} />
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class=${danger ? "danger" : "primary"}
          disabled=${busy || !value.username || !value.password}>${busy ? "Checking…" : action}</button>
        <button type="button" onClick=${onCancel} disabled=${busy}>Cancel</button>
      </div>
    </form>`;
  }
}
