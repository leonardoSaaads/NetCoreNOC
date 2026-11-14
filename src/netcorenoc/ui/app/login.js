/* Sign-in, the forced first password change, two-factor (ADR #482), and recovery by email.
 *
 * ## Two steps (ADR #482)
 *
 * A right password opens no session: `POST /api/login` answers with a challenge, the card stays
 * where it is with its fields locked, and the second step slides in beside it (`twofactor.js`) —
 * the six-digit code for an enrolled person, or, the first time, the QR code, the recovery codes
 * and the first code. Only `POST /api/login/code` opens the session. "Back" returns to the card.
 *
 * Unchanged in contract: a cookie session, no token box, and a `must_change_password` principal
 * who cannot reach anything but the change itself — the server's bootstrap gate
 * (`api/perimeter.py`, `BOOTSTRAP_ALLOWED`) enforces that, and this screen agrees with it.
 *
 * The failure message stays deliberately uninformative — *"check your credentials"*, never
 * *"no such user"* — because distinguishing the two is a username oracle. The recovery request
 * answers in the server's one sentence for the same reason.
 *
 * ## The layout (v0.30.0, ADR #446)
 *
 * Two columns over the network artwork (`/login-bg.svg`, self-hosted, so `img-src 'self'` holds):
 * what the product is on the left, the card on the right. Below 900 px the left column goes and the
 * card is the page. **The card holds only what signing in needs.** The two declarations it used to
 * carry — two-factor and "cannot get in?" — made the forced-change card taller than a laptop screen;
 * the first moved to Your account → Password, the second became one line, or a "Forgot your
 * password?" link once an admin has configured email.
 *
 * The forced change keeps its three affordances from `password.js` (V.2): a confirmation, the
 * server's length rule shown as it is typed, and reveal — a typo committed there is unrecoverable.
 */

import { html, Component, cx } from "./dom.js";
import { post, get, ApiError } from "./api.js";
import { setPasswordPolicy } from "./session.js";
import { PasswordInput, PasswordMeter, pairProblem } from "./password.js";
import { CardHead, ForgotForm, ResetForm, resetToken } from "./recover.js";
import { Icon } from "./icons.js";
import { LogoMark } from "./logo.js";
import { SecondStep } from "./twofactor.js";

export class Login extends Component {
  constructor(props) {
    super(props);
    const token = props.mustChange ? null : resetToken();
    this.state = {
      username: "", password: "", newPassword: "", confirmPassword: "",
      mustChange: !!props.mustChange, error: "", notice: "", busy: false,
      mode: token ? "reset" : "signin", token, recovery: false, second: null,
    };
    this.submit = this.submit.bind(this);
  }

  async componentDidMount() {
    // A boolean and nothing about any account; a failure simply leaves the link off.
    try { this.setState({ recovery: !!(await get("/api/login/options")).recovery }); }
    catch { /* recovery stays unoffered */ }
  }

  async submit(event) {
    event.preventDefault();
    if (this.state.busy) return;
    // The mismatch is caught HERE, before anything is sent: the server never sees the second entry.
    if (this.state.mustChange) {
      const problem = pairProblem(this.state.newPassword, this.state.confirmPassword);
      if (problem) { this.setState({ error: problem }); return; }
    }
    this.setState({ busy: true, error: "", notice: "" });
    const body = { username: this.state.username, password: this.state.password };
    if (this.state.mustChange) body.new_password = this.state.newPassword;
    try {
      const out = await post("/api/login", body);
      if (out.must_change_password) {
        // The policy travels with the demand: there is no session yet to ask /api/me for.
        setPasswordPolicy(out.password_policy);
        this.setState({ mustChange: true, busy: false, error: "", notice: "Choose your own password to continue." });
        return;
      }
      // ADR #482: a right password opens no session — the second step does. A forced change is
      // already made at this point, so "Back" returns to an ordinary sign-in with the new one.
      this.setState({ busy: false, second: out, mustChange: false, notice: "",
        newPassword: "", confirmPassword: "" });
    } catch (error) {
      this.setState({
        busy: false,
        error: error instanceof ApiError && error.status === 400 && this.state.mustChange
          ? error.detail || "That new password was not accepted."
          : error instanceof ApiError && error.status === 429
            ? "Too many attempts. Wait a moment and try again."
            : "Sign-in failed. Check your credentials.",
      });
    }
  }

