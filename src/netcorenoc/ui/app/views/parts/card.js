/* One situation's card: its head line, and the detail an operator judges it from.
 *
 * **Split out of `views/situations.js` in v0.16.1**, on the seam the two halves already had: that
 * module finds a situation and this one judges it. The screen owns the list, the tabs and the
 * search; everything below owns a single grouping — the head line's badges, the held payload, the
 * five gestures, the name field and the history.
 *
 * It moved because it had to and it moved where it belonged. `situations.js` reached 411 lines and
 * 20 224 bytes when the server-side search landed, over both the 400-line rule and the
 * module-graph guard's "no module is the old file renamed" ceiling — and the honest repair to a
 * file that is over budget is not to write less prose in it, it is to notice that it had been two
 * things for a while. `views/parts/members.js` was split out of the same file for the same reason
 * one release earlier (DECISIONS #239's seam, applied again).
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
 */

import { html, Component, cx } from "../../dom.js";
import { Icon } from "../../icons.js";
import { WhyGrouped } from "./why.js";
import { History, NameField, RESOLUTION_TEXT, Restructure } from "./lifecycle.js";
import { post } from "../../api.js";
import { Loading, Failed, Badge } from "../../widgets.js";
import { age, alarmName, deviceName, percent, plural, timeTitle } from "../../format.js";
import { Members } from "./members.js";
import { canEdit } from "../../session.js";
import * as store from "../../store.js";

export function SituationCard({ situation, onToggle, onChanged }) {
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

  async promote() {
    const { sid } = this.props;
    if (this.state.sending) return;
    this.setState({ sending: true, outcome: null });
    try {
      await post(`/api/situations/${sid}/promote`, {});
      this.setState({ sending: false, outcome: { ok: true, kind: "promote" } });
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

      ${editable && detail.status === "new" ? html`<div class="fb">
        <button type="button" disabled=${this.state.sending} title=${PROMOTE_TITLE}
                onClick=${() => this.promote()}>
          <${Icon} name="chevron" /> Start working this
        </button>
      </div>` : null}

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

const PROMOTE_TITLE =
  "Moves this to Open so the shift can see somebody has it. It records nothing about whether " +
  "the grouping is right — Confirm is how you say that.";

const OUTCOME_TEXT = {
  promote: "Open, and yours. Nothing was recorded about whether the grouping is right.",
  confirm: "Recorded: this grouping is correct. Every pair in it is now an asserted positive.",
  split: "Recorded: this grouping is wrong. Only the members you marked are asserted negative.",
  close: "Closed.",
  clear: "Cleared. That alarm was stale; this says nothing about the grouping and the correlator " +
         "learns nothing from it.",
};
