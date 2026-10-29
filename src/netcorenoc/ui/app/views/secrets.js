/* NOC → Secrets: the credentials people and agents use to reach equipment, and the AI providers'
 * keys agents run on (ADRs #454-#458, #471).
 *
 * Three tabs, each one thing a custodian came to do — Salesforce's setup pages, Paperclip's few
 * plain screens:
 *
 * * **Device credentials** — SSH, SNMP, API tokens, SFTP, SMTP: a list a custodian scans in seconds
 *   (a light, the kind's mark, a name, where it works, what it grants, whether agents may use it),
 *   filtered by family, and one credential opened at a time below it. A new one starts from cards
 *   that say what each kind is for (`parts/secretform.js`).
 * * **AI providers** — Anthropic, OpenAI, Google AI Studio, xAI, Meta, Ollama: a card per provider
 *   with its logo; paste a key and it is stored, sealed, ready for agents (`parts/aiproviders.js`).
 *   Apart from the device credentials because it is a different thing: what an agent thinks with,
 *   not what it reaches equipment with.
 * * **People & access** — who holds the vault, who leads the NOC (its KPIs and playbooks), which
 *   tokens are agents (`parts/nocgrants.js`), where `secrets.grants` is held.
 *
 * What this screen never shows is a secret, except in the one panel a custodian opens after a
 * step-up. Who sees what is the server's answer, not this screen's: a person who is not a custodian
 * reads an empty vault and the sentence why. Every control is an affordance; every refusal is the
 * server's (`session.js`).
 */

import { html, cx } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { can } from "../session.js";
import { relative, timeTitle } from "../format.js";
import { Icon } from "../icons.js";
import { SecretDetail, StatusLight } from "./parts/secretdetail.js";
import { SecretForm } from "./parts/secretform.js";
import { NocGrants } from "./parts/nocgrants.js";
import { AiProviders } from "./parts/aiproviders.js";
import { ACCESS, FAMILIES, KINDS, KindMark } from "./parts/vaultui.js";

const STATE_ORDER = { down: 0, degraded: 1, unknown: 2, up: 3 };
const TABS = [
  ["devices", "Device credentials", "vault", null],
  ["ai", "AI providers", "sparkle", null],
  ["grants", "People & access", "users", "secrets.grants"],
];

const isAi = (s) => s.kind.startsWith("ai_");

export class Secrets extends Loader {
  constructor(props) {
    super(props);
    this.what = "the vault";
    this.loadingLabel = "Opening the vault";
    this.state = { ...this.state, family: "", find: "" };
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
    const tab = TABS.find(([id]) => id === asked);
    return tab && (!tab[3] || can(tab[3])) ? tab[0] : "devices";
  }

  open(id, tab = this.tab()) {
    const q = tab === "devices" ? "" : `?tab=${tab}`;
    this.props.navigate(id == null ? `#/secrets${q}` : `#/secrets/${id}${q}`);
  }

  view(data) {
    if (!data.custodian) {
      return html`<${Empty} title="The vault is closed to you" will=${data.explanation} />`;
    }
    const tab = this.tab();
    const ai = data.secrets.filter(isAi).length;
    return html`<div class="secrets">
      <div class="tabs-line vault-tabs" role="tablist" aria-label="Secrets">
        ${TABS.filter(([, , , cap]) => !cap || can(cap)).map(([id, label, icon]) => html`<button type="button"
          role="tab" key=${id} aria-selected=${tab === id} class=${cx("tab-line", tab === id && "on")}
          onClick=${() => this.open(null, id)}><${Icon} name=${icon} /> ${label}${id === "devices"
            ? html` <b class="seg-count">${data.secrets.length - ai}</b>` : id === "ai"
            ? html` <b class="seg-count">${ai}</b>` : null}</button>`)}
      </div>
      ${data.warnings.map((w) => html`<p class="warnbox" key=${w}>${w}</p>`)}
      ${tab === "grants" ? html`<${NocGrants} secondFactor=${data.second_factor} />` : null}
      ${tab === "ai" ? html`<${AiProviders} data=${data} selected=${this.selected()}
        onOpen=${(id) => this.open(id, "ai")} onChanged=${() => this.refresh()} />` : null}
      ${tab === "devices" ? this.devices(data) : null}
      ${tab !== "grants" ? this.detail(data) : null}
    </div>`;
  }

  selected() { return Number((this.props.params || [])[0]) || null; }

  detail(data) {
    const selected = this.selected();
    return selected ? html`<${SecretDetail} key=${selected} id=${selected} catalogue=${data.catalogue}
      secondFactor=${data.second_factor} onClose=${() => this.open(null)}
      onChanged=${() => this.refresh()} onDeleted=${() => { this.open(null); this.refresh(); }} />` : null;
  }