  back(notice = "") { this.setState({ mode: "signin", token: null, error: "", notice }); }

  /** From the second step back to the card: a new challenge needs the password again. */
  restart(notice) {
    this.setState({ second: null, password: "", busy: false, error: "", notice });
  }

  signedIn(me) {
    this.setState({ second: null, password: "" });
    this.props.onSignedIn(me);
  }

  card() {
    const { mode, token, mustChange, error, notice, busy, recovery, second } = this.state;
    if (mode === "forgot") return html`<${ForgotForm} onBack=${(n) => this.back(n)} />`;
    if (mode === "reset") return html`<${ResetForm} token=${token} onBack=${(n) => this.back(n)} />`;
    return html`<form class="login-card" onSubmit=${this.submit} autocomplete="off">
      <${CardHead} title=${second ? "Almost there" : mustChange ? "Choose your password" : "Sign in"}
        hint=${second ? "Step 1 of 2 done. Step 2: your authenticator code."
          : mustChange ? "This account must set its own password before anything else."
          : "Use the account an admin gave you."} />
      ${notice ? html`<p class="ok-note" role="status">${notice}</p>` : null}
      <label for="lu">Username</label>
      <input id="lu" name="username" autocomplete="username" autocapitalize="none"
             spellcheck=${false} value=${this.state.username} disabled=${!!second}
             onInput=${(e) => this.setState({ username: e.target.value })} />
      ${second ? html`<p class="ok-note login-passed" role="status"><span class="login-tick"><${Icon} name="check" /></span>
        Password accepted. Finish with your authenticator.</p>` : html`
      <${PasswordInput} id="lp" label=${mustChange ? "Current password" : "Password"}
        autocomplete="current-password" value=${this.state.password}
        onInput=${(v) => this.setState({ password: v })} />`}
      ${mustChange ? html`
        <${PasswordInput} id="lp2" label="New password" autocomplete="new-password"
          describedBy="lp2-meter" value=${this.state.newPassword}
          onInput=${(v) => this.setState({ newPassword: v })} />
        <${PasswordMeter} id="lp2-meter" value=${this.state.newPassword} />
        <${PasswordInput} id="lp3" label="New password again" autocomplete="new-password"
          value=${this.state.confirmPassword}
          onInput=${(v) => this.setState({ confirmPassword: v })} />
      ` : null}
      ${second ? null : html`<button type="submit" id="loginBtn" class="primary" disabled=${busy}>
        ${busy ? "Signing in…" : mustChange ? "Set password and continue" : "Continue"}
      </button>`}
      ${error ? html`<div class="err" id="loginErr" role="alert">${error}</div>` : null}
      ${mustChange || second ? null : html`<p class="login-foot">${recovery
        ? html`<button type="button" class="linkish" id="forgotBtn"
            onClick=${() => this.setState({ mode: "forgot", error: "", notice: "" })}>Forgot your password?</button>`
        : html`<span class="muted">Forgot your password? An admin can reset it —${" "}
            <code class="mono">docs/troubleshoot.md</code>.</span>`}</p>`}
    </form>`;
  }

  render() {
    const second = this.state.second;
    return html`<div id="login" class=${cx("login", second && "has-second")}>
      <section class="login-hero" aria-hidden="true">
        <div class="login-brand"><span class="brand-mark"><${LogoMark} size=${44} /></span><span>Net<b>CoreNOC</b></span></div>
        <p class="login-tagline">SNMP trap correlation for telecom networks.</p>
        <ul class="login-points">
          <li>Point your equipment at it — no MIBs, no inventory, no topology files.</li>
          <li>Related alarms become one situation, with the probable root cause.</li>
          <li>Every grouping shows why, with numbers.</li>
        </ul>
      </section>
      <div class="login-panel">${this.card()}</div>
      ${second ? html`<div class="login-second">
        <${SecondStep} step=${second} onSignedIn=${(me) => this.signedIn(me)}
          onRestart=${(n) => this.restart(n)} /></div>` : null}
    </div>`;
  }
}
