/* NOC → a customer → Email: whom the customer update goes to, the mailbox replies come back to,
 * and every message sent or read (ADR #481).
 *
 * A case's customer update, once sent, is emailed to the customer's recipients with the case's tag
 * in the subject; the customer's reply, read from its mailbox every minute, lands on the case's
 * feed and — when the case was waiting on the customer — puts it back in the queue for whichever
 * agent is free. This tab is where that is set up and watched: the recipients and the sender (the
 * customer's book, kept by who works cases), the SMTP and IMAP credentials (stored under Access,
 * chosen here by a custodian), a test message, and the log — sent, failed, posted, unmatched or
 * refused, each with why.
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
    this.state = { data: null, form: null, error: null, busy: false, saved: false, test: null };
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
      recipients: d.recipients, cc: d.cc, sender: d.sender, sender_name: d.sender_name,
      smtp_secret_id: d.smtp ? d.smtp.id : null, imap_secret_id: d.imap ? d.imap.id : null,
      send_updates: d.send_updates, read_replies: d.read_replies,
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
      const answer = await post(`/api/noc/customers/${this.props.c.id}/mail/test`, {});
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
        <h3><${Icon} name="mail" /> Customer updates by email</h3>
        <p class="hint">A sent customer update is emailed here, its subject tagged with the case — a reply lands on the case's feed and puts a waiting case back in the queue.</p>
        <div class="cm-grid">
          <div class="pe-field"><label for="cm-to">Recipients</label>
            <input id="cm-to" value=${form.recipients} disabled=${!edit} placeholder="noc@customer.example, oncall@customer.example"
              onInput=${(e) => this.set("recipients", e.currentTarget.value)} /></div>
          <div class="pe-field"><label for="cm-cc">Copy (optional)</label>
            <input id="cm-cc" value=${form.cc} disabled=${!edit}
              onInput=${(e) => this.set("cc", e.currentTarget.value)} /></div>
          <div class="pe-field"><label for="cm-from">Sender address</label>
            <input id="cm-from" value=${form.sender} disabled=${!edit} placeholder="the SMTP account's address"
              onInput=${(e) => this.set("sender", e.currentTarget.value)} /></div>
          <div class="pe-field"><label for="cm-name">Sender name (optional)</label>
            <input id="cm-name" value=${form.sender_name} disabled=${!edit} placeholder="NOC — Your company"
              onInput=${(e) => this.set("sender_name", e.currentTarget.value)} /></div>
          <div class="pe-field"><label for="cm-smtp">Send through (SMTP)</label>${this.select("smtp", "smtp_secret_id", data.smtp)}</div>
          <div class="pe-field"><label for="cm-imap">Read replies from (IMAP)</label>${this.select("imap", "imap_secret_id", data.imap)}</div>
        </div>
        ${data.custodian && !data.choices.length ? html`<p class="hint">Store the customer's SMTP and IMAP
          credentials under <a href=${`#/customers/${c.id}/access`}>Access</a> to choose them here.</p>` : null}
        <div class="cm-toggles">
          <label class="check"><input type="checkbox" checked=${form.send_updates} disabled=${!edit}
            onChange=${(e) => this.set("send_updates", e.currentTarget.checked)} /> Email each customer update it is sent</label>
          <label class="check"><input type="checkbox" checked=${form.read_replies} disabled=${!edit}
            onChange=${(e) => this.set("read_replies", e.currentTarget.checked)} /> Read the replies and put them on the case</label>
        </div>
        ${edit ? html`<div class="cm-actions">
          <button type="submit" class="primary" disabled=${busy}>${busy ? "Saving…" : "Save"}</button>
          <button type="button" disabled=${!data.recipients || (test && test.busy)} onClick=${() => this.sendTest()}>
            <${Icon} name="send" /> Send a test message</button>
          ${saved ? html`<span class="ok-note" role="status">Saved.</span>` : null}
          ${test && !test.busy ? html`<span class=${test.ok ? "ok-note" : "err"} role="status">${test.ok ? "Test " : "Not sent: "}${test.detail}</span>` : null}
        </div>` : null}
        ${error ? html`<p class="err" role="alert">${error}</p>` : null}
        <p class="muted cm-poll">${data.imap
          ? html`Replies are read every ${Math.round(data.look_every_s)} s · last read ${data.polled_at
            ? html`<span title=${timeTitle(data.polled_at)}>${relative(data.polled_at)}</span>` : "not yet"}${data.poll_error
            ? html` · <span class="err">${data.poll_error}</span>` : ""}`
          : "Replies are not read until an IMAP credential is chosen."}</p>
      </form>
      <section class="panel-block cm-log" aria-labelledby="cm-log-h">
        <h3 id="cm-log-h">Messages</h3>
        ${data.messages.length ? html`<div class="table-scroll"><table class="data">
          <thead><tr><th scope="col">When</th><th scope="col"></th><th scope="col">Outcome</th>
            <th scope="col">Subject</th><th scope="col">From / to</th><th scope="col">Case</th></tr></thead>
          <tbody>${data.messages.map((m) => {
            const [word, tone] = OUTCOME[m.outcome] || [m.outcome, ""];
            return html`<tr key=${m.id}>
              <td title=${timeTitle(m.at)}>${relative(m.at)}</td>
              <td><span class="cm-dir" title=${m.direction === "out" ? "Sent" : "Received"}><${Icon}
                name=${m.direction === "out" ? "send" : "mail"} /></span></td>
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
