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
import { get, post } from "../api.js";
import { Loading, Empty, Failed, Badge, SeverityCell, DataTable, cell } from "../widgets.js";
import { age, alarmName, deviceName, score, percent, plural, timeTitle } from "../format.js";
import { canEdit } from "../session.js";
import * as store from "../store.js";

export class Situations extends Component {
  constructor(props) {
    super(props);
    this.state = { live: store.get(), filter: "", status: "open" };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
    // A deep link (#/situations/12) opens that card, which is what makes a situation shareable
    // during an incident (draft §2.4, §3).
    const deepLink = Number(this.props.params[0]);
    if (deepLink) this.open(deepLink);
  }

  componentWillUnmount() { if (this.unsubscribe) this.unsubscribe(); }

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
        <label class="visually-hidden" for="fltStatus">Status</label>
        <select id="fltStatus" value=${status}
                onChange=${(e) => this.setState({ status: e.target.value })}>
          <option value="open">open</option>
          <option value="closed">closed</option>
          <option value="merged">merged</option>
          <option value="">any status</option>
        </select>
        <span class="filter-count">${plural(rows.length, "situation")}</span>
      </div>

      ${live.situations === null ? html`<${Loading} label="Reading situations" />` : null}
      ${rows.length === 0 ? html`<${Empty}
          title=${query || status !== "open"
            ? "No situations match this filter."
            : "The network is quiet."}
          will=${query || status !== "open"
            ? "Clearing the filter will show everything the appliance currently holds."
            : "Situations appear here the moment two or more alarms correlate. Nothing has to be " +
              "configured first: the appliance learns alarm classes, network elements and their " +
              "affinities from the trap stream itself."}
          meanwhile=${query || status !== "open"
            ? null
            : "Point your devices' trap destination at this appliance. The Alarm classes screen " +
              "fills in as soon as the first trap arrives, before any grouping happens."} />` : null}

