/* Sign-in, and the forced first password change.
 *
 * Unchanged in contract from v0.12.0: a cookie session, no token box, and a `must_change_password`
 * principal who cannot reach anything but the change itself — the server's bootstrap gate
 * (`api/perimeter.py`, `BOOTSTRAP_ALLOWED`) enforces that, and this screen simply agrees with it.
 *
 * The failure message stays deliberately uninformative — *"check your credentials"*, never
 * *"no such user"* — because distinguishing the two is a username oracle. That was true in
 * v0.12.0 and is restated here because a rewrite is exactly when a helpful message gets added.
 *
 * ## The forced change had ONE field, and that was the worst defect on this screen (V.2)
 *
 * A bootstrap admin typed its new password once, into `#lp2`, with no confirmation, no indication
 * of what the appliance would accept, and no way to read back what it had typed. The two entries
 * could not disagree because there was only one — so a typo was not caught, it was **committed**,
 * and the bootstrap password had already been shown for the last time. The failure mode is total:
 * the environment is gone. It now has all three affordances, from `password.js`, which is the same
 * module the account screen uses so the two cannot drift.
 *
 * ## Two-factor and recovery (V.3, DECISIONS #238)
 *
 * A sentence that is true is not a placeholder; a greyed-out control promising a mechanism nobody
 * has built is. So this screen **says** that 2FA does not exist yet, that it will be required for
 * admins, and what an operator locked out today actually does. There is no disabled button, no
 * reserved region, and no mechanism behind any of it.
 *
 * It names the ROADMAP rather than a version, and that is #250 amending #238. The first version
 * said "v0.17.0", which the release table called `archetypes` and #249 then renamed
 * `external-cartridge` — so the console would have promised a second factor in a release about
 * ONNX. A version number nothing schedules is the empty placeholder wearing a date.
 */

import { html, Component } from "./dom.js";
import { post, get, ApiError } from "./api.js";
import { setPasswordPolicy } from "./session.js";
import { PasswordInput, PasswordMeter, pairProblem } from "./password.js";
import { Icon } from "./icons.js";

export class Login extends Component {
  constructor(props) {
    super(props);
    this.state = {
      username: "", password: "", newPassword: "", confirmPassword: "",
      mustChange: !!props.mustChange, error: "", busy: false,
    };
    this.submit = this.submit.bind(this);
  }

  async submit(event) {
    event.preventDefault();
    if (this.state.busy) return;
    // The mismatch is caught HERE, before anything is sent. A confirmation checked by the server
    // would not be a confirmation: the server never sees the second entry.
    if (this.state.mustChange) {
      const problem = pairProblem(this.state.newPassword, this.state.confirmPassword);
      if (problem) { this.setState({ error: problem }); return; }
    }
    this.setState({ busy: true, error: "" });
    const body = { username: this.state.username, password: this.state.password };
    if (this.state.mustChange) body.new_password = this.state.newPassword;
    try {
      const out = await post("/api/login", body);
      if (out.must_change_password) {
        // The policy travels with the demand, because there is no session yet to ask /api/me for.
        setPasswordPolicy(out.password_policy);
        this.setState({
          mustChange: true, busy: false,
          error: "Set a new password to continue.",
        });
        return;
      }
      // The login response predates capability resolution, so ask /api/me for the resolved set
      // rather than assuming role rank implies it (F28).
      const me = await get("/api/me");
      this.setState({ busy: false, password: "", newPassword: "", confirmPassword: "" });
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

        <${PasswordInput} id="lp" label="Password" autocomplete="current-password"
          value=${this.state.password}
          onInput=${(v) => this.setState({ password: v })} />

        ${mustChange ? html`
          <${PasswordInput} id="lp2" label="New password" autocomplete="new-password"
            describedBy="lp2-meter" value=${this.state.newPassword}
            onInput=${(v) => this.setState({ newPassword: v })} />
          <${PasswordMeter} id="lp2-meter" value=${this.state.newPassword} />
          <${PasswordInput} id="lp3" label="New password again" autocomplete="new-password"
            value=${this.state.confirmPassword}
            onInput=${(v) => this.setState({ confirmPassword: v })} />
          <p class="hint">This account must set its own password before it can do anything else.
            The appliance refuses every other route until it does.</p>
        ` : null}

        <button type="submit" id="loginBtn" disabled=${busy}>
          ${busy ? "Signing in…" : "Sign in"}
        </button>
        ${error ? html`<div class="err" id="loginErr" role="alert">${error}</div>` : null}
        <${SignInNotes} />
      </form>
    </div>`;
  }
}

/**
 * What this appliance does not have yet, and what to do when you cannot get in.
 *
 * Both are declarations. Neither is a control, and neither reserves space on any other screen —
 * the failure #219 recorded is a placeholder that outlives the feature it was waiting for.
 */
function SignInNotes() {
  return html`<div class="signin-notes">
    <p class="note-line">
      <${Icon} name="shield" /><span>
        <b>Two-factor authentication is not available.</b>${" "}It is on the roadmap and will
        be${" "}<b>required for admin accounts</b> when it arrives. Until then a password is the
        only factor this appliance has.</span>
    </p>
    <p class="note-line">
      <${Icon} name="info" /><span>
        <b>Cannot get in?</b>${" "}There is no email reset — this appliance sends nothing and stores
        no address. Restart it and read the new admin password from its startup log; it prints one
        whenever no enabled admin account remains. See${" "}
        <code class="mono">docs/troubleshoot.md</code>.
      </span>
    </p>
  </div>`;
}
