/* One broker's page: what it is, whether its traps arrive, and an admin's changes (ADR #494).
 *
 * The record page, as Zabbix shows a proxy: how its traps reach this server and whether they are
 * reaching it now (its port listening, or its proxy's last batch), what it holds, and the way into
 * its five screens. An admin changes its name, customer, port or ranges here, switches it off (its
 * map and situations stay readable), removes it while it has received nothing, and — for a proxy —
 * issues a new token, which stops the old one at once and is shown once.
 */

import { html, Component } from "../../../dom.js";
import { del, get, post } from "../../../api.js";
import { Loader } from "../../../widgets.js";
import { Icon } from "../../../icons.js";
import { can } from "../../../session.js";
import { count, relative, timeTitle } from "../../../format.js";
import { INTAKE, brokerIcon, partHref, partsFor, refreshBrokers } from "../../../brokers.js";
import { BrokerReady } from "./brokerwizard.js";

const STATUS = {
  listening: ["ok", "Listening"],
  ready: ["ok", "Ready"],
  receiving: ["ok", "Receiving"],
  waiting: ["warn", "Waiting for its proxy"],
  silent: ["warn", "Proxy silent"],
  off: ["muted", "Switched off"],
  "not-listening": ["warn", "No broker ports here"],
};

/** Whether its traps are arriving, as a dot and its words (never the colour alone). */
export function BrokerStatus({ status }) {
  const [tone, label] = STATUS[status] || ["bad", String(status || "").replace(/^error: /, "Port error: ")];
  return html`<span class=${`brk-status st-${tone}`}><span class="agent-dot" aria-hidden="true"></span>${label}</span>`;
}

export class BrokerHome extends Loader {
  constructor(props) {
    super(props);
    this.what = "this broker";
    this.loadingLabel = "Reading the broker";
    this.state = { ...this.state, editing: false, busy: null, error: null, issued: null };
  }

  async load() { return get(`/api/brokers/${this.props.id}`); }

  routeKey(props) { return `broker-${props.id}`; }

  async act(what, call) {
    this.setState({ busy: what, error: null });
    try {
      const out = await call();
      this.setState({ busy: null, editing: false, issued: what === "token" ? out : null });
      refreshBrokers();
      if (what === "delete") { this.props.navigate("#/brokers"); return; }
      this.reload();
    } catch (error) {
      this.setState({ busy: null, error });
    }
  }

  change(b, patch) {
    const body = {
      name: b.name, organization_id: b.organization_id, description: b.description || "",
      udp_port: b.udp_port, networks: b.networks || [], enabled: b.enabled, ...patch,
    };
    return this.act("save", () => post(`/api/brokers/${b.id}`, body));
  }

