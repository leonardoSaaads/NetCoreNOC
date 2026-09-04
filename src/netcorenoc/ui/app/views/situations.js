/* Situations — **the screen that justifies this release** (§IV.3).
 *
 * > The operator must be able to answer *"why did the system group these alarms?"* without
 * > leaving the screen.
 *
 * That is principle 2 and it is the product's whole claim. Every expanded card reaches the
 * per-term contributions that produced each link: the three named terms, their numbers, and the
 * threshold the sum had to clear. Not a score. The decomposition.
 *
 * ## The held card (ADR #173, v0.7.5 §5.1-§5.3)
 *
 * An expanded card is frozen on the payload the operator opened it with. Preact's diff already
 * guarantees the DOM node survives an update — the Split button an operator is aiming at is the
 * same object across re-renders — but node identity is not meaning identity: if the membership
 * changed underneath a set of ticked boxes, the marks would refer to alarms the operator never
 * saw. So the DATA is held, the marker says so, and collapsing releases the hold.
 *
 * ## The labelling payload contract, unchanged (draft §11.12, DECISIONS #127)
 *
 * `excluded_ids` asserts *marked-by-rest negative and nothing else*. **Omitting it means the
 * operator marked nothing** — a plain split — and never an empty list. A `confirm` never carries
 * exclusions, because a confirm asserts every pair positive and an exclusion would contradict it.
 * The gesture changed; the payload did not.
 */

import { html, Component, cx } from "../dom.js";
import { Icon } from "../icons.js";
import { WhyGrouped } from "./parts/why.js";
import { History, NameField, RESOLUTION_TEXT, Restructure } from "./parts/lifecycle.js";
import { get, post } from "../api.js";
import { Loading, Empty, Failed, Badge } from "../widgets.js";
import { age, alarmName, deviceName, percent, plural, timeTitle } from "../format.js";
import { Members } from "./parts/members.js";
import { canEdit } from "../session.js";
import * as store from "../store.js";

export class Situations extends Component {
  constructor(props) {
    super(props);
    // v0.16.0: `new` is what the correlator creates and what an untriaged appliance is full of,
    // so it is the state this screen opens on. `open` would have shown an empty list on a working
    // appliance the moment this release shipped (DECISIONS #254).
    this.state = { live: store.get(), filter: "", status: "new" };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
    // A deep link (#/situations/12) opens that card, which is what makes a situation shareable
    // during an incident (draft §2.4, §3).
    const deepLink = Number(this.props.params[0]);
    if (deepLink) this.open(deepLink);
  }

  componentWillUnmount() { if (this.unsubscribe) this.unsubscribe(); }

  /** Re-fetch a card the operator has just changed, and re-freeze the hold on the new payload.
   *
   * A gesture the operator made is the one case where the held card SHOULD move: they changed it,
   * so showing them what they changed it to is not a state change arriving underneath a click. The
   * hold's whole purpose is that an update they did not ask for cannot move the grouping they are
   * judging (ADR #173), and `refreshHeld` is the existing name for exactly this — "the card was
   * re-fetched deliberately (the operator asked)". */
  async reopen(sid) {
    try { store.refreshHeld(sid, await get(`/api/situations/${sid}`)); }
    catch (error) { store.refreshHeld(sid, { __error: error }); }
  }

  async open(sid) {
    if (store.isExpanded(sid)) { store.collapse(sid); return; }
    // Expand FIRST with no payload so the card shows its own loading state in place, then hold
    // the payload when it lands. v0.7.4 emptied the container before the round trip and a click
    // in that window hit nothing.
    store.expand(sid, null);
    try { store.refreshHeld(sid, await get(`/api/situations/${sid}`)); }
    catch (error) { store.refreshHeld(sid, { __error: error }); }
  }

  render(_props, { live, filter, status }) {
    const query = filter.trim().toLowerCase();
    const rows = (live.situations || []).filter((s) => {
      if (status && s.status !== status) return false;
      if (!query) return true;
      return `#${s.id} ${s.status}`.toLowerCase().includes(query);
    });

    return html`<div class="situations">
      <div class="filters" role="search">
        <label class="visually-hidden" for="fltText">Search situations</label>
        <input id="fltText" placeholder="search by id or status" value=${filter}
               onInput=${(e) => this.setState({ filter: e.target.value })} />
        <span class="filter-count">${plural(rows.length, "situation")}</span>
      </div>

      <div class="tabs" role="tablist" aria-label="Situation state">
        ${TABS.map(([value, label, hint]) => html`<button key=${value} type="button" role="tab"
            id=${`tab-${value || "any"}`}
            class=${cx("tab", status === value && "tab-on")}
            aria-selected=${status === value ? "true" : "false"}
            aria-controls="sits" title=${hint}
            onClick=${() => this.setState({ status: value })}>${label}</button>`)}
      </div>

      ${live.situations === null ? html`<${Loading} label="Reading situations" />` : null}
      ${rows.length === 0 ? html`<${Empty}
          title=${query || status !== "new"
            ? "No situations match this filter."
            : "The network is quiet."}
          will=${query || status !== "new"
            ? "Clearing the filter will show everything the appliance currently holds."
            : "Situations appear here the moment two or more alarms correlate. Nothing has to be " +
              "configured first: the appliance learns alarm classes, network elements and their " +
              "affinities from the trap stream itself."}
          meanwhile=${query || status !== "new"
            ? null
            : "Point your devices' trap destination at this appliance. The Alarm classes screen " +
              "fills in as soon as the first trap arrives, before any grouping happens."} />` : null}

      <div id="sits" class="cards" role="tabpanel"
           aria-labelledby=${`tab-${status || "any"}`}>
        ${rows.map((s) => html`<${SituationCard} key=${s.id} situation=${s}
                                                 onToggle=${() => this.open(s.id)}
                                                 onChanged=${() => this.reopen(s.id)} />`)}
      </div>
    </div>`;
  }
}

