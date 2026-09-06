/* The judgement surface: everything inside an expanded situation card.
 *
 * **Split out of `views/parts/card.js` in v0.16.4**, on the seam that file has had since it was
 * itself split out of `views/situations.js` one release earlier. Its own header states the seam:
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
 * ## What an operator may do, and when (v0.16.4, DECISIONS #291)
 *
 * The surface turns on two facts and neither of them is the status alone:
 *
 *   * **`judged`** — is an `ASSERTING_KINDS` gesture already on record? `open` means *"an operator
 *     is working it"* (#254) and is reached by a bare promote or a rename, neither of which says
 *     anything about the grouping. A judged situation folds its grouping controls behind one
 *     disclosure that names what was recorded; **nothing is removed**, which is directive 2.
 *   * **`restructurable`** — the server answers **409** to move, merge and operator-split on a
 *     resolved situation (*"that situation has resolved; reload the card"*) and **200** to a
 *     verdict there. Measured, both, with a live situation as the control. The console offers
 *     exactly what will be accepted rather than inventing a second rule.
 */

import { html, Component } from "../../dom.js";
import { Icon } from "../../icons.js";
import { WhyGrouped } from "./why.js";
import { History, NameField, RESOLUTION_TEXT, Restructure } from "./lifecycle.js";
import { post } from "../../api.js";
import { Loading, Failed } from "../../widgets.js";
import { age, alarmName, deviceName, lastJudgement, percent, plural, timeTitle }
  from "../../format.js";
import { Members } from "./members.js";
import { canEdit } from "../../session.js";

export class Detail extends Component {
  constructor(props) {
    super(props);
    // `adjusting` is the disclosure a JUDGED situation puts its grouping controls behind. It is
    // per-visit component state and nothing else — a card that remembered it would be a second,
    // private notion of situation state, which is what DECISIONS #267 refused to build.
    this.state = { marked: new Set(), sending: false, outcome: null, adjusting: false };
  }

  toggleMark(alarmId, on) {
    const marked = new Set(this.state.marked);
    if (on) marked.add(alarmId); else marked.delete(alarmId);
    this.setState({ marked });
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
    // **The two facts the action surface turns on** (v0.16.4, DECISIONS #291).
    //
    // `judged` is whether an ASSERTING gesture is already on record, and it is not the same
    // question as the status: `open` means "an operator is working it" (#254) and is reached by a
    // bare promote or a rename, neither of which says anything about the grouping. Keying the
    // surface on the status would hide Confirm from the ordinary path — promote, investigate,
    // then judge — which is the path an operator actually walks.
    //
    // `restructurable` agrees with the server rather than inventing a second rule. Measured: the
    // three restructuring routes answer **409** on a resolved situation (*"that situation has
    // resolved; reload the card"*), because reopening is a decision nobody has made; the two
    // verdict routes answer **200** there and leave the status alone, because a label about a
    // frozen bag is still evidence. So the console offers exactly what will be accepted.
    const judged = lastJudgement(detail.events);
    const restructurable = detail.status !== "resolved";
    // A judged situation folds its grouping controls behind one disclosure. Nothing is removed —
    // directive 2 — and an unjudged one is unchanged, which is where an operator does this work.
    const grouping = !judged || this.state.adjusting;

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

      <${Members} alarms=${detail.alarms} editable=${editable}
                  marked=${this.state.marked}
                  onMark=${(id, on) => this.toggleMark(id, on)}
                  onMarkAll=${(on) => this.markAll(on)}
                  onClear=${(id) => this.clearAlarm(id)}
                  onDeclared=${() => this.props.onChanged()} />

      <${WhyGrouped} links=${detail.links} byId=${byId} threshold=${detail.threshold} />

      ${editable && detail.status === "new" ? html`<div class="fb">
        <button type="button" disabled=${this.state.sending} title=${PROMOTE_TITLE}
                onClick=${() => this.promote()}>
          <${Icon} name="chevron" /> Start working this
        </button>
      </div>` : null}

      ${editable && judged && !this.state.adjusting ? html`<div class="judged">
        <p class="judged-note">
          <${Icon} name="check" />${" "}
          <b>${JUDGED_TEXT[judged.kind] ?? "This grouping has been judged"}</b>
          ${judged.actor ? ` by ${judged.actor_name || judged.actor}` : ""}${" "}
          <span class="age" title=${timeTitle(judged.at)}>${age(judged.at)} ago</span>
        </p>
        <button type="button" onClick=${() => this.setState({ adjusting: true })}
                title=${ADJUST_TITLE}>Adjust the grouping</button>
      </div>` : null}

      ${editable && grouping ? html`<div class="fb">
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
        ${restructurable ? html`<button type="button" disabled=${this.state.sending}
                onClick=${() => this.close()}>Close situation</button>` : null}
      </div>` : null}

      ${editable && grouping && restructurable ? html`<${Restructure}
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

/* What is already on record, in the words of the gesture that recorded it.
 *
 * Keyed on `ASSERTING_KINDS` and nothing wider: `rename`, `manual_clear` and the three closes all
 * promote a situation and none of them judges its grouping, so none of them belongs here. Each
 * line says what the appliance was TOLD, not what it concluded — the distinction v0.16.2 drew
 * between promoting and affirming, in the one place an operator reads it back. */
const JUDGED_TEXT = {
  verdict: "This grouping has been judged",
  move: "An alarm has been moved out of this grouping",
  merge: "Another situation has been merged into this one",
  operator_split: "Members have been split out of this grouping",
};

const ADJUST_TITLE =
  "Reopens the grouping controls. The judgement already on record is kept — a second one is a " +
  "second row of evidence, not a correction to the first.";

const OUTCOME_TEXT = {
  promote: "Open, and yours. Nothing was recorded about whether the grouping is right.",
  confirm: "Recorded: this grouping is correct. Every pair in it is now an asserted positive.",
  split: "Recorded: this grouping is wrong. Only the members you marked are asserted negative.",
  close: "Closed.",
  clear: "Cleared. That alarm was stale; this says nothing about the grouping and the correlator " +
         "learns nothing from it.",
};
