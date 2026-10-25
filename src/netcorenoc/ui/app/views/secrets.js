/* NOC → Secrets: the credentials people and agents use to reach equipment (ADRs #454-#458).
 *
 * A list a custodian can scan in seconds — a light, a name, where it works, what it grants,
 * whether agents may use it — and one credential opened at a time below it. What this screen
 * never shows is a secret, except in the one panel a custodian opens after a step-up.
 *
 * Who sees what is the server's answer, not this screen's: a person who is not a custodian reads
 * an empty vault and the sentence why; the custodians tab is offered where `secrets.grants` is
 * held. Every control is an affordance; every refusal is the server's (`session.js`).
 */

import { html, cx } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { can } from "../session.js";
import { relative, timeTitle } from "../format.js";
import { SecretDetail, StatusLight } from "./parts/secretdetail.js";
import { SecretForm } from "./parts/secretform.js";
import { NocGrants } from "./parts/nocgrants.js";

const STATE_ORDER = { down: 0, degraded: 1, unknown: 2, up: 3 };

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

  tab() {
    const asked = this.props.query && this.props.query.get("tab");
    return asked === "grants" && can("secrets.grants") ? "grants" : "credentials";
  }

  open(id) { this.props.navigate(id == null ? "#/secrets" : `#/secrets/${id}`); }

  view(data) {
    if (!data.custodian) {
      return html`<${Empty} title="The vault is closed to you" will=${data.explanation} />`;
    }
    const tab = this.tab();
    return html`<div class="secrets">
      <div class="access-tabs" role="tablist" aria-label="Secrets">
        <button type="button" role="tab" aria-selected=${tab === "credentials"}
          class=${cx("access-tab", tab === "credentials" && "on")}
          onClick=${() => this.props.navigate("#/secrets")}>Credentials</button>
        ${can("secrets.grants") ? html`<button type="button" role="tab"
          aria-selected=${tab === "grants"} class=${cx("access-tab", tab === "grants" && "on")}
          onClick=${() => this.props.navigate("#/secrets?tab=grants")}>Custodians & agents</button>`
          : null}
      </div>
      ${data.warnings.map((w) => html`<p class="warnbox" key=${w}>${w}</p>`)}
      ${tab === "grants" ? html`<${NocGrants} secondFactor=${data.second_factor} />`
        : this.credentials(data)}
    </div>`;
  }

  credentials(data) {
    const selected = Number((this.props.params || [])[0]) || null;
    const rows = [...data.secrets].sort((a, b) =>
      (STATE_ORDER[a.status.state] - STATE_ORDER[b.status.state]) || a.name.localeCompare(b.name));
    const counts = rows.reduce((acc, s) => ({ ...acc, [s.status.state]: (acc[s.status.state] || 0) + 1 }), {});
    return html`<div class="secrets-list">
      <div class="secrets-bar">
        <span class="secrets-counts" role="status">
          ${["up", "degraded", "down", "unknown"].filter((k) => counts[k]).map((k) => html`
            <${StatusLight} key=${k} compact status=${{ state: k, reason: `${counts[k]} ${k}` }} />
            <b class="secrets-count">${counts[k]}</b>`)}
        </span>
        ${can("secrets.write") && !this.state.creating ? html`<button type="button" class="primary"
          onClick=${() => this.setState({ creating: true })}>New credential</button>` : null}
      </div>
      ${this.state.creating ? html`<${SecretForm} catalogue=${data.catalogue} secondFactor=${data.second_factor}
        onSaved=${(saved) => { this.setState({ creating: false }); this.open(saved.id); this.refresh(); }}
        onCancel=${() => this.setState({ creating: false })} />` : null}
      ${rows.length ? html`<div class="table-scroll"><table class="data secrets-table">
        <thead><tr>
          <th scope="col">Status</th><th scope="col">Name</th><th scope="col" class="wide-only">Type</th>
          <th scope="col">Where</th><th scope="col">Grants</th><th scope="col">Agents</th>
          <th scope="col" class="wide-only">Last check</th>
        </tr></thead>
        <tbody>${rows.map((s) => html`<tr key=${s.id} class=${cx(selected === s.id && "row-selected")}>
          <td><${StatusLight} status=${s.status} /></td>
          <td><button type="button" class="linkish secret-open" onClick=${() => this.open(s.id)}
            aria-expanded=${selected === s.id ? "true" : "false"}>${s.name}</button></td>
          <td class="wide-only">${s.kind_label}</td>
          <td class="mono">${s.host}:${s.port}</td>
          <td><span class=${`access-pill access-${s.access_level}`}>${s.access_label.split(" — ")[0]}</span></td>
          <td>${s.agent_access ? html`<span class="badge badge-warn">on</span>` : html`<span class="muted">off</span>`}</td>
          <td class="wide-only" title=${timeTitle(s.status.last_checked_at)}>${s.status.last_checked_at
            ? relative(s.status.last_checked_at) : "—"}</td>
        </tr>`)}</tbody>
      </table></div>` : html`<${Empty} title="No credentials yet"
        will="Store the first one with New credential. Agents stay off until you switch them on, one credential at a time." />`}
      ${selected ? html`<${SecretDetail} key=${selected} id=${selected} catalogue=${data.catalogue}
        secondFactor=${data.second_factor} onClose=${() => this.open(null)}
        onChanged=${() => this.refresh()} onDeleted=${() => { this.open(null); this.refresh(); }} />` : null}
    </div>`;
  }
}
