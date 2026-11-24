/* Brokers: the networks this appliance monitors, each on its own (ADR #494).
 *
 * A broker is one monitored network owned by one customer — Claro's North-East and Claro's South
 * are two. Each learns from its own traps alone: its correlation, its map, its timeline, its
 * elements and its models. So the screens that show a network live **inside** its broker, and the
 * sidebar's *Brokers* group names each one with them underneath:
 *
 *   * `#/brokers` — every broker (as Zabbix lists its proxies): how its traps arrive, whether they
 *     are arriving, how many elements and live situations it holds; an admin adds one here;
 *   * `#/brokers/{id}` — one broker: what it is, how to send it traps, and an admin's changes;
 *   * `#/brokers/{id}/{graph|timeline|entities|maintenance|learning}` — that network alone.
 *
 * The old addresses (`#/graph`, `#/timeline`, `#/entities`, `#/learning`, `#/promotion`) still
 * land. With more than one broker, inside the broker of the element or situation they name, else
 * the last broker opened; with one network (or none the session may list), on the screen as it
 * always was — one network has nothing to separate.
 */

import { html, Component, cx } from "../../dom.js";
import { get } from "../../api.js";
import { Loader, Empty, Loading } from "../../widgets.js";
import { Icon } from "../../icons.js";
import { can } from "../../session.js";
import { count, plural, relative, timeTitle } from "../../format.js";
import { INTAKE, PARTS, brokerById, brokerIcon, brokerList, lastBroker, partHref, partsFor, refreshBrokers }
  from "../../brokers.js";
import { BrokerWizard } from "./parts/brokerwizard.js";
import { BrokerHome, BrokerStatus } from "./parts/brokerhome.js";
import { GraphView } from "./graph.js";
import { Timeline } from "./timeline.js";
import { Entities } from "./entities.js";
import { Maintenance } from "./maintenance.js";
import { BrokerLearning } from "./parts/brokerlearn.js";

export class Brokers extends Component {
  render({ params = [], query, navigate }) {
    const [id, part] = params;
    if (!Number(id)) return html`<${BrokerList} navigate=${navigate} />`;
    if (!part) return html`<${BrokerHome} key=${id} id=${Number(id)} navigate=${navigate} />`;
    return html`<${BrokerPart} key=${`${id}-${part}`} id=${Number(id)} part=${part}
      query=${query} navigate=${navigate} />`;
  }
}

/** The heading over a broker's screens: its name, and which screen. */
export function brokerHeading(params = []) {
  const [id, part] = params;
  if (!Number(id)) return null;
  const broker = brokerById(id);
  const name = broker ? broker.name : `Broker ${id}`;
  const found = PARTS.find(([key]) => key === part);
  return {
    title: found ? `${name} · ${found[1]}` : name,
    summary: broker ? `${broker.customer} · ${INTAKE[broker.intake][0]}` : null,
  };
}

function arrival(b) {
  if (b.intake === "port") return html`UDP <b class="mono">${b.udp_port}</b>${b.networks && b.networks.length ? ` + ${b.networks.length} ranges` : ""}`;
  if (b.intake === "proxy") {
    return b.proxy_seen_at ? html`proxy, last batch <span title=${timeTitle(b.proxy_seen_at)}>${relative(b.proxy_seen_at)}</span>`
      : "proxy, not yet connected";
  }
  return b.is_default ? "every unclaimed trap" : plural((b.networks || []).length, "range");
}

class BrokerList extends Loader {
  constructor(props) {
    super(props);
    this.what = "the brokers";
    this.loadingLabel = "Reading the brokers";
    this.state = { ...this.state, adding: false };
  }

  async load() { return get("/api/brokers"); }

  routeKey() { return "brokers"; }