/* The three states the schema now has, and the fourth entry that is not a state.
 *
 * `new` leads because it is what the correlator creates and what an untriaged appliance is full of
 * (DECISIONS #254). The titles say what each state MEANS rather than repeating its name: "open" and
 * "new" are not self-explanatory to somebody who has just been handed the console. */
const TABS = [
  ["new", "New", "Formed by the correlator, and nobody has looked at it yet"],
  ["open", "Open", "An operator has touched it: judged, moved, merged, split or named it"],
  ["resolved", "Resolved", "It has left — and the card says why"],
  ["", "Any", "Every situation this appliance currently holds"],
];

function SituationCard({ situation, onToggle, onChanged }) {
  const sid = situation.id;
  const expanded = store.isExpanded(sid);
  const withheld = store.withheldCount(sid);
  const detail = store.heldDetail(sid);

  return html`<article class=${cx("sit", expanded && "expanded")} data-sid=${sid}>
    <div class="sit-head">
      <button type="button" class="sit-toggle" aria-expanded=${expanded ? "true" : "false"}
              aria-controls=${`sit-detail-${sid}`} onClick=${onToggle}>
        <span class=${expanded ? "sit-chevron open" : "sit-chevron"}>
          <${Icon} name="chevron" /></span>
        <span class="sid">#${sid}</span>
        ${situationName(situation)
          ? html`<span class=${cx("sit-name", situation.operator_name && "sit-name-operator")}
                       title=${NAME_TITLE[situation.operator_name ? "operator" : "derived"]}
                 >${situationName(situation)}</span>` : null}
        <${Badge} tone=${situation.status === "resolved" ? "quiet" : "alarm"}>${situation.status}<//>
        ${situation.status === "resolved" && situation.resolution
          ? html`<${Badge} tone="quiet" title=${RESOLUTION_TEXT[situation.resolution] ?? ""}
                 >${situation.resolution.replace("_", " ")}<//>` : null}
        <${Badge}>${plural(situation.alarm_count, "alarm")}<//>
        ${situation.redacted_count ? html`<${Badge} tone="redacted" title=${SCOPE_TITLE}>
          +${situation.redacted_count} outside your scope<//>` : null}
        ${expanded && withheld > 0 ? html`<${Badge} tone="held" title=${HELD_TITLE}>
          held while open<//>` : null}
        <span class="age" title=${timeTitle(situation.updated_at)}>${age(situation.updated_at)}</span>
      </button>
      <a class="permalink" href=${`#/situations/${sid}`}
         title="A link to this situation alone, shareable during the incident">link</a>
    </div>
    <div id=${`sit-detail-${sid}`} class="detail" hidden=${!expanded}>
      ${expanded ? html`<${Detail} sid=${sid} detail=${detail} withheld=${withheld}
                                  onChanged=${onChanged} />` : null}
    </div>
  </article>`;
}

/** An operator's own name if there is one, else the server's projection of the membership. */
export function situationName(situation) {
  return situation.operator_name || situation.derived_name || "";
}

const NAME_TITLE = {
  operator: "A name an operator gave this situation. The id above it is still the identity.",
  derived: "Derived from this situation's members and recomputed when they change. An operator " +
           "can override it, and no model proposes one.",
};

const HELD_TITLE =
  "This card is frozen while you have it open, so the grouping you are judging cannot change " +
  "under your click. It may not reflect the last few seconds. Collapse it to resume live updates.";
const SCOPE_TITLE =
  "Members of this situation are outside your visibility scope and are not shown. Scoping hides " +
  "them from you; it does not stop them correlating.";

class Detail extends Component {
  constructor(props) {
    super(props);
    this.state = { marked: new Set(), sending: false, outcome: null };
  }

  toggleMark(alarmId, on) {
    const marked = new Set(this.state.marked);
    if (on) marked.add(alarmId); else marked.delete(alarmId);
    this.setState({ marked });
  }

  async verdict(kind) {
    const { sid, detail } = this.props;
    if (this.state.sending) return;
    this.setState({ sending: true, outcome: null });
    // Captured from the payload THIS render used, never re-read at click time, so it reports
    // what the operator was looking at rather than what became true while they decided.
    const body = { verdict: kind, member_ids: detail.alarms.map((a) => a.id),
                   updated_at: detail.updated_at };
    // A `split` carries the marks; a `confirm` never does. Omitting the field means the operator
    // marked nothing, which is a plain split — never an empty list (DECISIONS #127).
    if (kind === "split" && this.state.marked.size) {
      body.excluded_ids = detail.alarms
        .map((a) => a.id).filter((id) => this.state.marked.has(id));
    }
    try {
      await post(`/api/situations/${sid}/feedback`, body);
      this.setState({ sending: false, outcome: { ok: true, kind } });
    } catch (error) {
      this.setState({ sending: false, outcome: { ok: false, error } });
    }
  }

  async clearAlarm(alarmId) {
    if (this.state.sending) return;
    this.setState({ sending: true, outcome: null });
    try {
      await post(`/api/alarms/${alarmId}/clear`, {});
      this.setState({ sending: false, outcome: { ok: true, kind: "clear" } });
      this.props.onChanged();
    } catch (error) {
      this.setState({ sending: false, outcome: { ok: false, error } });
    }
  }

  async close() {
    const { sid } = this.props;
    this.setState({ sending: true });
    try {
      await post(`/api/situations/${sid}/close`, {});
      this.setState({ sending: false, outcome: { ok: true, kind: "close" } });
    } catch (error) {
      this.setState({ sending: false, outcome: { ok: false, error } });
    }
  }

  render({ sid, detail, withheld }) {
    if (!detail) return html`<${Loading} label=${`Reading situation #${sid}`} />`;
    if (detail.__error) {
      return html`<${Failed} error=${detail.__error} what=${`situation #${sid}`} />`;
    }
    const byId = new Map(detail.alarms.map((a) => [a.id, a]));
    const root = byId.get(detail.root_alarm_id);
    const editable = canEdit();

    return html`<div class="detail-body">
      ${withheld > 0 ? html`<p class="held-note">${
        `Frozen while open — ${plural(withheld, "update")} withheld. Collapse to resume.`}</p>` : null}

      ${detail.status === "resolved" ? html`<p class="resolution-note">
        <${Icon} name="info" />${" Resolved: "}
        ${RESOLUTION_TEXT[detail.resolution] ?? "the appliance did not record why"}.
      </p>` : null}

      ${root ? html`<p class="root">${"Probable root: "}
        <b>${alarmName(root)}</b>${" on "}<b>${deviceName(root)}</b>
        ${detail.root_confidence != null
          ? html` <span class="muted">(confidence ${percent(detail.root_confidence)})</span>` : null}
      </p>` : null}

      ${detail.redacted_members ? html`<div class="warnbox">
        <b>${plural(detail.redacted_members.count, "member")} outside your visibility scope</b>
        ${detail.redacted_members.classes.length
          ? `  classes: ${detail.redacted_members.classes.join(", ")}` : ""}
        <p class="hint">Scoping hides these members from you; it does not stop them correlating.
          This situation is larger than what is shown here.</p>
      </div>` : null}

      <${Members} alarms=${detail.alarms} editable=${editable}
                  marked=${this.state.marked}
                  onMark=${(id, on) => this.toggleMark(id, on)}
                  onClear=${(id) => this.clearAlarm(id)} />

      <${WhyGrouped} links=${detail.links} byId=${byId} threshold=${detail.threshold} />

      ${editable ? html`<div class="fb">
        <button type="button" class="primary" disabled=${this.state.sending}
                onClick=${() => this.verdict("confirm")}>
          <${Icon} name="check" /> Confirm grouping
        </button>
        <button type="button" class="warn" disabled=${this.state.sending}
                onClick=${() => this.verdict("split")}>
          <${Icon} name="cross" />${" "}
          ${this.state.marked.size
            ? `Split — ${plural(this.state.marked.size, "member")} marked as not belonging`
            : "Split (wrong grouping)"}
        </button>
        ${detail.status !== "resolved" ? html`<button type="button" disabled=${this.state.sending}
                onClick=${() => this.close()}>Close situation</button>` : null}
      </div>` : null}

      ${editable && detail.status !== "resolved" ? html`<${Restructure}
          sid=${sid} marked=${this.state.marked} post=${post}
          onDone=${() => this.props.onChanged()} />` : null}

      ${editable ? html`<${NameField} sid=${sid} post=${post}
          operatorName=${detail.operator_name} derivedName=${detail.derived_name}
          onDone=${() => this.props.onChanged()} />` : null}

      ${detail.events && detail.events.length ? html`<${History} events=${detail.events} />` : null}

      ${this.state.outcome ? html`<p class=${this.state.outcome.ok ? "ok-note" : "err"} role="status">
        ${this.state.outcome.ok
          ? OUTCOME_TEXT[this.state.outcome.kind]
          : `Not recorded — ${this.state.outcome.error.detail || this.state.outcome.error.message}`}
      </p>` : null}
    </div>`;
  }
}

const OUTCOME_TEXT = {
  confirm: "Recorded: this grouping is correct. Every pair in it is now an asserted positive.",
  split: "Recorded: this grouping is wrong. Only the members you marked are asserted negative.",
  close: "Closed.",
  clear: "Cleared. That alarm was stale; this says nothing about the grouping and the correlator " +
         "learns nothing from it.",
};
