/* NOC → a customer → Access: the credentials that reach its equipment and its mail server
 * (ADR #481).
 *
 * The owner's model: an agent working a case identifies the customer whose equipment the situation
 * touches and goes straight to that customer's credentials — SSH, SNMP, secure Telnet, an API —
 * rather than searching one vault-wide list. So a customer's book holds them: listed here, stored
 * here with the customer already chosen, opened here one at a time (`secretdetail.js`, behind a
 * step-up). Who sees them is the vault's rule, unchanged: only a custodian; anyone else reads the
 * sentence why.
 *
 * ADR #507: **hosts only** — SSH, SNMP, Telnet, SFTP, an API. The customer's mailbox is set up on
 * its Email tab, which keeps its own login (in the same vault); a mail credential is neither listed
 * nor offered here. And the tab opens on a banner that says what this place is: the keys to the
 * customer's network, sealed, leased to agents as a token and never shown to them.
 */

import { html, Component } from "../../../dom.js";
import { get } from "../../../api.js";
import { can } from "../../../session.js";
import { plural } from "../../../format.js";
import { Empty } from "../../../widgets.js";
import { Icon } from "../../../icons.js";
import { SecretDetail } from "./secretdetail.js";
import { SecretForm } from "./secretform.js";
import { CredentialTable } from "./credlist.js";

/** The catalogue a customer's credential is chosen from: what reaches its hosts (not AI, not mail). */
const hostKinds = (catalogue) => ({ ...catalogue, kinds: catalogue.kinds.filter((k) => k.group === "device") });
const MAIL = new Set(["smtp", "imap"]);
const PROTOCOLS = ["SSH", "SNMP v2c / v3", "Telnet", "SFTP", "API token", "Other"];

/** What this tab is, before anything in it: the keys to the customer's network. */
function AccessHero({ c, rows, agents }) {
  return html`<div class="access-hero">
    <svg class="access-art" viewBox="0 0 120 96" aria-hidden="true" focusable="false">
      <rect class="aa-door" x="14" y="10" width="72" height="76" rx="10" />
      <rect class="aa-inner" x="22" y="18" width="56" height="60" rx="6" />
      <circle class="aa-dial" cx="50" cy="48" r="15" />
      <circle class="aa-hub" cx="50" cy="48" r="4" />
      <path class="aa-spoke" d="M50 33v6M50 57v6M35 48h6M59 48h6" />
      <circle class="aa-key-ring" cx="98" cy="62" r="9" />
      <path class="aa-key" d="M98 71v16M98 79h6M98 84h4" />
      <path class="aa-lock" d="M80 24h20v16H80zM84 24v-5a6 6 0 0 1 12 0v5" />
    </svg>
    <div class="access-hero-text">
      <p class="access-eyebrow">Access — the keys to ${c.name}'s network</p>
      <h3>Every login to its hosts, sealed in the vault</h3>
      <p>How the NOC reaches each device: one credential per host or group of hosts. Encrypted in two
        layers; only the vault's custodians store or open one, after confirming who they are. An agent
        leases a short-lived token for its case and <b>never sees a password</b> — and every store,
        read and use is in the audit log.</p>
      <ul class="access-protos" aria-label="What can be stored">
        ${PROTOCOLS.map((p) => html`<li key=${p}>${p}</li>`)}</ul>
      <p class="access-count"><b>${plural(rows, "credential")}</b> · ${agents} switched on for agents</p>
    </div>
  </div>`;
}

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
    const rows = data.secrets.filter((s) => !MAIL.has(s.kind));
    const agents = rows.filter((s) => s.agent_access).length;
    return html`<section class="cust-access" aria-label=${`${c.name}'s credentials`}>
      <${AccessHero} c=${c} rows=${rows.length} agents=${agents} />
      <div class="secrets-bar">
        <p class="muted cust-access-note">Agents stay off a credential until you switch them on, one at a time.</p>
        ${can("secrets.write") && !creating ? html`<button type="button" class="primary"
          onClick=${() => this.setState({ creating: true })}><${Icon} name="key" /> New credential</button>` : null}
      </div>
      ${data.warnings.map((w) => html`<p class="warnbox" key=${w}>${w}</p>`)}
      ${creating ? html`<${SecretForm} catalogue=${hostKinds(data.catalogue)} secondFactor=${data.second_factor}
        customerId=${c.id}
        onSaved=${(saved) => { this.setState({ creating: false, open: saved.id }); this.load(); }}
        onCancel=${() => this.setState({ creating: false })} />` : null}
      ${rows.length ? html`<${CredentialTable} rows=${rows} selected=${open}
          onOpen=${(id) => this.setState({ open: open === id ? null : id })} />`
        : html`<${Empty} title=${`No credentials for ${c.name} yet`}
          will="Store one per device with New credential — SSH, SNMP, Telnet, SFTP or an API. Its mailbox is set up on the Email tab." />`}
      ${open ? html`<${SecretDetail} key=${open} id=${open} catalogue=${data.catalogue}
        secondFactor=${data.second_factor} onClose=${() => this.setState({ open: null })}
        onChanged=${() => this.load()} onDeleted=${() => { this.setState({ open: null }); this.load(); }} />` : null}
    </section>`;
  }
}
