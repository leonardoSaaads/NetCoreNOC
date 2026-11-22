/* Choosing a broker: "All brokers", or one (ADR #496).
 *
 * Grafana puts a dashboard's variables at the top, each with an "All" choice, and the panels below
 * follow it; the Overview does the same with its broker, at the heading's right. The choice is in
 * the address (`?broker=`), so it survives a reload and can be shared, and the live stream follows
 * it (`brokers.brokerOfRoute`). Situations uses the same control as its filter.
 */

import { html, Component } from "../../dom.js";
import { Icon } from "../../icons.js";
import { brokerList, onBrokers, refreshBrokers } from "../../brokers.js";
import { can } from "../../session.js";

export class BrokerSelect extends Component {
  componentDidMount() {
    this.stop = onBrokers(() => this.forceUpdate());
    if (!brokerList()) refreshBrokers();
  }

  componentWillUnmount() { if (this.stop) this.stop(); }

  render({ value, onChange, label = "Broker" }) {
    if (!can("brokers.read")) return null;
    const brokers = brokerList() || [];
    if (brokers.length < 2 && !value) return null; // one network: nothing to choose
    return html`<label class="brk-pick" title="Which network these figures are of">
      <span class="brk-pick-label"><${Icon} name="network" /> ${label}</span>
      <select data-role="broker-pick" onChange=${(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="" selected=${!value}>All brokers</option>
        ${brokers.map((b) => html`<option key=${b.id} value=${b.id}
          selected=${String(b.id) === String(value)}>${b.name}${b.enabled ? "" : " (off)"}</option>`)}
      </select>
    </label>`;
  }
}

/** The Overview's control, drawn at its heading's right. */
export function OverviewBroker({ query, navigate }) {
  const value = Number(query && query.get("broker")) || null;
  return html`<${BrokerSelect} value=${value}
    onChange=${(id) => navigate(id ? `#/overview?broker=${id}` : "#/overview")} />`;
}
