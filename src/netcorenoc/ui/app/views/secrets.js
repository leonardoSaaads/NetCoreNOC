/* NOC → Vault access: who holds the vault, and the credentials that belong to no customer yet
 * (ADRs #454-#458, #481).
 *
 * The vault itself is unchanged — sealed twice, custodians, a step-up for every act, leases instead
 * of secrets — but its contents now live where they are used (ADR #481): each **customer's**
 * credentials in that customer's book (*Customers → a customer → Access*), so an agent working a
 * case goes straight to the customer's own; and the **AI providers'** keys under *Agents → AI
 * providers*, beside the agents that run on them. What stays here is the vault's own business:
 *
 * * **Custodians & agents** — who holds the vault, who leads the NOC (its KPIs and playbooks),
 *   which tokens are agents (`parts/nocgrants.js`), where `secrets.grants` is held.
 * * **Not assigned** — device credentials stored before they belonged to a customer: each opened
 *   here and given its customer with *Change…* (a step-up), after which it leaves this list.
 *
 * `#/secrets/{id}` still opens any credential by its number — the NOC overview links here from an
 * element that stopped answering.
 */

import { html, cx } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { can } from "../session.js";
import { Icon } from "../icons.js";
import { SecretDetail } from "./parts/secretdetail.js";
import { NocGrants } from "./parts/nocgrants.js";
import { CredentialTable } from "./parts/credlist.js";

const TABS = [
  ["grants", "Custodians & agents", "users", "secrets.grants"],
  ["unassigned", "Not assigned", "vault", null],
];

const isAi = (s) => s.kind.startsWith("ai_");
/** A device credential no customer owns yet (ADR #481). */
const waitingOf = (data) => data.secrets.filter((s) => !isAi(s) && !s.customer_id);

export class Secrets extends Loader {
  constructor(props) {
    super(props);
    this.what = "the vault";
    this.loadingLabel = "Opening the vault";
  }

  async load() { return get("/api/noc/secrets"); }

  /** Re-read without the loading state, so an open credential stays open while the list moves. */
  async refresh() {
    try {
      this.setState({ data: await this.load() });
    } catch (error) {
      this.setState({ status: "error", error });
    }
  }

  /** Opening a credential or a tab is a re-render, not a re-read: the list is already here. */
  routeKey() { return "secrets"; }

  tab(data) {
    const asked = this.props.query && this.props.query.get("tab");
    const allowed = TABS.filter(([, , , cap]) => !cap || can(cap)).map(([id]) => id);
    if (allowed.includes(asked)) return asked;
    return waitingOf(data).length || !allowed.includes("grants") ? "unassigned" : "grants";
  }

  open(id, tab) {
    const q = tab ? `?tab=${tab}` : "";
    this.props.navigate(id == null ? `#/secrets${q}` : `#/secrets/${id}${q}`);
  }

  selected() { return Number((this.props.params || [])[0]) || null; }

  view(data) {
    if (!data.custodian) {
      return html`<${Empty} title="The vault is closed to you" will=${data.explanation} />`;
    }
    const tab = this.tab(data);
    const waiting = waitingOf(data);
    const selected = this.selected();
    return html`<div class="secrets">
      <p class="hint vault-moved"><${Icon} name="info" /> A customer's credentials are in its book —
        ${" "}<a href="#/customers">Customers</a> → a customer → Access; the AI providers' keys are under
        ${" "}<a href="#/agents?tab=providers">Agents → AI providers</a>.</p>
      <div class="tabs-line vault-tabs" role="tablist" aria-label="Vault access">
        ${TABS.filter(([, , , cap]) => !cap || can(cap)).map(([id, label, icon]) => html`<button type="button"
          role="tab" key=${id} aria-selected=${tab === id} class=${cx("tab-line", tab === id && "on")}
          onClick=${() => this.open(null, id)}><${Icon} name=${icon} /> ${label}${id === "unassigned"
            ? html` <b class="seg-count">${waiting.length}</b>` : null}</button>`)}
      </div>
      ${data.warnings.map((w) => html`<p class="warnbox" key=${w}>${w}</p>`)}
      ${tab === "grants" ? html`<${NocGrants} secondFactor=${data.second_factor} />` : null}
      ${tab === "unassigned" ? (waiting.length ? html`<div class="secrets-list">
          <p class="muted">Credentials that belong to no customer. Open one and choose its customer with
            ${" "}<b>Change…</b>: an agent working that customer's case finds it there.</p>
          <${CredentialTable} rows=${waiting} selected=${selected} onOpen=${(id) => this.open(id, "unassigned")} />
        </div>` : html`<${Empty} title="Every credential belongs to a customer"
          will="New credentials are stored from each customer's Access tab." />`) : null}
      ${selected ? html`<${SecretDetail} key=${selected} id=${selected} catalogue=${data.catalogue}
        secondFactor=${data.second_factor} onClose=${() => this.open(null, tab)}
        onChanged=${() => this.refresh()} onDeleted=${() => { this.open(null, tab); this.refresh(); }} />` : null}
    </div>`;
  }
}
