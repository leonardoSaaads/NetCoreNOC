/* Change your own password while signed in.
 *
 * `POST /api/password` is `self.read` — every authenticated principal holds it — and in v0.12.0
 * it had **no UI surface at all** (draft §4). The login overlay handled a *forced* change on
 * first sign-in, so a signed-in operator had no way to change their password. That is a security
 * affordance missing from a product that ships a password policy, and it is the smallest and
 * least arguable of the eight gaps this release closes.
 */

import { html, Component } from "../dom.js";
import { post } from "../api.js";
import { SectionHeading } from "../widgets.js";
import { session } from "../session.js";
import { PasswordInput, PasswordMeter, pairProblem } from "../password.js";
import { Icon } from "../icons.js";

export class Account extends Component {
  constructor(props) {
    super(props);
    this.state = { current: "", next: "", confirm: "", busy: false, outcome: null };
    this.submit = this.submit.bind(this);
  }

  async submit(event) {
    event.preventDefault();
    // One refusal, shared with the sign-in card, so the two screens say the same words about the
    // same policy — and so a change to `MIN_PASSWORD` moves both.
    const problem = pairProblem(this.state.next, this.state.confirm);
    if (problem) { this.setState({ outcome: { ok: false, message: problem } }); return; }
    this.setState({ busy: true, outcome: null });
    try {
      await post("/api/password", {
        old_password: this.state.current,
        new_password: this.state.next,
      });
      this.setState({
        busy: false, current: "", next: "", confirm: "",
        // F82: this said "Other sessions are unaffected." The route calls
        // `revoke_user_sessions(principal.user_id)`, which revokes EVERY session this account
        // holds — the caller's included — and its own return says "sign in again". Driven: two
        // sessions for one account, one changes the password, both go to 401.
        outcome: {
          ok: true,
          message: "Password changed. Every session this account holds was signed out, "
                   + "including this one — sign in again with the new password.",
        },
      });
    } catch (error) {
      this.setState({
        busy: false,
        outcome: { ok: false, message: error.detail || error.message },
      });
    }
  }

  render(_props, { busy, outcome }) {
    const active = session();
    return html`<div class="account">
      <${SectionHeading} title="Signed in as"
        hint="Your role and resolved capabilities come from the server on every request; this is
              a display of them, not a second copy." />
      <dl class="kv">
        <dt>user</dt><dd>${active.user}</dd>
        <dt>role</dt><dd><span class="role-tag">${active.role}</span></dd>
        <dt>capabilities</dt>
        <dd>${[...active.capabilities].sort().map((c) => html`<code class="mono cap" key=${c}>${c}</code>`)}</dd>
      </dl>

      <${SectionHeading} title="Change your password"
        hint="It stores a hash and never the password. Changing it signs out every session this
              account holds, including this one." />
      <form class="stack" onSubmit=${this.submit} autocomplete="off">
        <${PasswordInput} id="pwCurrent" label="Current password" autocomplete="current-password"
          value=${this.state.current} onInput=${(v) => this.setState({ current: v })} />
        <${PasswordInput} id="pwNext" label="New password" autocomplete="new-password"
          describedBy="pwNext-meter" value=${this.state.next}
          onInput=${(v) => this.setState({ next: v })} />
        <${PasswordMeter} id="pwNext-meter" value=${this.state.next} />
        <${PasswordInput} id="pwConfirm" label="New password again" autocomplete="new-password"
          value=${this.state.confirm} onInput=${(v) => this.setState({ confirm: v })} />
        <button type="submit" disabled=${busy}>${busy ? "Changing…" : "Change password"}</button>
      </form>
      ${outcome ? html`<p class=${outcome.ok ? "ok-note" : "err"} role="alert">${outcome.message}</p>` : null}

      <${SectionHeading} title="Second factor" />
      <p class="note-line">
        <${Icon} name="shield" /><span>
          <b>Not available yet.</b>${" "}Two-factor authentication is on the roadmap and will
          be${" "}<b>required for admin accounts</b> when it arrives. There is nothing to enrol
          here today, and this appliance holds no secret, no recovery code and no address for
          you.</span>
      </p>
    </div>`;
  }
}
