/* NOC → a customer → Email: the server side of the customer's email (ADRs #481, #500).
 *
 * Whom a message goes to is chosen per email, on each case's feed (Send email, Reply, Reply all).
 * This tab is the integration: which SMTP credential sends and as whom, which IMAP mailbox the
 * replies are read from (every minute, onto the case's feed — a waiting case goes back to the
 * queue), a test message to any address, and the log — sent, failed, on the case, unmatched or
 * refused, each with why. The credentials are stored under Access and chosen here by a custodian.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";

const OUTCOME = {
  sent: ["Sent", "ok"], failed: ["Not sent", "alarm"], posted: ["On the case", "ok"],
  unmatched: ["No case", "warn"], refused: ["Refused", "warn"],
};

function credentialLabel(c) {
  return c ? `${c.name} · ${c.host}:${c.port}` : "";
}

export class CustomerMail extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, form: null, error: null, busy: false, saved: false, test: null,
      testTo: "" };
  }

  componentDidMount() { this.load(); }

  async load() {
    try {
      const data = await get(`/api/noc/customers/${this.props.c.id}/mail`);
      this.setState({ data, form: this.formOf(data), error: null });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  formOf(d) {
    return {
      sender: d.sender, sender_name: d.sender_name,
      smtp_secret_id: d.smtp ? d.smtp.id : null, imap_secret_id: d.imap ? d.imap.id : null,
      read_replies: d.read_replies,
    };
  }

  set(key, value) { this.setState({ form: { ...this.state.form, [key]: value }, saved: false }); }

  async save(event) {
    event.preventDefault();
    this.setState({ busy: true, error: null, saved: false });
    try {
      const data = await post(`/api/noc/customers/${this.props.c.id}/mail`,
        { ...this.state.form, version: this.state.data.version });
      this.setState({ busy: false, data, form: this.formOf(data), saved: true });
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  async sendTest() {
    this.setState({ test: { busy: true } });
    try {
      const answer = await post(`/api/noc/customers/${this.props.c.id}/mail/test`,
        { to: this.state.testTo.trim() });
      this.setState({ test: answer });
    } catch (error) {
      this.setState({ test: { ok: false, detail: error.message } });
    }
  }

  select(kind, key, chosen) {
    const { data, form } = this.state;
    const options = data.choices.filter((c) => c.kind === kind);
    if (!data.custodian) {
      return html`<p class="cm-cred-fixed">${chosen ? credentialLabel(chosen)
        : html`<span class="muted">none — a custodian chooses it</span>`}</p>`;
    }
    return html`<select id=${`cm-${kind}`} value=${form[key] ?? ""}
      onChange=${(e) => this.set(key, e.currentTarget.value ? Number(e.currentTarget.value) : null)}>
      <option value="">${kind === "smtp" ? (data.fallback ? "The appliance's email server" : "None") : "None — replies are not read"}</option>
      ${options.map((c) => html`<option key=${c.id} value=${c.id}>${credentialLabel(c)}</option>`)}
    </select>`;
  }

  render({ c }, { data, form, error, busy, saved, test }) {
    if (!data) return error ? html`<p class="err" role="alert">${error}</p>` : html`<p class="muted">Reading ${c.name}'s email settings…</p>`;
    const edit = data.may_edit;
    return html`<section class="cust-mail" aria-label=${`${c.name}'s email`}>
      <form class="panel-block cm-form" onSubmit=${(e) => this.save(e)}>
        <h3><${Icon} name="mail" /> Mail server</h3>
        <p class="hint">Emails about this customer's cases are written on each case's feed — <b>Send email</b>,${" "}
          <b>Reply</b>, <b>Reply all</b> — and leave through the server below. Replies are read back onto the case.</p>
        <div class="cm-grid">
          <fieldset class="cm-side">
            <legend><${Icon} name="send" /> Outgoing (SMTP)</legend>
            <div class="pe-field"><label for="cm-smtp">Send through</label>${this.select("smtp", "smtp_secret_id", data.smtp)}</div>
            <div class="pe-field"><label for="cm-from">Sender address</label>
              <input id="cm-from" value=${form.sender} disabled=${!edit} placeholder="the SMTP account's address"
                onInput=${(e) => this.set("sender", e.currentTarget.value)} /></div>
            <div class="pe-field"><label for="cm-name">Sender name (optional)</label>
              <input id="cm-name" value=${form.sender_name} disabled=${!edit} placeholder="NOC — Your company"
                onInput=${(e) => this.set("sender_name", e.currentTarget.value)} /></div>
          </fieldset>
          <fieldset class="cm-side">
            <legend><${Icon} name="inbox" /> Incoming (IMAP)</legend>
            <div class="pe-field"><label for="cm-imap">Read replies from</label>${this.select("imap", "imap_secret_id", data.imap)}</div>
            <label class="check"><input type="checkbox" checked=${form.read_replies} disabled=${!edit}
              onChange=${(e) => this.set("read_replies", e.currentTarget.checked)} /> Read the replies and put them on the case</label>
            <p class="muted cm-poll">${data.imap
              ? html`Read every ${Math.round(data.look_every_s)} s · last read ${data.polled_at
                ? html`<span title=${timeTitle(data.polled_at)}>${relative(data.polled_at)}</span>` : "not yet"}${data.poll_error
                ? html` · <span class="err">${data.poll_error}</span>` : ""}`
              : "Replies are not read until an IMAP credential is chosen."}</p>
          </fieldset>
        </div>
        ${data.custodian && !data.choices.length ? html`<p class="hint">Store the customer's SMTP and IMAP
          credentials under <a href=${`#/customers/${c.id}/access`}>Access</a> to choose them here.</p>` : null}
        ${edit ? html`<div class="cm-actions">
          <button type="submit" class="primary" disabled=${busy}>${busy ? "Saving…" : "Save"}</button>
          <span class="cm-test">
            <input type="email" value=${this.state.testTo} placeholder="send a test to…" aria-label="Test address"
              onInput=${(e) => this.setState({ testTo: e.currentTarget.value })} />
            <button type="button" disabled=${!this.state.testTo.trim() || (test && test.busy)} onClick=${() => this.sendTest()}>
              <${Icon} name="send" /> Send a test</button>
          </span>
          ${saved ? html`<span class="ok-note" role="status">Saved.</span>` : null}
          ${test && !test.busy ? html`<span class=${test.ok ? "ok-note" : "err"} role="status">${test.ok ? "Test " : "Not sent: "}${test.detail}</span>` : null}
        </div>` : null}
        ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      </form>
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
