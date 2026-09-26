/* The Timeline's filter bar (v0.23.0, #392): window, organization, element, trap, kind — and the
 * situation, when the timeline was opened from one.
 *
 * Every control writes one parameter into the address and the SQL applies it (`/api/activity/*`):
 * nothing here filters a row it was sent. The element list narrows to the organization chosen; the
 * trap control takes a name from the catalogue or any OID, and an OID means that branch (arc
 * boundaries, #384). Each active filter is also a chip that removes it, so what the list below is
 * narrowed by is always on screen and one click from undone.
 */

import { html, Component, cx } from "../../dom.js";
import { WINDOWS, KINDS } from "./marks.js";

const OID = /^\.?[0-9]+(\.[0-9]+)+$/;

export class FilterBar extends Component {
  constructor(props) {
    super(props);
    this.state = { trap: "" };
  }

  /** A trap typed or picked: a known name becomes its OID; an OID is taken as a branch. */
  submitTrap(event) {
    event.preventDefault();
    const text = this.state.trap.trim();
    if (!text) return;
    const hit = (this.props.traps || []).find((t) => t.name.toLowerCase() === text.toLowerCase());
    const oid = hit ? hit.oid : OID.test(text) ? text.replace(/^\./, "") : null;
    if (oid) {
      this.setState({ trap: "" });
      this.props.set("oid", oid);
    }
  }

  render({ config, elements, orgs, traps, situation, set, clear }, { trap }) {
    const inOrg = config.org
      ? elements.filter((e) => String(e.organization_id) === String(config.org))
      : elements;
    const name = (id) => (elements.find((e) => String(e.ne_id) === String(id)) || {}).device || `#${id}`;
    const org = (id) => (orgs.find((o) => String(o.id) === String(id)) || {}).name || `#${id}`;
    const trapName = (oid) => ((traps || []).find((t) => t.oid === oid) || {}).name || oid;
    const chips = [
      config.sid ? ["sid", `Situation #${config.sid}${situation ? ` · ${situation}` : ""}`] : null,
      config.org ? ["org", `Organization: ${org(config.org)}`] : null,
      config.ne ? ["ne", `Element: ${name(config.ne)}`] : null,
      config.oid ? ["oid", `Trap: ${trapName(config.oid)}`] : null,
      config.kind !== "both" ? ["kind", `Only ${config.kind}s`] : null,
    ].filter(Boolean);
    return html`<div class="tl-filters" role="group" aria-label="Timeline filters">
      <div class="tl-row">
        <div class="ranges" role="group" aria-label="Window">
          ${WINDOWS.map((w) => html`<button type="button" key=${w.label}
              class=${cx("range", w.seconds === config.win && !config.sid && "on")}
              aria-pressed=${w.seconds === config.win && !config.sid}
              onClick=${() => set("win", w.seconds)}>${w.label}</button>`)}
          ${config.sid ? html`<span class="range on" aria-current="true">whole situation</span>` : null}
        </div>
      </div>
      <div class="tl-row tl-selects">
        ${orgs.length > 1 ? html`<label class="tl-field"><span>Organization</span>
          <select value=${config.org} onChange=${(e) => set("org", e.target.value)}>
            <option value="">all</option>
            ${orgs.map((o) => html`<option key=${o.id} value=${String(o.id)}>${o.name}</option>`)}
          </select></label>` : null}
        <label class="tl-field"><span>Element</span>
          <select value=${config.ne} onChange=${(e) => set("ne", e.target.value)}>
            <option value="">${config.org ? "every element in it" : "every element"}</option>
            ${inOrg.map((e) => html`<option key=${e.ne_id} value=${String(e.ne_id)}>${e.device}</option>`)}
          </select></label>
        <form class="tl-field tl-trap" onSubmit=${(e) => this.submitTrap(e)}>
          <label for="tlTrap">Trap</label>
          <span class="tl-trap-row">
            <input id="tlTrap" type="search" list="tlTraps" autocomplete="off"
                   placeholder="name or OID (a branch)" value=${trap}
                   onInput=${(e) => this.setState({ trap: e.currentTarget.value })} />
            <button type="submit" class="tap">Apply</button>
          </span>
          <datalist id="tlTraps">${(traps || []).map((t) => html`<option key=${t.oid}
            value=${t.name}>${t.oid}</option>`)}</datalist>
        </form>
        <label class="tl-field"><span>Show</span>
          <select value=${config.kind} onChange=${(e) => set("kind", e.target.value)}>
            ${KINDS.map(([value, label]) => html`<option key=${value} value=${value}>${label}</option>`)}
          </select></label>
      </div>
      ${chips.length ? html`<div class="tl-row tl-chips" aria-label="Active filters">
        ${chips.map(([key, text]) => html`<button type="button" key=${key} class="chip"
            aria-label=${`Remove filter: ${text}`} onClick=${() => set(key, "")}>
          ${text}<span aria-hidden="true"> ×</span></button>`)}
        ${chips.length > 1 ? html`<button type="button" class="linkish" onClick=${clear}>Clear all</button>` : null}
      </div>` : null}
    </div>`;
  }
}
