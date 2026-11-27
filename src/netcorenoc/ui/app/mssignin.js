/* Sign in with Microsoft, for a mailbox (ADR #510).
 *
 * Outlook.com and Hotmail refuse a password, an app password too: the email wizard's account step
 * (`mailwizard.js`) shows this instead — the application (client) ID from the owner's own Entra
 * registration, with the steps to one, then a code to type at microsoft.com/devicelogin, polled
 * through `/api/mail/oauth` until the owner approves. The device code never reaches the browser;
 * the server holds it and hands back an opaque flow key, which the wizard sends with Save.
 */

import { html, Component } from "./dom.js";
import { post } from "./api.js";
import { Icon } from "./icons.js";

const TENANTS = [["consumers", "Personal account — Outlook.com, Hotmail, Live"],
  ["organizations", "Work or school — Microsoft 365"]];
const REGISTER = [
  "Open entra.microsoft.com → App registrations → New registration (free).",
  "Supported account types: personal Microsoft accounts (and work accounts, for Microsoft 365).",
  "Authentication → Allow public client flows → Yes.",
  "API permissions → Add → APIs my organization uses → Office 365 Exchange Online → Delegated: SMTP.Send and IMAP.AccessAsUser.All.",
  "Copy the Application (client) ID here.",
];
const CLIENT_ID = /^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$/i;

/** Microsoft's sign-in: the code, the link, and the wait until it is approved. */
export class MicrosoftSignIn extends Component {
  constructor(props) {
    super(props);
    this.state = { code: null, busy: false, error: null, account: null };
  }

  componentWillUnmount() { clearTimeout(this.timer); }

  async start() {
    const { d } = this.props;
    this.setState({ busy: true, error: null, account: null });
    try {
      const code = await post("/api/mail/oauth/start", { client_id: d.oauth_client_id.trim(), tenant: d.oauth_tenant });
      this.setState({ code, busy: false });
      this.props.onFlow(null);
      this.wait(code);
    } catch (error) {
      this.setState({ busy: false, error: error.detail || error.message });
    }
  }

  wait(code) {
    this.timer = setTimeout(async () => {
      try {
        const answer = await post("/api/mail/oauth/poll", { flow: code.flow });
        if (answer.status === "signed_in") {
          this.setState({ code: null, account: answer.account || "the mailbox" });
          this.props.onFlow(code.flow);
        } else this.wait(code);
      } catch (error) {
        this.setState({ code: null, error: error.detail || error.message });
      }
    }, Math.max(2, code.interval || 5) * 1000);
  }

  render({ d, signedIn, onChange }, { code, busy, error, account }) {
    const ok = CLIENT_ID.test(d.oauth_client_id.trim());
    return html`<div class="mw-signin">
      <div class="mw-fields">
        <div class="pe-field"><label for="mw-client">Application (client) ID</label>
          <input id="mw-client" class="mono" value=${d.oauth_client_id} placeholder="1a2b3c4d-…"
            onInput=${(e) => onChange({ oauth_client_id: e.currentTarget.value })} /></div>
        <div class="pe-field"><label for="mw-tenant">Account</label>
          <select id="mw-tenant" value=${d.oauth_tenant} onChange=${(e) => onChange({ oauth_tenant: e.currentTarget.value })}>
            ${TENANTS.map(([v, l]) => html`<option key=${v} value=${v}>${l}</option>`)}</select></div>
      </div>
      <details class="mw-how"><summary>How to get an application ID</summary>
        <ol>${REGISTER.map((s) => html`<li key=${s}>${s}</li>`)}</ol></details>
      ${code ? html`<div class="mw-code" role="status">
          <p>Open <a href=${code.verification_uri} target="_blank" rel="noopener noreferrer">${code.verification_uri}</a>,
            type this code and sign in as <b>${d.sender || "the mailbox"}</b>:</p>
          <p class="mw-code-value mono">${code.user_code}</p>
          <p class="muted">Waiting for Microsoft… this page notices by itself.</p></div>`
        : account || signedIn
        ? html`<p class="ok-note" role="status"><${Icon} name="check" /> Signed in with Microsoft${account ? ` as ${account}` : ""}.${" "}
            <button type="button" class="linkish" disabled=${!ok || busy} onClick=${() => this.start()}>Sign in again</button></p>`
        : html`<button type="button" class="primary" disabled=${!ok || busy} onClick=${() => this.start()}>
            ${busy ? "Asking Microsoft…" : "Sign in with Microsoft"}</button>`}
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
    </div>`;
  }
}