  view(data) {
    const brokers = data.brokers || [];
    if (this.state.adding) {
      return html`<${BrokerWizard} onCancel=${() => this.setState({ adding: false }, () => this.reload())}
        onCreated=${() => refreshBrokers()} />`;
    }
    return html`<div class="brokers">
      <div class="brk-toolbar">
        <p class="muted">${plural(brokers.length, "broker")} · ${plural(new Set(brokers.map((b) => b.organization_id)).size, "customer")}</p>
        ${can("brokers.write") ? html`<button type="button" class="primary" data-role="add-broker"
          onClick=${() => this.setState({ adding: true })}><${Icon} name="network" /> Add a broker</button>` : null}
      </div>
      ${brokers.length ? html`<div class="table-scroll"><table class="data brk-table">
        <thead><tr><th scope="col">Broker</th><th scope="col">Customer</th><th scope="col">Traps arrive</th>
          <th scope="col" class="num">Elements</th><th scope="col" class="num">Live situations</th>
          <th scope="col" class="wide-only">Last trap</th><th scope="col">State</th></tr></thead>
        <tbody>${brokers.map((b) => html`<tr key=${b.id} class=${cx(!b.enabled && "row-muted")}>
          <td><a class="brk-name" href=${partHref(b.id)}><span class="record-icon"><${Icon}
            name=${brokerIcon(b)} /></span><b>${b.name}</b></a>
            ${b.is_default ? html`<span class="badge">default</span>` : null}</td>
          <td>${b.customer}</td>
          <td><span class="brk-intake">${INTAKE[b.intake][0]}</span><br /><small class="muted">${arrival(b)}</small></td>
          <td class="num">${b.elements == null ? "—" : count(b.elements)}</td>
          <td class="num">${b.live == null ? "—" : b.live ? html`<a href=${`#/situations?broker=${b.id}`}>${count(b.live)}</a>` : "0"}</td>
          <td class="wide-only">${b.last_trap ? html`<span title=${timeTitle(b.last_trap)}>${relative(b.last_trap)}</span>` : html`<span class="muted">never</span>`}</td>
          <td><${BrokerStatus} status=${b.status} /></td>
        </tr>`)}</tbody></table></div>`
        : html`<${Empty} title="No broker you can see"
            will="Each network this appliance monitors appears here." meanwhile="An admin adds one." />`}
    </div>`;
  }
}

/** One of a broker's five screens, on that network alone. */
function BrokerPart({ id, part, query, navigate }) {
  const allowed = partsFor().find(([key]) => key === part);
  if (!allowed) {
    return html`<${Empty} title="Not a screen you can open here"
      will="A broker's screens are its Network graph, Timeline, Entities, Maintenance and Learning." />`;
  }
  const base = partHref(id, part);
  const shared = { brokerId: id, base, query, navigate, params: [] };
  if (part === "graph") return html`<${GraphView} ...${shared} />`;
  if (part === "timeline") return html`<${Timeline} ...${shared} />`;
  if (part === "entities") return html`<${Entities} ...${shared} />`;
  if (part === "maintenance") return html`<${Maintenance} ...${shared} />`;
  return html`<${BrokerLearning} ...${shared} />`;
}

/* ---------- the old addresses ---------- */

/** Where an old address goes: the broker of what it names, else the last one opened. */
async function brokerFor(query) {
  const asked = query && Number(query.get("broker"));
  if (asked) return asked;
  try {
    const ne = query && query.get("ne");
    if (ne && /^\d+$/.test(ne)) return (await get(`/api/elements/${ne}`)).broker_id;
    const sid = query && query.get("sid");
    if (sid && /^\d+$/.test(sid)) return (await get(`/api/situations/${sid}`)).broker_id;
  } catch { /* fall through to the last broker opened */ }
  if (lastBroker()) return lastBroker();
  if (!brokerList()) await refreshBrokers();
  const first = (brokerList() || []).find((b) => b.live) || (brokerList() || [])[0];
  return first ? first.id : 1;
}

/** Whether this console separates networks: more than one broker the session may list. */
function separated() {
  if (!can("brokers.read")) return false;
  const list = brokerList();
  return list === null ? null : list.length > 1;
}

/** `#/graph` and its siblings. `Classic` is the screen as one network shows it; `preset` is the
 *  query the old address implied (`#/corpus` is Learning on its Corpus tab). */
export function brokerAlias(Classic, part, preset = {}) {
  return class BrokerAlias extends Component {
    constructor(props) {
      super(props);
      this.state = { split: separated() };
    }

    async componentDidMount() {
      let split = this.state.split;
      if (split === null) {
        await refreshBrokers();
        split = (brokerList() || []).length > 1;
        this.setState({ split });
      }
      if (split) this.redirect();
    }

    async redirect() {
      const id = await brokerFor(this.props.query);
      const q = new URLSearchParams(String(this.props.query ?? ""));
      q.delete("broker");
      for (const [key, value] of Object.entries(preset)) if (!q.get(key)) q.set(key, value);
      const href = partHref(id, part, q.toString() || undefined);
      // Replaced, not pushed: Back must not land on this address and be sent forward again.
      if (globalThis.location && globalThis.location.replace) globalThis.location.replace(href);
      else this.props.navigate(href);
    }

    render(props, { split }) {
      if (split === false) return html`<${Classic} ...${props} />`;
      return html`<${Loading} label="Opening the broker it belongs to" />`;
    }
  };
}