  view(b) {
    const { editing, busy, error, issued } = this.state;
    const admin = can("brokers.write");
    if (issued) {
      return html`<${BrokerReady} broker=${{ ...b, token: issued.token }} title="A new token for its proxy"
        onDone=${() => this.setState({ issued: null })} />`;
    }
    return html`<div class="brk-home">
      <section class="panel-block brk-card">
        <header class="brk-card-head">
          <span class="record-icon"><${Icon} name=${brokerIcon(b)} /></span>
          <div><p class="brk-card-line"><${BrokerStatus} status=${b.status} />
            ${b.is_default ? html`<span class="badge">default</span>` : null}</p>
            <p class="muted">${b.description || INTAKE[b.intake][1]}</p></div>
          ${admin && !editing ? html`<button type="button" onClick=${() => this.setState({ editing: true })}>
            <${Icon} name="settings" /> Change</button>` : null}
        </header>
        ${editing ? html`<${BrokerEdit} broker=${b} busy=${busy === "save"}
          onSave=${(patch) => this.change(b, patch)} onCancel=${() => this.setState({ editing: false })} />`
          : html`<dl class="brk-facts">
            <dt>NOC customer</dt><dd><a href=${`#/customers/${b.organization_id}`}>${b.customer}</a>
              <span class="muted"> · its situations are this customer's cases</span></dd>
            <dt>Traps arrive</dt><dd>${INTAKE[b.intake][0]} — <span class="muted">${INTAKE[b.intake][1]}</span></dd>
            ${b.intake === "port" ? html`<dt>Its port</dt><dd><code class="mono">${(globalThis.location && globalThis.location.hostname) || "this-server"}:${b.udp_port}</code>
              <span class="muted"> · UDP; its devices send their traps here</span></dd>` : null}
            ${(b.networks || []).length ? html`<dt>Ranges</dt><dd class="mono">${b.networks.join(", ")}</dd>` : null}
            ${b.intake === "proxy" ? html`<dt>Its proxy</dt><dd>${b.proxy_seen_at
              ? html`last batch <span title=${timeTitle(b.proxy_seen_at)}>${relative(b.proxy_seen_at)}</span>${b.proxy_address ? ` from ${b.proxy_address}` : ""}${b.proxy_version ? ` · v${b.proxy_version}` : ""} · ${count(b.proxy_seq)} traps forwarded`
              : "has not connected yet"}${b.proxy_token_hint ? html` · token …${b.proxy_token_hint}` : null}</dd>` : null}
            <dt>Elements</dt><dd>${count(b.elements ?? 0)}</dd>
            ${b.live != null ? html`<dt>Live situations</dt><dd><a href=${`#/situations?broker=${b.id}`}>${count(b.live)}</a></dd>` : null}
            ${b.last_trap ? html`<dt>Last trap</dt><dd title=${timeTitle(b.last_trap)}>${relative(b.last_trap)}</dd>` : null}
          </dl>`}
        ${error ? html`<p class="err" role="alert">${error.message || String(error)}</p>` : null}
      </section>
      <nav class="brk-tiles" aria-label=${`${b.name}'s screens`}>
        ${partsFor().map(([part, label, icon]) => html`<a key=${part} class="brk-tile" href=${partHref(b.id, part)}>
          <span class="brk-tile-icon"><${Icon} name=${icon} /></span><b>${label}</b>
          <span class="muted">${TILE[part]}</span></a>`)}
      </nav>
      ${admin && !b.is_default ? html`<section class="panel-block brk-admin">
        <h3>Administer</h3>
        <div class="brk-admin-row">
          <button type="button" disabled=${Boolean(busy)} onClick=${() => this.change(b, { enabled: !b.enabled })}>
            ${b.enabled ? "Switch off" : "Switch on"}</button>
          <span class="muted">${b.enabled ? OFF[b.intake] : "It takes its traps again."}</span>
        </div>
        ${b.intake === "proxy" ? html`<div class="brk-admin-row">
          <button type="button" disabled=${Boolean(busy)} onClick=${() => this.act("token", () => post(`/api/brokers/${b.id}/token`, {}))}>
            Issue a new token</button>
          <span class="muted">The old token stops at once; the proxy needs the new one.</span></div>` : null}
        <div class="brk-admin-row">
          <button type="button" class="danger" disabled=${Boolean(busy) || (b.elements ?? 0) > 0}
            onClick=${() => this.act("delete", () => del(`/api/brokers/${b.id}`))}>Remove</button>
          <span class="muted">${(b.elements ?? 0) > 0 ? "It has received traps: switch it off instead, so its history stays readable." : "Only while it has received nothing."}</span>
        </div>
      </section>` : null}
    </div>`;
  }
}

/** What switching a broker off does to its traps, by how they arrive. */
const OFF = {
  port: "Its port closes while it is off; its map and situations stay readable.",
  proxy: "Its proxy keeps the traps on its own disk until it is switched on again.",
  cidr: "Its ranges go to the Default broker while it is off; its history stays.",
};

const TILE = {
  graph: "Learned affinity between its elements",
  timeline: "Its raises and clears over time",
  entities: "Its elements and their components",
  maintenance: "Planned work on its elements",
  learning: "Its labels, its judge and its models",
};

class BrokerEdit extends Component {
  constructor(props) {
    super(props);
    const b = props.broker;
    this.state = { name: b.name, customer: b.organization_id, description: b.description || "",
      port: b.udp_port ? String(b.udp_port) : "", networks: (b.networks || []).join(", "), customers: [] };
  }

  async componentDidMount() {
    try { this.setState({ customers: (await get("/api/organizations")).organizations || [] }); }
    catch { /* the customer stays as it is */ }
  }

  submit(e) {
    e.preventDefault();
    const b = this.props.broker;
    const s = this.state;
    this.props.onSave({
      name: s.name.trim(), organization_id: Number(s.customer), description: s.description.trim(),
      udp_port: b.intake === "port" ? Number(s.port) : null,
      networks: b.intake === "proxy" || b.is_default ? [] : s.networks.split(/[\s,]+/).filter(Boolean),
    });
  }

  /* ADR #507: three groups an owner reads top to bottom — what the broker is called, which NOC
   * customer it belongs to (exactly one), and how its traps arrive — each field with its label above
   * it and one line saying what it does; the buttons under the last group, right-aligned. */
  render({ broker: b, busy, onCancel }, s) {
    const id = (f) => `brk-${b.id}-${f}`;
    return html`<form class="brk-form" onSubmit=${(e) => this.submit(e)}>
      <fieldset class="brk-group">
        <legend>Broker</legend>
        <div class="brk-fields">
          <div class="pe-field brk-wide brk-name"><label for=${id("name")}>Name</label>
            <input id=${id("name")} type="text" value=${s.name} maxlength="80" required
              onInput=${(e) => this.setState({ name: e.target.value })} />
            <small class="muted">How the sidebar, the situations and the cases name this network.</small></div>
          <div class="pe-field brk-wide"><label for=${id("desc")}>Description</label>
            <textarea id=${id("desc")} rows="2" maxlength="2000" value=${s.description}
              onInput=${(e) => this.setState({ description: e.target.value })}></textarea></div>
        </div>
      </fieldset>
      <fieldset class="brk-group">
        <legend>NOC customer</legend>
        <div class="brk-fields">
          <div class="pe-field"><label for=${id("cust")}>Customer</label>
            <select id=${id("cust")} onChange=${(e) => this.setState({ customer: Number(e.target.value) })}>
              ${s.customers.map((c) => html`<option key=${c.id} value=${c.id} selected=${c.id === s.customer}
                >${c.name}${c.is_default ? " (default)" : ""}</option>`)}
            </select>
            <small class="muted">Every broker belongs to exactly one customer: its situations become
              that customer's NOC cases, worked with its instructions, contacts and routines.</small></div>
          <p class="brk-aside"><a href="#/customers">Customers in the NOC →</a></p>
        </div>
      </fieldset>
      <fieldset class="brk-group">
        <legend>How its traps arrive</legend>
        <p class="brk-arrive"><b>${INTAKE[b.intake][0]}</b> <span class="muted">— ${INTAKE[b.intake][1].replace(/\.+$/, "")}.
          This is fixed: a network that moves to a proxy is a new broker.</span></p>
        ${b.intake === "port" ? html`<div class="pe-field brk-narrow"><label for=${id("port")}>UDP port</label>
          <input id=${id("port")} type="number" min="1024" max="65535" value=${s.port}
            onInput=${(e) => this.setState({ port: e.target.value })} /></div>` : null}
        ${b.intake !== "proxy" && !b.is_default ? html`<div class="pe-field"><label for=${id("nets")}>Ranges (CIDR)</label>
          <textarea id=${id("nets")} class="mono" rows="2" value=${s.networks}
            onInput=${(e) => this.setState({ networks: e.target.value })}></textarea>
          <small class="muted">Comma or space separated, such as 10.20.0.0/16, 192.0.2.0/24.</small></div>` : null}
        ${b.is_default ? html`<p class="muted brk-arrive">It takes every trap on the standard port that no
          other broker's ranges claim.</p>` : null}
      </fieldset>
      <div class="brk-nav">
        <button type="button" onClick=${onCancel}>Cancel</button>
        <button type="submit" class="primary" disabled=${busy || !s.name.trim()}>${busy ? "Saving…" : "Save changes"}</button>
      </div>
    </form>`;
  }
}
