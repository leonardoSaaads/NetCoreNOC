/* NOC → a customer → Access: the credentials that reach its equipment and its mail server
 * (ADR #481).
 *
 * The owner's model: an agent working a case identifies the customer whose equipment the situation
 * touches and goes straight to that customer's credentials — SSH, SNMP, secure Telnet, an API —
 * rather than searching one vault-wide list. So a customer's book holds them: listed here, stored
 * here with the customer already chosen, opened here one at a time (`secretdetail.js`, behind a
 * step-up). Who sees them is the vault's rule, unchanged: only a custodian; anyone else reads the
 * sentence why. The customer's SMTP and IMAP credentials are stored here too, for its Email tab.
 */

import { html, Component } from "../../dom.js";
import { get } from "../../api.js";
import { can } from "../../session.js";
import { plural } from "../../format.js";
import { Empty } from "../../widgets.js";
import { SecretDetail } from "./secretdetail.js";
import { SecretForm } from "./secretform.js";
import { CredentialTable } from "./credlist.js";

/** The catalogue a customer's credential is chosen from: what reaches equipment, and mail. */
const notAi = (catalogue) => ({ ...catalogue, kinds: catalogue.kinds.filter((k) => k.group !== "ai") });

export class CustomerAccess extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, creating: false, open: null };
  }

  componentDidMount() { this.load(); }

  async load() {
    try {
      this.setState({ data: await get(`/api/noc/secrets?customer_id=${this.props.c.id}`), error: null });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  render({ c }, { data, error, creating, open }) {
    if (error) return html`<p class="err" role="alert">${error}</p>`;
    if (!data) return html`<p class="muted">Reading ${c.name}'s credentials…</p>`;
    if (!data.custodian) {
      return html`<${Empty} title="Only the vault's custodians see these credentials"
        will=${data.explanation} />`;
    }
    const rows = data.secrets;
    const agents = rows.filter((s) => s.agent_access).length;
    return html`<section class="cust-access" aria-label=${`${c.name}'s credentials`}>
      <div class="secrets-bar">
        <p class="muted cust-access-note">${plural(rows.length, "credential")} for ${c.name}'s equipment ·
          ${" "}${agents} switched on for agents. An agent leases one for a case and never sees the secret.</p>
        ${can("secrets.write") && !creating ? html`<button type="button" class="primary"
          onClick=${() => this.setState({ creating: true })}>New credential</button>` : null}
      </div>
      ${data.warnings.map((w) => html`<p class="warnbox" key=${w}>${w}</p>`)}
      ${creating ? html`<${SecretForm} catalogue=${notAi(data.catalogue)} secondFactor=${data.second_factor}
        customerId=${c.id}
        onSaved=${(saved) => { this.setState({ creating: false, open: saved.id }); this.load(); }}
        onCancel=${() => this.setState({ creating: false })} />` : null}
      ${rows.length ? html`<${CredentialTable} rows=${rows} selected=${open}
          onOpen=${(id) => this.setState({ open: open === id ? null : id })} />`
        : html`<${Empty} title=${`No credentials for ${c.name} yet`}
          will="Store one per device with New credential — SSH, SNMP, secure Telnet or an API — and the customer's SMTP and IMAP for its Email. Agents stay off until you switch them on, one credential at a time." />`}
      ${open ? html`<${SecretDetail} key=${open} id=${open} catalogue=${data.catalogue}
        secondFactor=${data.second_factor} onClose=${() => this.setState({ open: null })}
        onChanged=${() => this.load()} onDeleted=${() => { this.setState({ open: null }); this.load(); }} />` : null}
    </section>`;
  }
}
