/* Adding a broker: three steps — how its traps arrive, its network, review (ADR #494).
 *
 * Like adding a proxy in Zabbix, the first question is how the traps reach this server, because
 * it decides everything after it and cannot change later (a network that moves from its own port
 * to a remote proxy is a new broker: its elements' identity is (broker, address)):
 *
 *   * **Own port + ranges** (the default) — its own UDP port on this server; repeated addresses
 *     across brokers are fine there. Its ranges also claim sources on the standard port.
 *   * **Remote proxy** — a NetCoreNOC proxy in its network forwards over HTTPS with a token,
 *     shown once when the broker is added, with the command that starts the proxy.
 *   * **Ranges only** — the standard port, by source range. Addresses may not repeat.
 *
 * Every check is the server's (a port, a name or a range another broker holds); this form says
 * what the server answered beside the button.
 */

import { html, Component, cx } from "../../../dom.js";
import { get, post } from "../../../api.js";
import { Icon } from "../../../icons.js";
import { INTAKE, brokerList } from "../../../brokers.js";

const MODES = [
  ["port", "network", "Yes"],
  ["proxy", "globe", "Yes"],
  ["cidr", "router", "No"],
];
const STEPS = ["How its traps arrive", "Its network", "Review"];

/** The next port no broker holds, from 1162 up: what an operator would type anyway. */
function nextPort() {
  const taken = new Set((brokerList() || []).map((b) => b.udp_port).filter(Boolean));
  let port = 1162;
  while (taken.has(port)) port += 1;
  return port;
}

function ranges(text) {
  return String(text || "").split(/[\s,]+/).map((r) => r.trim()).filter(Boolean);
}

export class BrokerWizard extends Component {
  constructor(props) {
    super(props);
    this.state = { step: 0, intake: "port", name: "", customer: null, customers: [],
      description: "", port: String(nextPort()), networks: "", busy: false, error: null, made: null };
  }

  async componentDidMount() {
    try {
      const orgs = await get("/api/organizations");
      this.setState({ customers: orgs.organizations || [], customer: orgs.default_organization_id });
    } catch (error) {
      this.setState({ error });
    }
  }

  ready(step) {
    const s = this.state;
    if (step === 1) {
      if (!s.name.trim() || !s.customer) return false;
      if (s.intake === "port" && !(Number(s.port) >= 1024 && Number(s.port) <= 65535)) return false;
      if (s.intake === "cidr" && !ranges(s.networks).length) return false;
    }
    return true;
  }

  body() {
    const s = this.state;
    return {
      name: s.name.trim(),
      organization_id: Number(s.customer),
      description: s.description.trim(),
      intake: s.intake,
      udp_port: s.intake === "port" ? Number(s.port) : null,
      networks: s.intake === "proxy" ? [] : ranges(s.networks),
    };
  }

