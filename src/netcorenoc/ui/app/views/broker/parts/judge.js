/* The judgement surface: everything inside an expanded situation card.
 *
 * **Split out of `views/broker/parts/card.js` in v0.16.4**, on the seam that file has had since it was
 * itself split out of `views/broker/situations.js` one release earlier. Its own header states the seam:
 * *"that module finds a situation and this one judges it"* — and inside it, the head line **is**
 * the finding (an id, a name, its badges, an age, a permalink, a toggle) while everything below
 * the fold is the judging. The two are read at different moments by an operator scanning a list
 * versus one working a single incident, and they have no state in common.
 *
 * It moved for the reason `card.js` moved: it went over budget. The module-graph guard's ceiling
 * is a third of the 52 738-byte file v0.12.0 replaced, and this release's action surface took
 * `card.js` past it — *"the honest repair to a file that is over budget is not to write less prose
 * in it, it is to notice that it had been two things for a while"* (DECISIONS #239's seam, applied
 * a third time).
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
 *
 * ## What an operator may do, and when (ADR #491)
 *
 * The status decides it. **New**: Confirm grouping, merge, move members. **Pending**: Accept or
 * Reject the proposal above the bar — a Confirm there used to open a second Open situation beside
 * the one the alarms belonged to. **Open**: Start working this, merge, move — no Confirm, it is
 * confirmed. **Resolved**: nothing but the record. The server answers 409 to a restructure on a
 * resolved situation, so the console offers exactly what will be accepted.
 */

import { html, Component } from "../../../dom.js";
import { Icon } from "../../../icons.js";
import { WhyGrouped } from "./why.js";
import { History, NameField, RESOLUTION_TEXT } from "./lifecycle.js";
import { Restructure } from "./restructure.js";
import { post } from "../../../api.js";
import { Loading, Failed } from "../../../widgets.js";
import { alarmName, deviceName, percent, plural } from "../../../format.js";
import { Members } from "./members.js";
import { Decide } from "./decide.js";
import { can, canEdit } from "../../../session.js";

export class Detail extends Component {
  constructor(props) {
    super(props);
    this.state = {
      marked: new Set(),
      sending: false,
      outcome: null,
      confirmingClear: false,
    };
  }

  toggleMark(alarmId, on) {
    // From the PENDING state, not `this.state`: two ticks in one frame would otherwise both start
    // from the same set and the second would drop the first.
    this.setState((state) => {
      const marked = new Set(state.marked);
      if (on) marked.add(alarmId); else marked.delete(alarmId);
      return { marked };
    });
  }

