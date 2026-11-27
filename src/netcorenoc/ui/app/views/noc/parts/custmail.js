/* NOC → a customer → Email: the mailbox the customer's cases write from (ADRs #481, #500, #510).
 *
 * Whom a message goes to is chosen per email, on each case's feed (Send email, Reply, Reply all).
 * This tab is the integration, and ADR #510 sets it up **here**, in three steps — provider, account,
 * test and save (`mailwizard.js`) — with its own login: the wizard tries the mailbox without
 * storing anything, and Save seals the login in the vault as this customer's SMTP (and IMAP)
 * credential. Access is for the customer's hosts. Replies are read every minute onto the case's
 * feed (a waiting case goes back to the queue); the log says what was sent, read, or refused.
 */

import { html, Component, cx } from "../../../dom.js";
import { get, post } from "../../../api.js";
import { relative, timeTitle } from "../../../format.js";
import { Icon } from "../../../icons.js";
import { session } from "../../../session.js";
import { MailWizard, ProviderMark } from "../../../mailwizard.js";

const OUTCOME = {
  sent: ["Sent", "ok"], failed: ["Not sent", "alarm"], posted: ["On the case", "ok"],
  unmatched: ["No case", "warn"], refused: ["Refused", "warn"],
};
/** A stored server back to the provider it most likely is, for the wizard to start on. */
const HOSTS = { "smtp.gmail.com": "gmail", "smtp-mail.outlook.com": "outlook", "smtp.office365.com": "microsoft",
  "smtp.mail.yahoo.com": "yahoo", "smtp.mail.me.com": "icloud", "smtp.zoho.com": "zoho" };
const SMTP_SEC = { STARTTLS: "starttls", SMTPS: "tls", plain: "none" };
const IMAP_SEC = { IMAPS: "tls", STARTTLS: "starttls" };

/** The wizard's starting point from what is saved. */
function initialOf(d) {
  const s = d.smtp || {};
  const i = d.imap || {};
  return { provider: HOSTS[s.host] || (s.host ? "custom" : "gmail"), sender: d.sender, sender_name: d.sender_name,
    host: s.host || "", port: s.port || 587, security: SMTP_SEC[s.protocol_version] || "starttls",
    imap_host: i.host || "", imap_port: i.port || 993, imap_security: IMAP_SEC[i.protocol_version] || "tls",
    reads: !!d.imap, password_set: !!d.smtp };
}

/** The body the account routes take, from the wizard's draft. */
function account(draft) {
  return { provider: draft.provider, sender: draft.sender, sender_name: draft.sender_name, username: draft.username,
    password: draft.password, auth: draft.auth, oauth_client_id: draft.oauth_client_id, oauth_tenant: draft.oauth_tenant,
    flow: draft.flow, smtp_host: draft.host, smtp_port: Number(draft.port), smtp_security: draft.security,
    imap_host: draft.imap_host, imap_port: Number(draft.imap_port), imap_security: draft.imap_security };
}

export class CustomerMail extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, changing: false };
  }

  componentDidMount() { this.load(); }

  async load() {
    try {
      const data = await get(`/api/noc/customers/${this.props.c.id}/mail`);
      this.setState({ data, error: null, changing: false });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  mailbox(c, data) {
    const s = data.smtp;
    if (!s) {
      return html`<div class="cm-empty">
        <p><b>No mailbox yet.</b> ${data.fallback ? "Until one is set up, its emails leave through the appliance's own server."
          : "Emails about its cases cannot leave until one is set up."}</p>
        ${data.custodian ? html`<button type="button" class="primary" onClick=${() => this.setState({ changing: true })}>
          <${Icon} name="mail" /> Set up the mailbox</button>`
          : html`<p class="muted">A vault custodian sets the mailbox up: its login is kept in the vault.</p>`}
      </div>`;
    }
    const provider = HOSTS[s.host] || "custom";
    return html`<div class="cm-box">
      <${ProviderMark} id=${provider} size=${36} />
      <div><b>${data.sender_name ? `${data.sender_name} <${data.sender}>` : data.sender}</b>
        <p class="muted">Sends through ${s.host}:${s.port}${data.imap ? html` · reads replies from ${data.imap.host}
          ${" "}— every ${Math.round(data.look_every_s)} s, last ${data.polled_at
            ? html`<span title=${timeTitle(data.polled_at)}>${relative(data.polled_at)}</span>` : "not yet"}` : " · replies are not read"}</p>
        ${data.poll_error ? html`<p class="err">${data.poll_error}</p>` : null}</div>
      ${data.custodian ? html`<button type="button" onClick=${() => this.setState({ changing: true })}>
        <${Icon} name="settings" /> Change</button>` : null}
    </div>`;
  }

  render({ c }, { data, error, changing }) {
    if (!data) return error ? html`<p class="err" role="alert">${error}</p>` : html`<p class="muted">Reading ${c.name}'s email settings…</p>`;
    const base = `/api/noc/customers/${c.id}/mail`;
    return html`<section class="cust-mail" aria-label=${`${c.name}'s email`}>
      ${changing ? html`<${MailWizard} providers=${data.providers} initial=${initialOf(data)} withImap
          me=${session().email || ""} onCancel=${() => this.setState({ changing: false })}
          onTry=${(draft, to) => post(`${base}/try`, { to, account: account(draft) })}
          onSave=${async (draft) => { await post(`${base}/account`, account(draft)); await this.load(); }} />`
        : html`<section class="panel-block cm-form">
          <h3><${Icon} name="mail" /> Mailbox</h3>
          <p class="hint">Emails about this customer's cases are written on each case's feed — <b>Send email</b>,${" "}
            <b>Reply</b>, <b>Reply all</b> — and leave from this mailbox. Replies are read back onto the case.</p>
          ${this.mailbox(c, data)}
        </section>`}
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <section class="panel-block cm-log" aria-labelledby="cm-log-h">
        <h3 id="cm-log-h">Messages</h3>
        ${data.messages.length ? html`<div class="table-scroll"><table class="data">
          <thead><tr><th scope="col">When</th><th scope="col"></th><th scope="col">Outcome</th>
            <th scope="col">Subject</th><th scope="col">From / to</th><th scope="col">Case</th></tr></thead>
          <tbody>${data.messages.map((m) => {
            const [word, tone] = OUTCOME[m.outcome] || [m.outcome, ""];
            const mark = m.direction === "out" ? "send" : "mail";
            return html`<tr key=${m.id}>
              <td title=${timeTitle(m.at)}>${relative(m.at)}</td>
              <td><span class="cm-dir" title=${m.direction === "out" ? "Sent" : "Received"}><${Icon}
                name=${mark} /></span></td>
              <td><span class=${cx("badge", tone && `badge-${tone}`)} title=${m.detail || ""}>${word}</span></td>
              <td class="cm-subject">${m.subject || html`<span class="muted">(no subject)</span>`}</td>
              <td class="muted">${m.direction === "out" ? m.recipients : m.sender}</td>
              <td>${m.situation_id ? html`<a href=${`#/cases/${m.situation_id}`}>#${m.situation_id}</a>` : "—"}</td>
            </tr>`;
          })}</tbody></table></div>`
          : html`<p class="muted">Nothing sent or read yet.</p>`}
      </section>
    </section>`;
  }
}
