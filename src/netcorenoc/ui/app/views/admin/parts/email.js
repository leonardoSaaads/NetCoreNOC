/* Settings → Email: the mailbox the appliance sends recovery links from (v0.30.0, ADR #445).
 *
 * ADR #510: set up in three steps — provider, account, test and save — by `mailwizard.js`, each step
 * validated and Save offered once a test went through. Outlook.com and Hotmail sign in with
 * Microsoft (they refuse a password); the rest take an app password, with the steps to one.
 * Configured, this is a card saying what sends and how, with **Change** to run the steps again, and
 * the recovery settings — the console's address for links and "Forgot your password?" — beside it.
 *
 * The password and the sign-in are write-only: the server says only whether one is saved (and
 * whether the password comes from `NETCORENOC_SMTP_PASSWORD`).
 */

import { html, Component, cx } from "../../../dom.js";
import { get, post } from "../../../api.js";
import { Loading, Failed, SectionHeading } from "../../../widgets.js";
import { InfoTip } from "../../../info.js";
import { can, session } from "../../../session.js";
import { Icon } from "../../../icons.js";
import { MailWizard, ProviderMark } from "../../../mailwizard.js";

/** The body `POST /api/email` takes, from what is saved and what the wizard drafted. */
function body(saved, draft, extra) {
  return {
    enabled: true, provider: draft.provider, host: draft.host, port: Number(draft.port), security: draft.security,
    username: draft.username, password: draft.password, sender: draft.sender, sender_name: draft.sender_name,
    verify_tls: saved.verify_tls, timeout_s: saved.timeout_s, public_url: extra.public_url, recovery: extra.recovery,
    auth: draft.auth, oauth_client_id: draft.oauth_client_id, oauth_tenant: draft.oauth_tenant, flow: draft.flow,
  };
}

export class EmailPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, changing: false, extra: null, busy: false, outcome: null };
  }

  componentDidMount() { this.load(); }

  async load() {
    try {
      const data = await get("/api/email");
      const origin = (globalThis.location && globalThis.location.origin) || "";
      this.setState({ data, error: null, changing: !data.host,
        extra: { public_url: data.public_url || origin, recovery: data.recovery, enabled: data.enabled } });
    } catch (error) {
      this.setState({ error });
    }
  }

  /** The recovery settings alone: the rest as saved, nothing typed again. */
  async saveExtra() {
    const { data, extra } = this.state;
    this.setState({ busy: true, outcome: null });
    try {
      await post("/api/email", { ...body(data, { ...data, password: null, flow: null }, extra), enabled: extra.enabled });
      this.setState({ busy: false, outcome: { ok: true, text: "Saved." } });
      await this.load();
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, text: error.detail || error.message } });
    }
  }

  extras(writable) {
    const { extra } = this.state;
    const set = (patch) => this.setState({ extra: { ...extra, ...patch }, outcome: null });
    return html`<div class="email-extra">
      <label class="pe-field"><span>Console address for links
        <${InfoTip} label="Why this is asked">The reset link points here. It is never taken from the
          request that asks for a link — anyone could forge that and send people to their own site.<//></span>
        <input value=${extra.public_url} placeholder="https://noc.example.com" disabled=${!writable}
          onInput=${(e) => set({ public_url: e.currentTarget.value.trim() })} /></label>
      <label class="check-row"><input type="checkbox" checked=${extra.recovery} disabled=${!writable}
        onChange=${(e) => set({ recovery: e.currentTarget.checked })} />
        <span><b>Offer “Forgot your password?”</b> on the sign-in screen, for accounts with a recovery
          email (set in People & access, or by each person under Your account).</span></label>
    </div>`;
  }

  summary(data, writable) {
    const p = data.providers[data.provider] || data.providers.custom;
    const how = data.auth === "oauth" ? (data.signed_in ? "Signed in with Microsoft" : "Microsoft sign-in missing")
      : data.password_source === "environment" ? "Password from NETCORENOC_SMTP_PASSWORD"
      : data.password_set ? "App password saved" : "No password saved";
    return html`<section class="panel-block email-summary">
      <div class="email-summary-head">
        <${ProviderMark} id=${data.provider} size=${36} />
        <div><b>${data.sender || "No sender yet"}</b>
          <p class="muted">${p.label} · ${data.host}:${data.port} · ${how}</p></div>
        <span class=${cx("badge", data.recovery_available ? "badge-ok" : "badge-warn")}>
          ${data.recovery_available ? "Recovery by email is on" : data.enabled ? "Email on, recovery off" : "Email is off"}</span>
        ${writable ? html`<button type="button" onClick=${() => this.setState({ changing: true })}>
          <${Icon} name="settings" /> Change</button>` : null}
      </div>
    </section>`;
  }

  render(_props, { data, error, changing, busy, outcome }) {
    if (error) return html`<${Failed} error=${error} retry=${() => this.load()} what="the email settings" />`;
    if (!data) return html`<${Loading} label="Reading the email settings" />`;
    const writable = can("config.write");
    const { extra } = this.state;
    return html`<div class="settingsview email">
      <p class="muted email-lead">The mailbox password-reset links are sent from. Pick the provider, sign in, send a test, save.</p>
      ${changing && writable ? html`<${MailWizard} providers=${data.providers} initial=${data}
          me=${session().email || ""} onCancel=${data.host ? () => this.setState({ changing: false }) : null}
          onTry=${async (draft, to) => { await post("/api/email/test", { to, draft: body(data, draft, extra) });
            return { ok: true, sent: true, detail: `sent to ${to}. Check the inbox (and spam).` }; }}
          onSave=${async (draft) => { await post("/api/email", body(data, draft, extra)); await this.load(); }}>
          ${this.extras(writable)}
        <//>`
        : html`${this.summary(data, writable)}
          <section class="panel-block">
            <${SectionHeading} icon="key" title="Password recovery" />
            ${this.extras(writable)}
            ${writable ? html`<div class="roles-foot save-bar">
              <label class="check-row"><input type="checkbox" checked=${extra.enabled}
                onChange=${(e) => this.setState({ extra: { ...extra, enabled: e.currentTarget.checked } })} />
                <span><b>Send email</b></span></label>
              <button type="button" class="primary" disabled=${busy} onClick=${() => this.saveExtra()}>
                ${busy ? "Saving…" : "Save"}</button>
              ${outcome ? html`<span class=${outcome.ok ? "ok-note" : "err"} role="status">${outcome.text}</span>` : null}
            </div>` : null}
          </section>`}
    </div>`;
  }
}
