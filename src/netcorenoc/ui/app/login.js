/* Sign-in, and the forced first password change.
 *
 * Unchanged in contract from v0.12.0: a cookie session, no token box, and a `must_change_password`
 * principal who cannot reach anything but the change itself — the server's bootstrap gate
 * (`api/perimeter.py`, `BOOTSTRAP_ALLOWED`) enforces that, and this screen simply agrees with it.
 *
 * The failure message stays deliberately uninformative — *"check your credentials"*, never
 * *"no such user"* — because distinguishing the two is a username oracle. That was true in
 * v0.12.0 and is restated here because a rewrite is exactly when a helpful message gets added.
 */

import { html, Component } from "./dom.js";
import { post, get, ApiError } from "./api.js";

export class Login extends Component {
  constructor(props) {
    super(props);
    this.state = {
      username: "", password: "", newPassword: "",
      mustChange: !!props.mustChange, error: "", busy: false,
    };
    this.submit = this.submit.bind(this);
  }

  async submit(event) {
    event.preventDefault();
    if (this.state.busy) return;
    this.setState({ busy: true, error: "" });
    const body = { username: this.state.username, password: this.state.password };
    if (this.state.mustChange) body.new_password = this.state.newPassword;
    try {
      const out = await post("/api/login", body);
      if (out.must_change_password) {
        this.setState({
          mustChange: true, busy: false,
          error: "Set a new password to continue.",
        });
        return;
      }
      // The login response predates capability resolution, so ask /api/me for the resolved set
      // rather than assuming role rank implies it (F28).
      const me = await get("/api/me");
      this.setState({ busy: false, password: "", newPassword: "" });
      this.props.onSignedIn({ ...me, user: me.user ?? out.user, role: me.role ?? out.role });
    } catch (error) {
      this.setState({
        busy: false,
        error: error instanceof ApiError && error.status === 400 && this.state.mustChange
          ? error.detail || "That new password was not accepted."
          : "Sign-in failed. Check your credentials.",
      });
    }
  }

  render(_props, { mustChange, error, busy }) {
    return html`<div id="login" class="login">
      <form class="login-card" onSubmit=${this.submit} autocomplete="off">
        <h1>Net<span>CoreNOC</span></h1>
        <p class="hint">Sign in to the correlator.</p>

        <label for="lu">Username</label>
        <input id="lu" name="username" autocomplete="username" autocapitalize="none"
               spellcheck="false" value=${this.state.username}
               onInput=${(e) => this.setState({ username: e.target.value })} />

        <label for="lp">Password</label>
        <input id="lp" name="password" type="password" autocomplete="current-password"
               value=${this.state.password}
               onInput=${(e) => this.setState({ password: e.target.value })} />

        ${mustChange ? html`
          <label for="lp2">New password (12–128 characters)</label>
          <input id="lp2" name="new_password" type="password" autocomplete="new-password"
                 value=${this.state.newPassword}
                 onInput=${(e) => this.setState({ newPassword: e.target.value })} />
          <p class="hint">This account must set its own password before it can do anything else.
            The appliance refuses every other route until it does.</p>
        ` : null}

        <button type="submit" id="loginBtn" disabled=${busy}>
          ${busy ? "Signing in…" : "Sign in"}
        </button>
        ${error ? html`<div class="err" id="loginErr" role="alert">${error}</div>` : null}
      </form>
    </div>`;
  }
}
