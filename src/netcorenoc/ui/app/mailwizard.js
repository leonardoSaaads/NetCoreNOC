/* Email, set up in three steps — provider, account, test and save (ADR #510).
 *
 * The owner's words: as Maintenance is, by steps, each validated before the next. Shared by
 * Settings → Email (the appliance's own mailbox) and a customer's Email tab (the mailbox its cases
 * write from, `withImap`), which differ only in where the draft is tried and kept — `onTry` and
 * `onSave` — and in the customer's inbox for replies.
 *
 * Outlook.com and Hotmail refuse a password: their account step is **Sign in with Microsoft** — a
 * code to type at microsoft.com/devicelogin, polled until the owner approves (`/api/mail/oauth`).
 * The other providers take an app password, with the steps to one. The server's details are folded
 * away and filled from the provider; Save is offered once a test went through.
 */

import { html, Component, cx } from "./dom.js";
import { post } from "./api.js";
import { Icon } from "./icons.js";
import { BrandMark } from "./brands.js";
import { PasswordInput } from "./password.js";

const STEPS = [["provider", "Provider"], ["account", "Account"], ["test", "Test and save"]];
const SECURITY = [["starttls", "STARTTLS (587)"], ["tls", "SSL/TLS (465)"], ["none", "None (25)"]];
const ADDRESS = /^[^@\s<>"',;:]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/;
/** Where each provider's replies are read (IMAPS 993), for the customer's inbox. */
const IMAP = { gmail: "imap.gmail.com", outlook: "outlook.office365.com", microsoft: "outlook.office365.com",
  yahoo: "imap.mail.yahoo.com", icloud: "imap.mail.me.com", zoho: "imap.zoho.com" };
const TENANTS = [["consumers", "Personal account — Outlook.com, Hotmail, Live"],
  ["organizations", "Work or school — Microsoft 365"]];
const REGISTER = [
  "Open entra.microsoft.com → App registrations → New registration (free).",
  "Supported account types: personal Microsoft accounts (and work accounts, for Microsoft 365).",
  "Authentication → Allow public client flows → Yes.",
  "API permissions → Add → APIs my organization uses → Office 365 Exchange Online → Delegated: SMTP.Send and IMAP.AccessAsUser.All.",
  "Copy the Application (client) ID here.",
];

export const validAddress = (text) => ADDRESS.test(String(text || "").trim());

/** A provider's mark; a custom server is a plain tile, never another company's logo. */
export function ProviderMark({ id, size = 28 }) {
  return id === "custom" || !id
    ? html`<span class="brand-tile provider-own mw-own" aria-hidden="true"><${Icon} name="network" /></span>`
    : html`<${BrandMark} brand=${id} size=${size} />`;
}

/** The draft a wizard starts from: what is saved, or the first provider's defaults. */
export function startFrom(saved) {
  return {
    provider: saved.provider || "gmail", sender: saved.sender || "", sender_name: saved.sender_name || "",
    username: saved.username || "", password: "", auth: saved.auth || "password",
    oauth_client_id: saved.oauth_client_id || "", oauth_tenant: saved.oauth_tenant || "consumers",
    host: saved.host || "", port: saved.port || 587, security: saved.security || "starttls",
    imap_host: saved.imap_host || "", imap_port: saved.imap_port || 993, imap_security: saved.imap_security || "tls",
    reads: saved.imap_host ? true : !!saved.reads,
  };
}

/** Microsoft's sign-in: the code, the link, and the wait until it is approved. */
class MicrosoftSignIn extends Component {
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
    const ok = /^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(d.oauth_client_id.trim());
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
        ? html`<p class="ok-note" role="status"><${Icon} name="check" /> Signed in with Microsoft${account ? ` as ${account}` : ""}.
            <button type="button" class="linkish" disabled=${!ok || busy} onClick=${() => this.start()}>Sign in again</button></p>`
        : html`<button type="button" class="primary" disabled=${!ok || busy} onClick=${() => this.start()}>
            ${busy ? "Asking Microsoft…" : "Sign in with Microsoft"}</button>`}
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
    </div>`;
  }
}

export class MailWizard extends Component {
  constructor(props) {
    super(props);
    this.state = { step: 0, d: startFrom(props.initial || {}), flow: null, details: false, sameUser: true,
      testTo: "sender", other: "", test: null, busy: false, error: null };
  }

  provider() { return this.props.providers[this.state.d.provider] || {}; }

  oauth() {
    const p = this.provider();
    return p.auth === "oauth" || (p.auth === "either" && this.state.d.auth === "oauth");
  }

  set(patch) { this.setState({ d: { ...this.state.d, ...patch }, test: null, error: null }); }

  pick(key) {
    const p = this.props.providers[key];
    this.setState({ step: 1, details: key === "custom", test: null, flow: null, d: { ...this.state.d, provider: key,
      host: p.host || this.state.d.host, port: p.port || 587, security: p.security || "starttls",
      auth: p.auth === "oauth" ? "oauth" : "password",
      imap_host: IMAP[key] || this.state.d.imap_host } });
  }

  /** What stops the account step, in words; null when it may go on. */
  accountProblem() {
    const { d, flow } = this.state;
    const { initial = {} } = this.props;
    if (!validAddress(d.sender)) return "The sender must be an email address, such as noc@example.com.";
    if (!d.host.trim() || !(d.port > 0)) return "The SMTP server and port are needed (Server details).";
    if (this.oauth()) {
      const kept = initial.signed_in && initial.oauth_client_id === d.oauth_client_id.trim();
      return flow || kept ? null : "Sign in with Microsoft first.";
    }
    if (!d.password && !(initial.password_set && initial.provider === d.provider)) return "Type the app password.";
    if (this.props.withImap && d.reads && !d.imap_host.trim()) return "The IMAP server is needed to read replies.";
    return null;
  }

  draft() {
    const { d, flow, sameUser } = this.state;
    const oauth = this.oauth();
    return { ...d, username: sameUser ? d.sender.trim() : d.username.trim(), sender: d.sender.trim(),
      auth: oauth ? "oauth" : "password", password: oauth ? null : (d.password || null),
      oauth_client_id: d.oauth_client_id.trim(), flow: oauth ? flow : null,
      imap_host: this.props.withImap && d.reads ? d.imap_host.trim() : "" };
  }

  target() {
    const { testTo, other, d } = this.state;
    if (testTo === "sender") return d.sender.trim();
    if (testTo === "me") return this.props.me || "";
    return other.trim();
  }

  async run(what) {
    this.setState({ busy: true, error: null, ...(what === "try" ? { test: null } : {}) });
    try {
      if (what === "try") this.setState({ test: await this.props.onTry(this.draft(), this.target()), busy: false });
      else { await this.props.onSave(this.draft()); this.setState({ busy: false }); }
    } catch (error) {
      this.setState({ busy: false, ...(what === "try" ? { test: { ok: false, detail: error.detail || error.message } }
        : { error: error.detail || error.message }) });
    }
  }

  providerStep() {
    const { providers } = this.props;
    return html`<div class="mw-providers" role="list">
      ${Object.entries(providers).map(([key, p]) => html`<button type="button" role="listitem" key=${key}
        class=${cx("mw-provider", this.state.d.provider === key && "on")} onClick=${() => this.pick(key)}>
        <${ProviderMark} id=${key} />
        <span><b>${p.label}</b><small class="muted">${p.auth === "oauth" ? "Sign in with Microsoft"
          : p.auth === "either" ? "Microsoft sign-in or a password" : key === "custom" ? "Any SMTP server" : "App password"}</small></span>
      </button>`)}
    </div>`;
  }

  accountStep() {
    const { d, details, sameUser } = this.state;
    const p = this.provider();
    const oauth = this.oauth();
    return html`<div class="mw-account">
      <p class="mw-note"><${ProviderMark} id=${d.provider} size=${20} /> ${p.note}</p>
      <div class="mw-fields">
        <div class="pe-field"><label for="mw-sender">Email address (sender)</label>
          <input id="mw-sender" type="email" value=${d.sender} placeholder="noc@example.com" autocomplete="off"
            onInput=${(e) => this.set({ sender: e.currentTarget.value })} /></div>
        <div class="pe-field"><label for="mw-name">Sender name</label>
          <input id="mw-name" value=${d.sender_name} maxlength="80" placeholder="NOC — Your company"
            onInput=${(e) => this.set({ sender_name: e.currentTarget.value })} /></div>
      </div>
      ${p.auth === "either" ? html`<div class="seg" role="radiogroup" aria-label="How it signs in">
        ${[["oauth", "Sign in with Microsoft"], ["password", "Password"]].map(([v, l]) => html`<button type="button" key=${v}
          role="radio" aria-checked=${d.auth === v} class=${cx("seg-btn", d.auth === v && "on")}
          onClick=${() => this.set({ auth: v })}>${l}</button>`)}</div>` : null}
      ${oauth ? html`<${MicrosoftSignIn} d=${d} signedIn=${(this.props.initial || {}).signed_in}
          onChange=${(patch) => this.set(patch)} onFlow=${(flow) => this.setState({ flow })} />`
        : html`<div class="pe-field mw-pass">
          <${PasswordInput} id="mw-pass" label=${d.provider === "custom" ? "Password" : "App password"}
            value=${d.password} autocomplete="new-password" onInput=${(v) => this.set({ password: v })} />
          ${(this.props.initial || {}).password_set && !d.password
            ? html`<small class="muted">A password is saved: leave it empty to keep it.</small>` : null}
          ${(p.steps || []).length ? html`<ol class="mw-steps">${p.steps.map((s) => html`<li key=${s}>${s}</li>`)}</ol>` : null}</div>`}
      <button type="button" class="linkish mw-toggle" aria-expanded=${details}
        onClick=${() => this.setState({ details: !details })}><${Icon} name="chevron" /> Server details</button>
      ${details ? html`<div class="mw-fields mw-server">
        <div class="pe-field"><label for="mw-host">SMTP server</label><input id="mw-host" value=${d.host}
          onInput=${(e) => this.set({ host: e.currentTarget.value.trim() })} /></div>
        <div class="pe-field"><label for="mw-port">Port</label><input id="mw-port" type="number" min="1" max="65535"
          value=${d.port} onInput=${(e) => this.set({ port: Number(e.currentTarget.value) })} /></div>
        <div class="pe-field"><label for="mw-sec">Security</label><select id="mw-sec" value=${d.security}
          onChange=${(e) => this.set({ security: e.currentTarget.value })}>${SECURITY.map(([v, l]) =>
            html`<option key=${v} value=${v}>${l}</option>`)}</select></div>
        <label class="check"><input type="checkbox" checked=${sameUser}
          onChange=${(e) => this.setState({ sameUser: e.currentTarget.checked })} /> The username is the sender address</label>
        ${sameUser ? null : html`<div class="pe-field"><label for="mw-user">Username</label><input id="mw-user"
          value=${d.username} onInput=${(e) => this.set({ username: e.currentTarget.value })} /></div>`}
      </div>` : null}
      ${this.props.withImap ? html`<fieldset class="mw-imap"><legend><${Icon} name="inbox" /> Replies</legend>
        <label class="check"><input type="checkbox" checked=${d.reads}
          onChange=${(e) => this.set({ reads: e.currentTarget.checked })} /> Read the customer's replies from this mailbox and put them on the case</label>
        ${d.reads ? html`<div class="mw-fields"><div class="pe-field"><label for="mw-imap">IMAP server</label>
            <input id="mw-imap" value=${d.imap_host} onInput=${(e) => this.set({ imap_host: e.currentTarget.value.trim() })} /></div>
          <div class="pe-field"><label for="mw-iport">Port</label><input id="mw-iport" type="number" value=${d.imap_port}
            onInput=${(e) => this.set({ imap_port: Number(e.currentTarget.value) })} /></div>
          <div class="pe-field"><label for="mw-isec">Security</label><select id="mw-isec" value=${d.imap_security}
            onChange=${(e) => this.set({ imap_security: e.currentTarget.value })}>
            <option value="tls">SSL/TLS (993)</option><option value="starttls">STARTTLS (143)</option></select></div></div>` : null}
      </fieldset>` : null}
    </div>`;
  }

  testStep() {
    const { testTo, other, test, busy, d } = this.state;
    const options = [["sender", `The sender itself — ${d.sender}`],
      ...(this.props.me ? [["me", `Me — ${this.props.me}`]] : []), ["other", "Another address"]];
    return html`<div class="mw-test">
      <p class="mw-label">Send a test message to</p>
      <div class="mw-targets" role="radiogroup" aria-label="Send the test to">
        ${options.map(([v, l]) => html`<label key=${v} class=${cx("mw-target", testTo === v && "on")}>
          <input type="radio" name="mw-to" checked=${testTo === v} onChange=${() => this.setState({ testTo: v, test: null })} /> ${l}</label>`)}
      </div>
      ${testTo === "other" ? html`<input type="email" class="mw-other" aria-label="Test address" value=${other}
        placeholder="someone@example.com" onInput=${(e) => this.setState({ other: e.currentTarget.value, test: null })} />` : null}
      <button type="button" disabled=${busy || !validAddress(this.target())} onClick=${() => this.run("try")}>
        <${Icon} name="send" /> ${busy && !test ? "Sending…" : "Send the test"}</button>
      ${test ? html`<div class=${cx("mw-result", test.ok ? "ok" : "bad")} role="status">
        <p><b>${test.sent === false || (!test.ok && test.sent == null) ? "Not sent" : "Sent"}</b> — ${test.detail}</p>
        ${test.read != null ? html`<p><b>Inbox</b> — ${test.read ? "signed in; replies will be read" : test.read_detail}</p>` : null}
        ${test.ok ? html`<p class="muted">Check that it arrived, then save.</p>` : null}</div>` : null}
      ${this.props.children}
    </div>`;
  }

  render({ onCancel, saveLabel = "Save email settings" }, { step, busy, error, test }) {
    const problem = step === 1 ? this.accountProblem() : null;
    return html`<section class="panel-block mail-wizard" aria-label="Set up email">
      <ol class="mw-stepper">${STEPS.map(([id, label], i) => html`<li key=${id}
        class=${cx(i === step && "on", i < step && "done")}><span>${i < step ? "✓" : i + 1}</span>${label}</li>`)}</ol>
      ${step === 0 ? this.providerStep() : step === 1 ? this.accountStep() : this.testStep()}
      ${problem && step === 1 ? html`<p class="hint mw-problem">${problem}</p>` : null}
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <footer class="mw-nav">
        ${onCancel ? html`<button type="button" class="linkish" onClick=${onCancel}>Cancel</button>` : null}
        ${step > 0 ? html`<button type="button" onClick=${() => this.setState({ step: step - 1, test: null })}>Back</button>` : null}
        ${step === 1 ? html`<button type="button" class="primary" disabled=${!!problem}
          onClick=${() => this.setState({ step: 2 })}>Next</button>` : null}
        ${step === 2 ? html`<button type="button" class="primary" disabled=${busy || !(test && test.ok)}
          title=${test && test.ok ? "" : "Send a test that goes through first"}
          onClick=${() => this.run("save")}>${saveLabel}</button>` : null}
      </footer>
    </section>`;
  }
}