  rows(data) {
    const { family, find } = this.state;
    const words = find.trim().toLowerCase();
    return data.secrets.filter((s) => !isAi(s))
      .filter((s) => !family || (KINDS[s.kind] || KINDS.other).family === family)
      .filter((s) => !words || `${s.name} ${s.host} ${s.kind_label} ${s.description || ""}`.toLowerCase().includes(words))
      .sort((a, b) => (STATE_ORDER[a.status.state] - STATE_ORDER[b.status.state]) || a.name.localeCompare(b.name));
  }

  toolbar(devices) {
    const { family, find } = this.state;
    const counts = devices.reduce((acc, s) => ({ ...acc, [s.status.state]: (acc[s.status.state] || 0) + 1 }), {});
    const per = (f) => devices.filter((s) => (KINDS[s.kind] || KINDS.other).family === f).length;
    return html`<div class="secrets-bar">
      <div class="cf-chips" role="group" aria-label="Show">
        <button type="button" class=${cx("cf-chip", !family && "on")} aria-pressed=${!family}
          onClick=${() => this.setState({ family: "" })}>All <b class="seg-count">${devices.length}</b></button>
        ${FAMILIES.filter(([f]) => per(f)).map(([f, label, icon]) => html`<button type="button" key=${f}
          class=${cx("cf-chip", family === f && "on")} aria-pressed=${family === f}
          onClick=${() => this.setState({ family: family === f ? "" : f })}><${Icon} name=${icon} /> ${label}
          <b class="seg-count">${per(f)}</b></button>`)}
      </div>
      <span class="secrets-counts" role="status">
        ${["up", "degraded", "down", "unknown"].filter((k) => counts[k]).map((k) => html`
          <${StatusLight} key=${k} compact status=${{ state: k, reason: `${counts[k]} ${k}` }} />
          <b class="secrets-count">${counts[k]}</b>`)}
      </span>
      <label class="visually-hidden" for="secrets-find">Find a credential</label>
      <input id="secrets-find" type="search" class="find" placeholder="Find by name or host" value=${find}
        onInput=${(e) => this.setState({ find: e.currentTarget.value })} />
      ${can("secrets.write") && !this.state.creating ? html`<button type="button" class="primary"
        onClick=${() => this.setState({ creating: true })}>New credential</button>` : null}
    </div>`;
  }

  devices(data) {
    const selected = this.selected();
    const devices = data.secrets.filter((s) => !isAi(s));
    const rows = this.rows(data);
    return html`<div class="secrets-list">
      ${this.toolbar(devices)}
      ${this.state.creating ? html`<${SecretForm} catalogue=${data.catalogue} secondFactor=${data.second_factor}
        onSaved=${(saved) => { this.setState({ creating: false }); this.open(saved.id); this.refresh(); }}
        onCancel=${() => this.setState({ creating: false })} />` : null}
      ${rows.length ? html`<div class="table-scroll list-view"><table class="data secrets-table">
        <thead><tr>
          <th scope="col">Status</th><th scope="col">Credential</th>
          <th scope="col">Where</th><th scope="col">Grants</th><th scope="col">Agents</th>
          <th scope="col" class="wide-only">Last check</th>
        </tr></thead>
        <tbody>${rows.map((s) => html`<tr key=${s.id} class=${cx(selected === s.id && "row-selected")}>
          <td><${StatusLight} status=${s.status} /></td>
          <td><span class="secret-name"><${KindMark} kind=${s.kind} size=${28} />
            <span><button type="button" class="linkish secret-open" onClick=${() => this.open(s.id)}
              aria-expanded=${selected === s.id ? "true" : "false"}>${s.name}</button>
            <span class="muted secret-kind">${s.kind_label}</span></span></span></td>
          <td class="mono">${s.host}:${s.port}</td>
          <td><span class=${`access-pill access-${s.access_level}`}><${Icon}
            name=${(ACCESS[s.access_level] || ACCESS.view_only).icon} /> ${s.access_label.split(" — ")[0]}</span></td>
          <td>${s.agent_access ? html`<span class="badge badge-warn">on</span>` : html`<span class="muted">off</span>`}</td>
          <td class="wide-only" title=${timeTitle(s.status.last_checked_at)}>${s.status.last_checked_at
            ? relative(s.status.last_checked_at) : "—"}</td>
        </tr>`)}</tbody>
      </table></div>` : devices.length ? html`<p class="muted">No credential matches.</p>`
        : html`<${Empty} title="No device credentials yet"
          will="Store the first one with New credential: pick what it opens — SSH, SNMP, an API — and fill three short parts. Agents stay off until you switch them on, one credential at a time." />`}
    </div>`;
  }
}
