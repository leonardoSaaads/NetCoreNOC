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

export class Account extends Component {
  constructor(props) {
    super(props);
    this.state = { current: "", next: "", confirm: "", busy: false, outcome: null };
    this.submit = this.submit.bind(this);
  }

  async submit(event) {
    event.preventDefault();
    if (this.state.next !== this.state.confirm) {
      this.setState({ outcome: { ok: false, message: "The two new passwords do not match." } });
      return;
    }
    this.setState({ busy: true, outcome: null });
    try {
      await post("/api/password", {
        old_password: this.state.current,
        new_password: this.state.next,
      });
      this.setState({
        busy: false, current: "", next: "", confirm: "",
        outcome: { ok: true, message: "Password changed. Other sessions are unaffected." },
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
        hint="The appliance requires 12–128 characters. It stores a hash and never the password." />
      <form class="stack" onSubmit=${this.submit} autocomplete="off">
        <label for="pwCurrent">Current password</label>
        <input id="pwCurrent" type="password" autocomplete="current-password"
               value=${this.state.current}
               onInput=${(e) => this.setState({ current: e.target.value })} />
        <label for="pwNext">New password</label>
        <input id="pwNext" type="password" autocomplete="new-password" value=${this.state.next}
               onInput=${(e) => this.setState({ next: e.target.value })} />
        <label for="pwConfirm">New password again</label>
        <input id="pwConfirm" type="password" autocomplete="new-password" value=${this.state.confirm}
               onInput=${(e) => this.setState({ confirm: e.target.value })} />
        <button type="submit" disabled=${busy}>${busy ? "Changing…" : "Change password"}</button>
      </form>
      ${outcome ? html`<p class=${outcome.ok ? "ok-note" : "err"} role="alert">${outcome.message}</p>` : null}
    </div>`;
  }
}
