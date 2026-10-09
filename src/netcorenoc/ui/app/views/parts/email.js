/* Settings → Email: the SMTP server recovery links are sent through (v0.30.0, ADR #445).
 *
 * Built for someone who has never configured SMTP: pick the provider, type the address and an app
 * password, send a test. The server, port and security come from the provider, under "Server
 * details", which is open for **Custom SMTP** and offers every field Zabbix's media type does —
 * host, port, security, authentication, sender, certificate verification and a timeout.
 *
 * The password is write-only: the field is always empty, the server says only whether one is
 * saved (and whether it comes from `NETCORENOC_SMTP_PASSWORD`), and an empty field keeps it.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { Loading, Failed, SectionHeading } from "../../widgets.js";
import { InfoTip } from "../../info.js";
import { can, session } from "../../session.js";
import { PasswordInput } from "../../password.js";

const SECURITY = [["starttls", "STARTTLS (587)"], ["tls", "SSL/TLS (465)"], ["none", "None (25)"]];
const FIELDS = ["enabled", "provider", "host", "port", "security", "username", "sender",
  "sender_name", "verify_tls", "timeout_s", "public_url", "recovery"];

function draft(data) {
  const form = Object.fromEntries(FIELDS.map((k) => [k, data[k]]));
  if (!form.public_url) form.public_url = (globalThis.location && globalThis.location.origin) || "";
  // Never configured: whoever fills the form in means to send, so "Send email" starts ticked.
  if (!data.host) form.enabled = true;
  return { ...form, password: "", userFollows: !form.username || form.username === form.sender };
}

export class EmailPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, form: null, busy: false, outcome: null,
      testTo: session().email || "", test: null };
  }

  componentDidMount() { this.load(); }

  async load() {
    try {
      const data = await get("/api/email");
      this.setState({ data, form: draft(data), error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  set(patch) {
    const form = { ...this.state.form, ...patch };
    if (form.userFollows) form.username = form.sender;
    this.setState({ form, outcome: null });
  }

  pick(key) {
    const p = this.state.data.providers[key];
    const follows = p.username === null;
    this.set({ provider: key, host: p.host || this.state.form.host, port: p.port, security: p.security,
      userFollows: follows, ...(follows ? {} : { username: p.username || "" }) });
  }

  async save(clearPassword = false) {
    const { form } = this.state;
    this.setState({ busy: true, outcome: null });
    const body = Object.fromEntries(FIELDS.map((k) => [k, form[k]]));
    body.port = Number(body.port);
    body.timeout_s = Number(body.timeout_s);
    body.password = clearPassword ? "" : form.password || null;
    try {
      await post("/api/email", body);
      this.setState({ busy: false, outcome: { ok: true, text: "Saved." } });
      await this.load();
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, text: error.detail || error.message } });
    }
  }

  async sendTest(event) {
    event.preventDefault();
    this.setState({ test: { busy: true } });
    try {
      await post("/api/email/test", { to: this.state.testTo.trim() });
      this.setState({ test: { ok: true, text: `Sent to ${this.state.testTo.trim()}. Check the inbox (and spam).` } });
    } catch (error) {
      this.setState({ test: { ok: false, text: error.detail || error.message } });
    }
  }

  render(_props, { data, error, form, busy, outcome, testTo, test }) {
    if (error) return html`<${Failed} error=${error} retry=${() => this.load()} what="the email settings" />`;
    if (!data) return html`<${Loading} label="Reading the email settings" />`;
    const writable = can("config.write");
    const provider = data.providers[form.provider] || data.providers.custom;
    const custom = form.provider === "custom";
    const passwordHint = data.password_source === "environment"
      ? "Set by NETCORENOC_SMTP_PASSWORD; this field is ignored."
      : data.password_set ? "A password is saved. Leave empty to keep it." : "";
    return html`<div class="settingsview email">
      <div class="settings-intro"><p><b>Outgoing email.</b> Used to send password-reset links, so a
        person who forgot a password can choose a new one from the sign-in screen. Pick your provider,
        enter the address and an app password, and send a test.</p>
        <p><span class=${cx("badge", data.recovery_available ? "badge-ok" : "badge-warn")}>
          ${data.recovery_available ? "Recovery by email is on" : data.enabled ? "Email on, recovery off" : "Email is off"}</span></p></div>

      <section class="panel-block">
        <${SectionHeading} title="Provider" />
        <div class="provider-grid" role="radiogroup" aria-label="Email provider">
          ${Object.entries(data.providers).map(([key, p]) => html`<button type="button" key=${key}
            role="radio" aria-checked=${form.provider === key} disabled=${!writable}
            class=${cx("provider", form.provider === key && "on")} onClick=${() => this.pick(key)}>${p.label}</button>`)}
        </div>
        <p class="hint provider-note">${provider.note}</p>
      </section>

      <section class="panel-block">
        <${SectionHeading} title="Account" />
        <div class="email-grid">
          <label class="pe-field"><span>Email address (sender)</span>
            <input type="email" value=${form.sender} placeholder="noc@example.com" disabled=${!writable}
              onInput=${(e) => this.set({ sender: e.currentTarget.value.trim() })} /></label>
          <label class="pe-field"><span>Sender name</span>
            <input value=${form.sender_name} maxlength="80" disabled=${!writable}
              onInput=${(e) => this.set({ sender_name: e.currentTarget.value })} /></label>
          ${form.userFollows ? null : html`<label class="pe-field"><span>Username</span>
            <input value=${form.username} disabled=${!writable} autocomplete="off"
              onInput=${(e) => this.set({ username: e.currentTarget.value })} /></label>`}
          ${writable ? html`<div><${PasswordInput} id="smtpPass" label=${custom ? "Password" : "App password"}
            autocomplete="new-password" value=${form.password} onInput=${(v) => this.set({ password: v })} />
            ${passwordHint ? html`<p class="hint">${passwordHint}${data.password_set && data.password_source === "database"
              ? html`${" "}<button type="button" class="linkish" onClick=${() => this.save(true)}>Remove it</button>` : null}</p>` : null}</div>` : null}
          <label class="pe-field email-wide"><span>Console address for links
            <${InfoTip} label="Why this is asked">The reset link points here. It is never taken from
              the request that asks for a link — anyone could forge that and send people to their own
              site.<//></span>
            <input value=${form.public_url} placeholder="https://noc.example.com" disabled=${!writable}
              onInput=${(e) => this.set({ public_url: e.currentTarget.value.trim() })} /></label>
        </div>
        <label class="check-row"><input type="checkbox" checked=${form.enabled} disabled=${!writable}
          onChange=${(e) => this.set({ enabled: e.currentTarget.checked })} /><span><b>Send email</b></span></label>
        <label class="check-row"><input type="checkbox" checked=${form.recovery} disabled=${!writable}
          onChange=${(e) => this.set({ recovery: e.currentTarget.checked })} />
          <span><b>Offer “Forgot your password?”</b> on the sign-in screen, for accounts with a recovery
            email (set in People & access, or by each person under Your account).</span></label>
      </section>

      <details class="panel-block email-server" open=${custom}>
        <summary>Server details${custom ? "" : ` — ${provider.host}:${provider.port}`}</summary>
        <div class="email-grid">
          <label class="pe-field"><span>SMTP server</span>
            <input value=${form.host} placeholder="smtp.example.com" disabled=${!writable}
              onInput=${(e) => this.set({ host: e.currentTarget.value.trim() })} /></label>
          <label class="pe-field"><span>Port</span>
            <input type="number" min="1" max="65535" value=${form.port} disabled=${!writable}
              onInput=${(e) => this.set({ port: e.currentTarget.value })} /></label>
          <label class="pe-field"><span>Security</span>
            <select value=${form.security} disabled=${!writable}
              onChange=${(e) => this.set({ security: e.currentTarget.value })}>
              ${SECURITY.map(([v, label]) => html`<option key=${v} value=${v}>${label}</option>`)}</select></label>
          <label class="pe-field"><span>Timeout (seconds)</span>
            <input type="number" min="3" max="60" value=${form.timeout_s} disabled=${!writable}
              onInput=${(e) => this.set({ timeout_s: e.currentTarget.value })} /></label>
          <label class="check-row email-wide"><input type="checkbox" checked=${form.userFollows} disabled=${!writable}
            onChange=${(e) => this.set({ userFollows: e.currentTarget.checked })} />
            <span>Sign in with the sender address</span></label>
          <label class="check-row email-wide"><input type="checkbox" checked=${form.verify_tls} disabled=${!writable}
            onChange=${(e) => this.set({ verify_tls: e.currentTarget.checked })} />
            <span>Verify the server's certificate</span></label>
        </div>
        ${form.security === "none" ? html`<p class="warnbox">Without TLS the password and every message
          cross the network in clear text. Use it only for a relay on a trusted network.</p>` : null}
        ${form.verify_tls ? null : html`<p class="warnbox">An unverified certificate lets anyone on the
          path pose as the server and read the password.</p>`}
      </details>

      ${writable ? html`<div class="roles-foot save-bar">
        <button type="button" class="primary" disabled=${busy} onClick=${() => this.save()}>
          ${busy ? "Saving…" : "Save email settings"}</button>
        ${outcome ? html`<span class=${outcome.ok ? "ok-note" : "err"} role="status">${outcome.text}</span>` : null}
      </div>
      <form class="panel-block inline-add email-test" onSubmit=${(e) => this.sendTest(e)}>
        <label class="pe-field"><span>Send a test message to</span>
          <input type="email" value=${testTo} placeholder="you@example.com"
            onInput=${(e) => this.setState({ testTo: e.currentTarget.value })} /></label>
        <button type="submit" disabled=${!testTo.trim() || (test && test.busy) || !data.host}>
          ${test && test.busy ? "Sending…" : "Send test"}</button>
        ${test && !test.busy ? html`<p class=${test.ok ? "ok-note" : "err"} role="status">${test.text}</p>` : null}
        ${data.host ? null : html`<p class="hint">Save the settings first; the test uses what is saved.</p>`}
      </form>` : null}
    </div>`;
  }
}