      <div id="sits" class="cards">
        ${rows.map((s) => html`<${SituationCard} key=${s.id} situation=${s}
                                                 onToggle=${() => this.open(s.id)} />`)}
      </div>
    </div>`;
  }
}

function SituationCard({ situation, onToggle }) {
  const sid = situation.id;
  const expanded = store.isExpanded(sid);
  const withheld = store.withheldCount(sid);
  const detail = store.heldDetail(sid);

  return html`<article class=${cx("sit", expanded && "expanded")} data-sid=${sid}>
    <div class="sit-head">
      <button type="button" class="sit-toggle" aria-expanded=${expanded ? "true" : "false"}
              aria-controls=${`sit-detail-${sid}`} onClick=${onToggle}>
        <span class="sid">#${sid}</span>
        <${Badge} tone=${situation.status === "open" ? "alarm" : "quiet"}>${situation.status}<//>
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
      ${expanded ? html`<${Detail} sid=${sid} detail=${detail} withheld=${withheld} />` : null}
    </div>
  </article>`;
}

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
                  onMark=${(id, on) => this.toggleMark(id, on)} />

      <${WhyGrouped} links=${detail.links} byId=${byId} threshold=${detail.threshold} />

      ${editable ? html`<div class="fb">
        <button type="button" disabled=${this.state.sending}
                onClick=${() => this.verdict("confirm")}>✓ Confirm grouping</button>
        <button type="button" class="warn" disabled=${this.state.sending}
                onClick=${() => this.verdict("split")}>
          ${this.state.marked.size
            ? `✗ Split — ${plural(this.state.marked.size, "member")} marked as not belonging`
            : "✗ Split (wrong grouping)"}
        </button>
        ${detail.status === "open" ? html`<button type="button" disabled=${this.state.sending}
                onClick=${() => this.close()}>Close situation</button>` : null}
      </div>` : null}

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
};

function Members({ alarms, editable, marked, onMark }) {
  const columns = [
    ...(editable ? [{ key: "mark", label: "", title: "tick the members that do NOT belong" }] : []),
    { key: "device", label: "device" },
    { key: "class", label: "class" },
    { key: "instance", label: "instance" },
    { key: "severity", label: "severity" },
    { key: "count", label: "count", numeric: true },
    { key: "state", label: "state" },
  ];
  const rows = alarms.map((a, index) => ({
    key: a.id,
    cells: {
      // The accessible name is POSITIONAL, not the device and class names.
      //
      // Embedding them read better until the escaping invariant measured it: an operator label is
      // attacker-influenced text, and putting it in an attribute means a screen reader announces
      // whatever arrived in a trap. `setAttribute` makes it inert as markup, so this is not an
      // XSS — it is the same string reaching a second sink that the F1 discipline never covered,
      // and "inert" is not the same as "appropriate to read aloud". Position is also simply
      // clearer than a 200-character name.
      mark: html`<td><input type="checkbox" checked=${marked.has(a.id)}
        aria-label=${`Mark member ${index + 1} of ${alarms.length} as not belonging`}
        onChange=${(e) => onMark(a.id, e.target.checked)} /></td>`,
      device: deviceName(a),
      class: html`<span>${alarmName(a)}${a.is_flapping
        ? html`<span class="flap" title="This alarm is flapping"> ~flapping</span>` : null}</span>`,
      instance: a.instance || "—",
      severity: cell(html`<${SeverityCell} alarm=${a} />`),
      count: a.count,
      state: a.status,
    },
  }));
  // The mark column is a <td> already; hand it through verbatim.
  for (const row of rows) if (editable) row.cells.mark = cell(row.cells.mark);
  return html`<${DataTable} columns=${columns} rows=${rows}
    caption=${`${plural(alarms.length, "member alarm")} in this situation`} />`;
}

/**
 * **Why these alarms were grouped**, decomposed per link.
 *
 * Each row is one pair and its three named terms with the number each contributed. The bar is a
 * second encoding of the same numbers, never the only one — a bar alone cannot be read off a bad
 * monitor and cannot be copied into a ticket.
 *
 * The terms come from the scorer's own named list (`l.terms`), falling back to the legacy three
 * columns for an older response. Same numbers, one typed source (v0.6.0 §S2).
 */
function WhyGrouped({ links, byId, threshold }) {
  const shown = (links || []).slice(0, 30);
  if (!shown.length) {
    return html`<section class="why">
      <h3>Why these were grouped</h3>
      <p class="hint">This situation has one member, so there is no link to explain.</p>
    </section>`;
  }
  return html`<section class="why">
    <h3>Why these were grouped</h3>
    <p class="hint">Every pair below scored above the link threshold${
      threshold != null ? ` of ${score(threshold)}` : ""}. The score is the sum of three named
      terms, and each term's contribution is shown so the decision can be checked rather than
      trusted.</p>
    <ol class="links">
      ${shown.map((link, index) => html`<li class="linkrow" key=${index}>
        <span class="linkscore" title="the sum of the three terms below">${score(link.score)}</span>
        <${TermBar} link=${link} />
        <span class="linkpair">
          ${nameOf(byId, link.alarm_a)} <span aria-hidden="true">↔</span> ${nameOf(byId, link.alarm_b)}
        </span>
      </li>`)}
    </ol>
    ${links.length > shown.length
      ? html`<p class="hint">${links.length - shown.length} further link(s) are not shown.</p>`
      : null}
  </section>`;
}

function nameOf(byId, alarmId) {
  const alarm = byId.get(alarmId);
  return alarm ? alarmName(alarm) : `alarm ${alarmId}`;
}

const TERM_KEY = { temporal: "t", class_affinity: "a", entity_affinity: "e" };
const TERM_LABEL = {
  temporal: "t — how close in time",
  class_affinity: "A — how often these alarm classes co-occur",
  entity_affinity: "E — how often these devices co-occur",
};

function termsOf(link) {
  if (Array.isArray(link.terms) && link.terms.length) return link.terms;
  return [
    { name: "temporal", contribution: link.term_t },
    { name: "class_affinity", contribution: link.term_a },
    { name: "entity_affinity", contribution: link.term_e },
  ];
}

function TermBar({ link }) {
  const terms = termsOf(link);
  const title = terms.map((t) => `${TERM_LABEL[t.name] ?? t.name}: ${score(t.contribution)}`).join("\n");
  return html`<span class="terms" title=${title}>
    ${terms.map((t) => html`<span class="term" key=${t.name}>
      <span class=${cx("term-bar", `term-${TERM_KEY[t.name] ?? "t"}`)}
            style=${{ width: `${Math.max(2, Math.round(120 * (t.contribution / 0.95)))}px` }}
            aria-hidden="true"></span>
      <span class="term-num">${(TERM_KEY[t.name] ?? "?").toUpperCase()} ${score(t.contribution)}</span>
    </span>`)}
  </span>`;
}