  async create() {
    this.setState({ busy: true, error: null });
    try {
      const made = await post("/api/brokers", this.body());
      this.setState({ busy: false, made });
      if (this.props.onCreated) this.props.onCreated(made);
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render({ onCancel }, s) {
    if (s.made) return html`<${BrokerReady} broker=${s.made} onDone=${onCancel} />`;
    return html`<section class="panel-block brk-wizard" aria-labelledby="brk-wizard-title">
      <header class="brk-wizard-head">
        <h3 id="brk-wizard-title">Add a broker</h3>
        <button type="button" class="btn-ghost" onClick=${onCancel}>Cancel</button>
      </header>
      <ol class="brk-steps">${STEPS.map((label, i) => html`<li key=${i}
        class=${cx(i === s.step && "on", i < s.step && "done")}><span>${i + 1}</span>${label}</li>`)}</ol>
      ${[this.mode, this.network, this.review][s.step].call(this)}
      ${s.error ? html`<p class="err" role="alert">${s.error.message || String(s.error)}</p>` : null}
      <div class="brk-nav">
        ${s.step > 0 ? html`<button type="button" onClick=${() => this.setState({ step: s.step - 1 })}>Back</button>` : null}
        ${s.step < 2
          ? html`<button type="button" class="primary" disabled=${!this.ready(s.step)}
              onClick=${() => this.setState({ step: s.step + 1, error: null })}>Next</button>`
          : html`<button type="button" class="primary" disabled=${s.busy}
              onClick=${() => this.create()}>${s.busy ? "Adding…" : "Add the broker"}</button>`}
      </div>
    </section>`;
  }

  mode() {
    return html`<div class="brk-modes" role="radiogroup" aria-label="How its traps arrive">
      ${MODES.map(([key, icon, repeat]) => {
        const [title, line] = INTAKE[key];
        const on = this.state.intake === key;
        return html`<button type="button" key=${key} role="radio" aria-checked=${on}
          class=${cx("brk-mode", on && "on")} onClick=${() => this.setState({ intake: key })}>
          <span class="brk-mode-icon"><${Icon} name=${icon} /></span>
          <b>${title}${key === "port" ? html` <span class="badge">default</span>` : null}</b>
          <span class="muted">${line}</span>
          <span class="brk-mode-fact">Repeated IPs across brokers: <b>${repeat}</b></span>
        </button>`;
      })}
    </div>`;
  }

  network() {
    const s = this.state;
    return html`<div class="grid-form brk-form">
      <label>Name <input type="text" value=${s.name} maxlength="80" placeholder="Claro Nordeste"
        onInput=${(e) => this.setState({ name: e.target.value })} /></label>
      <label>Customer <select onChange=${(e) => this.setState({ customer: Number(e.target.value) })}>
        ${s.customers.map((c) => html`<option key=${c.id} value=${c.id}
          selected=${c.id === s.customer}>${c.name}</option>`)}</select></label>
      ${s.intake === "port" ? html`<label>UDP port on this server
        <input type="number" min="1024" max="65535" value=${s.port}
          onInput=${(e) => this.setState({ port: e.target.value })} />
        <span class="hint">Its devices send traps here instead of to the standard port.</span></label>` : null}
      ${s.intake !== "proxy" ? html`<label class="brk-wide">${s.intake === "cidr" ? "Ranges (CIDR)" : "Ranges on the standard port (optional)"}
        <textarea rows="3" placeholder="10.20.0.0/16, 172.16.8.0/22" value=${s.networks}
          onInput=${(e) => this.setState({ networks: e.target.value })}></textarea>
        <span class="hint">The longest prefix wins, as in routing. The same range in two brokers is refused.</span></label>`
        : html`<p class="hint brk-wide">The proxy's token and the command to start it are shown once, after the broker is added.</p>`}
      <label class="brk-wide">Description (optional)
        <input type="text" value=${s.description} maxlength="2000"
          onInput=${(e) => this.setState({ description: e.target.value })} /></label>
    </div>`;
  }

  review() {
    const b = this.body();
    const customer = this.state.customers.find((c) => c.id === b.organization_id);
    return html`<dl class="brk-facts">
      <dt>Name</dt><dd>${b.name}</dd>
      <dt>Customer</dt><dd>${customer ? customer.name : b.organization_id}</dd>
      <dt>Traps arrive</dt><dd>${INTAKE[b.intake][0]}${b.udp_port ? ` — UDP ${b.udp_port}` : ""}</dd>
      ${b.networks.length ? html`<dt>Ranges</dt><dd class="mono">${b.networks.join(", ")}</dd>` : null}
      <dt>Learns</dt><dd>Its own correlation, map and models, from its own traps only.</dd>
    </dl>`;
  }
}

/** After adding: where to send the traps — and, for a proxy, its token, shown this once. */
export function BrokerReady({ broker, onDone, title }) {
  const here = globalThis.location || {};
  const host = here.hostname || "this-server";
  const origin = here.origin || "https://this-server";
  return html`<section class="panel-block brk-ready" aria-labelledby="brk-ready-title">
    <h3 id="brk-ready-title"><${Icon} name="check" /> ${title || `${broker.name} is added`}</h3>
    ${broker.intake === "port" ? html`<p>Point its devices' traps at <code class="mono">${host}:${broker.udp_port}</code>.
      It is listening within seconds; its page says so.</p>` : null}
    ${broker.intake === "cidr" ? html`<p>Traps from its ranges on the standard port are its own from now on.</p>` : null}
    ${broker.token ? html`
      <p><b>The proxy's token, shown once.</b> This appliance keeps only its hash: lost, it is replaced,
        never shown again. Save it on the proxy's host in a file only its user can read.</p>
      <pre class="key-value mono">${broker.token}</pre>
      <${CopyText} text=${broker.token} label="Copy the token" />
      <p>On a host in its network (Python 3.12 and NetCoreNOC installed):</p>
      <pre class="mono brk-cmd">install -m 600 /dev/null /etc/netcorenoc/proxy.token   # then paste the token in it
python -m netcorenoc proxy --server ${origin} \\
  --token-file /etc/netcorenoc/proxy.token --listen 0.0.0.0:162 --spool /var/lib/netcorenoc/proxy</pre>
      <p class="hint">It keeps every trap on its own disk while the link is down, and sends them when it returns.</p>` : null}
    <button type="button" class="primary" onClick=${onDone}>Done</button>
  </section>`;
}

class CopyText extends Component {
  async copy() {
    try {
      await globalThis.navigator.clipboard.writeText(this.props.text);
      this.setState({ said: "Copied." });
    } catch {
      this.setState({ said: "Select it and copy it by hand." });
    }
  }

  render({ label }, { said }) {
    return html`<span class="copy-row"><button type="button" onClick=${() => this.copy()}>${label}</button>
      ${said ? html`<span class="muted" role="status">${said}</span>` : null}</span>`;
  }
}