  /* Mark or unmark every member at once.
   *
   * Measured: one corpus situation holds **1 051** members, and a partial split over it is not a
   * gesture anybody completes one checkbox at a time. The set is built from the payload THIS
   * render used, for the same reason `verdict` captures its `member_ids` there — an update that
   * arrived mid-gesture must not silently widen what the operator marked. */
  markAll(on) {
    this.setState({ marked: on ? new Set(this.props.detail.alarms.map((a) => a.id)) : new Set() });
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

  /** The "outlived a window" marker has been seen (#388). The alarm stays active. */
  async ackOutlived(alarmId) {
    try {
      await post(`/api/alarms/${alarmId}/outlived/ack`, {});
      this.props.onChanged();
    } catch (error) {
      this.setState({ outcome: { ok: false, error } });
    }
  }

  /**
   * Clear every active member at once, or only the marked ones.
   *
   * One request. The alternative — looping the single-clear route in the client — is 1 051 requests
   * on the largest situation in the corpus, and a failure halfway through leaves a state no screen
   * describes. The server derives the set from `situation_id`; `only_ids` can narrow it and cannot
   * widen it, so this never asks for an alarm the operator was not already shown.
   */
  async clearMany(onlyMarked) {
    const { sid, detail } = this.props;
    if (this.state.sending) return;
    this.setState({ sending: true, outcome: null, confirmingClear: false });
    const body = { situation_id: sid };
    if (onlyMarked) {
      body.only_ids = detail.alarms
        .filter((a) => a.status === "active" && this.state.marked.has(a.id))
        .map((a) => a.id);
    }
    try {
      const out = await post("/api/alarms/clear", body);
      this.setState({
        sending: false,
        marked: new Set(),
        outcome: { ok: true, kind: "clear_all", count: out?.cleared ?? 0 },
      });
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
      // ADR #480: the button opens the NOC case of an open situation (its own route); the bare
      // promote of a new situation stays an API act for programs.
      await post(`/api/noc/cases/${sid}/open`, {});
      this.setState({ sending: false, outcome: { ok: true, kind: "promote" } });
      this.props.onChanged();
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
    // What the bulk clear can act on: active members, and how many of those are marked. Counted
    // from the rows this principal was served, which is the same set the server derives — a
    // scoped viewer's hidden members are absent from `detail.alarms` and so from both numbers.
    const active = detail.alarms.filter((a) => a.status === "active");
    const activeCount = active.length;
    const markedActive = active.filter((a) => this.state.marked.has(a.id)).length;
    // **What the bar offers is the status's** (ADR #491). Open means somebody confirmed the
    // grouping, so an Open card offers no Confirm — and no "judged" fold, which kept offering it
    // after a merge. New is a grouping to confirm, merge or correct; Pending is a proposal answered
    // by Accept or Reject above the bar; Open is worked and may still take a merge or a move; a
    // resolved one is over.
    const restructurable = detail.status === "new" || detail.status === "open";

    return html`<div class="detail-body">
      ${/* v0.16.4: the `Frozen while open — 60 updates withheld. Collapse to resume.` paragraph
            is gone. A count that climbs while an operator reads reads as an alarm about the
            appliance rather than as the courtesy it is, and the head line already carries the
            fact as a symbol with the number in its title. A paragraph explaining a control is the
            control having failed. */ null}

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

      ${/* **One bar, above the table** (v0.20.0, DECISIONS #358's sibling). What may be
            pressed and in what words is `views/broker/parts/decide.js`; this file owns when a press
            sends, what it sends and what the server answered. */ null}
      ${editable ? html`<${Decide}
        status=${detail.status} sending=${this.state.sending}
        restructure=${restructurable ? html`<${Restructure}
          sid=${sid} status=${detail.status} marked=${this.state.marked} post=${post}
          total=${detail.alarms.length + (detail.redacted_members?.count ?? 0)}
          onDone=${() => this.props.onChanged()} />` : null}
        activeCount=${activeCount} markedActive=${markedActive}
        confirmingClear=${this.state.confirmingClear}
        kase=${detail.case ? { ...detail.case, sid } : null} canWork=${can("cases.work")}
        onPromote=${() => this.promote()}
        onVerdict=${(kind) => this.verdict(kind)}
        onAskClear=${(on) => this.setState({ confirmingClear: on })}
        onClearMany=${() => this.clearMany(markedActive > 0)} />` : null}

      <${Members} alarms=${detail.alarms} editable=${editable}
                  marked=${this.state.marked}
                  onMark=${(id, on) => this.toggleMark(id, on)}
                  onMarkAll=${(on) => this.markAll(on)}
                  onClear=${(id) => this.clearAlarm(id)}
                  onAck=${(id) => this.ackOutlived(id)}
                  onDeclared=${() => this.props.onChanged()} />

      <${WhyGrouped} links=${detail.links} byId=${byId} threshold=${detail.threshold}
        table=${detail.link_terms} scale=${detail.score_scale} />

      ${editable ? html`<${NameField} sid=${sid} post=${post}
          operatorName=${detail.operator_name} derivedName=${detail.derived_name}
          onDone=${() => this.props.onChanged()} />` : null}

      ${detail.events && detail.events.length ? html`<${History} events=${detail.events} />` : null}

      ${this.state.outcome ? html`<p class=${this.state.outcome.ok ? "ok-note" : "err"} role="status">
        ${this.state.outcome.ok
          ? this.state.outcome.kind === "clear_all"
            // The server's count and not the client's, because they can differ honestly: a member
            // that self-cleared between the render and the press was already inactive, and saying
            // "12 cleared" when the appliance cleared 11 would make the console the less reliable
            // of the two records.
            ? `${plural(this.state.outcome.count, "alarm")} hand-cleared.`
            : OUTCOME_TEXT[this.state.outcome.kind]
          : `Not recorded — ${this.state.outcome.error.detail || this.state.outcome.error.message}`}
      </p>` : null}
    </div>`;
  }
}

const OUTCOME_TEXT = {
  promote: "Its case is in the NOC queue. Nothing was recorded about whether the grouping is right.",
  confirm: "Recorded: this grouping is correct. Every pair in it is now an asserted positive.",
  split: "Recorded: this grouping is wrong. Only the members you marked are asserted negative.",
  close: "Closed.",
  clear: "Cleared. That alarm was stale; this says nothing about the grouping and the correlator " +
         "learns nothing from it.",
};
