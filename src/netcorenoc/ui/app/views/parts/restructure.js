/* The three restructuring gestures, and the confidence they carry.
 *
 * **Split out of `views/parts/lifecycle.js` in v0.20.0**, at the module graph's 17 579-byte
 * ceiling and on the seam that file's own title drew: *"move, merge, split, name — and the
 * confidence they carry"*. A name is not a restructuring. What stays behind is what a situation
 * **is and has been** — its operator name, its history, and the vocabulary for how it ended;
 * what comes here is the three gestures that change **which alarms are in it**, which are the
 * ones that produce training rows and the ones the server refuses with 409 once it resolves.
 *
 * ## Why the confidence control is one control and not four
 *
 * An operator restructuring a situation is doing **one** thing: saying how the incident actually
 * looks. Asking them how sure they are once, and applying that answer to whichever action they
 * then take, matches the gesture. Four sliders would be four chances to leave one at a value
 * nobody chose, and the plan's §4 registers a floor of 0.50 below which a gesture produces no
 * training row — so a control left at a stale value is not cosmetic, it decides whether the
 * appliance learns from what the operator just did.
 *
 * **The number is always on screen, with what it does to the row.** `m(c) = 0.6 + 0.4c` is
 * registered, so an operator can be told exactly what their answer is worth — and below 0.50 the
 * card says the action will still happen and will teach the correlator nothing. That sentence is
 * the whole of the honesty here: a control that silently discarded the evidence would be worse
 * than one that never asked.
 *
 * ## v0.20.0: a selection, then a destination — and the typed id is gone (DECISIONS #358)
 *
 * The section was three forms stacked under a heading and a paragraph: a confidence slider, two
 * number fields asking an operator to *type a situation id from memory*, and three buttons, two
 * of which were disabled with a tooltip explaining what to do first. The maintainer's verdict —
 * *"it completely violates UI/UX principles"* — is the correct one, and the shape it should have
 * had is the shape the gesture already has:
 *
 *   1. **tick the members** in the table above (the marks, which already exist and already feed
 *      a split);
 *   2. **say where they go** — and only then, because with nothing ticked there is no gesture to
 *      offer and a disabled button explaining itself is a control that has failed;
 *   3. **pick the destination from the situations that exist**, because the appliance knows them.
 *
 * The typed id was defended as *"the identity — what an operator pastes into a chat"*. The id is
 * still the identity and is still what the picker shows; what changed is that recalling it is no
 * longer the operator's job. The defence's other half — *"a dropdown of every open situation is a
 * search problem"* — was answered by v0.16.1, which shipped the search this filters with.
 *
 * **Split is not gone; it is where it belongs.** `operator_split` is *"move these to a situation
 * that does not exist yet"*, so it is the first row of the same destination list rather than a
 * third control asking a different question. The evidence each row writes is unchanged.
 *
 * ## What is deliberately still NOT here
 *
 * No model-proposed name. A model writing "fibre cut" above a grouping the operator is about to
 * judge contaminates that judgement, which is the `incumbent_linked` mistake in a new register
 * (`PREREGISTRATION-0.16.0.md` §1's register, one level up).
 */

import { html, Component, cx } from "../../dom.js";
import { Icon } from "../../icons.js";
import { age, percent, plural, timeTitle } from "../../format.js";
import * as store from "../../store.js";

/** The registered floor. Below it a gesture is recorded in full and produces no training row. */
export const CONFIDENCE_FLOOR = 0.5;

/**
 * `m(c) = 0.6 + 0.4c`, the registered multiplier — **shown, never applied here.**
 *
 * The console computes it only to *say* what a row will be worth. The weight that reaches a fit is
 * derived server-side, at derivation, composed with the design-effect and class-balance factors;
 * a console that multiplied anything would be a second implementation of a registered constant.
 */
export function weightAt(confidence) {
  return 0.6 + 0.4 * confidence;
}

/** The confidence an action carries, with the number and its consequence both on screen. */
export function Confidence({ value, onChange, disabled }) {
  const below = value < CONFIDENCE_FLOOR;
  return html`<div class=${cx("confidence", below && "confidence-below")}>
    <label for="fbConfidence">How sure are you?</label>
    <input id="fbConfidence" type="range" min="0" max="1" step="0.05" value=${value}
           disabled=${disabled} aria-describedby="fbConfidenceNote"
           onInput=${(e) => onChange(Number(e.target.value))} />
    <output for="fbConfidence" class="confidence-value">${percent(value)}</output>
    <p id="fbConfidenceNote" class=${cx("hint", below && "err")}>
      ${below
        ? "Below 50%, the change is made and recorded, and it teaches the correlator nothing."
        : `Counts toward training at ${percent(weightAt(value))} of a full weight.`}
    </p>
  </div>`;
}

/** The open situations this one could exchange members with, newest first, never itself. */
function peers(sid) {
  return (store.get().situations || [])
    .filter((s) => s.id !== sid && s.status !== "resolved")
    .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
}

/**
 * The two restructuring questions, each one press from the marks that answer them.
 *
 * `asking` is `null`, `"move"` or `"merge"`: which destination list is open. It is per-visit
 * component state, like `judge.js`'s `adjusting`, and nothing persists it — a card that remembered
 * an open picker would be a second notion of situation state (DECISIONS #267).
 */
export class Restructure extends Component {
  constructor(props) {
    super(props);
    this.state = { asking: null, filter: "", confidence: 0.8, busy: false, outcome: null };
  }

  async send(kind, path, body) {
    if (this.state.busy) return;
    this.setState({ busy: true, outcome: null });
    try {
      await this.props.post(path, body);
      this.setState({ busy: false, asking: null, filter: "", outcome: { ok: true, kind } });
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, error } });
    }
  }

  /** Move every marked member. One alarm is one `/move`; several are a `/split` then nothing. */
  move(target) {
    const { sid, marked } = this.props;
    const c = this.state.confidence;
    const ids = [...marked];
    if (target === null) {
      return this.send("split", `/api/situations/${sid}/split`,
                       { alarm_ids: ids, confidence: c });
    }
    // The route moves **one** alarm, because what it asserts is about one alarm's membership.
    // Several marked members are several assertions and the console does not invent a batch
    // route for them: it sends them in order and reports the first refusal.
    return this.sendMany(ids.map((id) => ({
      kind: "move", path: `/api/situations/${sid}/move`,
      body: { alarm_id: id, to_situation_id: target, confidence: c },
    })));
  }

  async sendMany(steps) {
    if (this.state.busy) return;
    this.setState({ busy: true, outcome: null });
    try {
      for (const step of steps) await this.props.post(step.path, step.body);
      this.setState({
        busy: false, asking: null, filter: "",
        outcome: { ok: true, kind: "move", count: steps.length },
      });
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, error } });
    }
  }

  render({ sid, marked }, { asking, filter, confidence, busy, outcome }) {
    const n = marked.size;
    // **Nothing ticked is not a state that needs three disabled controls.** It needs one line
    // saying what to tick, which is what the operator has to do next.
    return html`<section class="lifecycle">
      ${n === 0 && !asking
        ? html`<p class="hint lifecycle-idle">
            <${Icon} name="info" />${" "}Tick members above to move them somewhere else.
          </p>`
        : null}

      <div class="lifecycle-actions">
        ${n > 0 ? html`<button type="button" class=${cx("tap", asking === "move" && "on")}
          disabled=${busy} aria-expanded=${asking === "move" ? "true" : "false"}
          onClick=${() => this.setState({ asking: asking === "move" ? null : "move", filter: "" })}>
          <${Icon} name="chevron" />${" "}Move ${plural(n, "member")} elsewhere
        </button>` : null}
        <button type="button" class=${cx("tap", asking === "merge" && "on")}
          disabled=${busy} aria-expanded=${asking === "merge" ? "true" : "false"}
          onClick=${() => this.setState({ asking: asking === "merge" ? null : "merge", filter: "" })}>
          <${Icon} name="check" />${" "}Merge another situation in
        </button>
      </div>

      ${asking ? html`<${Destinations}
        sid=${sid} kind=${asking} filter=${filter} busy=${busy} marked=${n}
        onFilter=${(v) => this.setState({ filter: v })}
        onPick=${(target) => (asking === "move"
          ? this.move(target)
          : this.send("merge", `/api/situations/${sid}/merge`,
                      { from_situation_id: target, confidence: this.state.confidence }))} />` : null}

      ${/* The confidence belongs to the gesture, so it is beside the list that fires one rather
            than above a section that might not. */ null}
      ${asking ? html`<${Confidence} value=${confidence} disabled=${busy}
                     onChange=${(v) => this.setState({ confidence: v })} />` : null}

      ${outcome ? html`<p class=${outcome.ok ? "ok-note" : "err"} role="status">
        ${outcome.ok
          ? (outcome.kind === "move"
              ? `${outcome.count > 1 ? `${plural(outcome.count, "member")} moved.` : "Moved."} ` +
                RESTRUCTURE_TEXT.move
              : RESTRUCTURE_TEXT[outcome.kind])
          : `Not applied — ${outcome.error.detail || outcome.error.message}`}
      </p>` : null}
    </section>`;
  }
}

/**
 * Where the marked members go, or which situation comes in: the situations that **exist**.
 *
 * The list is the store's, which is every situation this principal was served — so a scoped
 * operator is offered exactly the destinations they may name, and the 404 the route would answer
 * is one an operator can no longer reach by typing.
 *
 * `New situation` is the first row for a move and absent for a merge, because a merge with a
 * situation that does not exist is not a gesture.
 */
function Destinations({ sid, kind, filter, busy, marked, onFilter, onPick }) {
  const needle = filter.trim().toLowerCase();
  const all = peers(sid);
  const shown = needle
    ? all.filter((s) => String(s.id).includes(needle)
        || (s.operator_name || s.derived_name || "").toLowerCase().includes(needle))
    : all;
  return html`<div class="destinations">
    <label class="visually-hidden" for=${`lcFind-${sid}`}>Find a situation</label>
    <input id=${`lcFind-${sid}`} type="search" value=${filter} disabled=${busy}
           placeholder=${`Filter ${plural(all.length, "open situation")} by id or name`}
           onInput=${(e) => onFilter(e.target.value)} />
    <ul class="destination-list">
      ${kind === "move" ? html`<li>
        <button type="button" class="destination destination-new" disabled=${busy}
                onClick=${() => onPick(null)}>
          <b>A new situation</b>${" "}
          <span class="muted">${plural(marked, "member")} split out on their own</span>
        </button>
      </li>` : null}
      ${shown.map((s) => html`<li key=${s.id}>
        <button type="button" class="destination" disabled=${busy} onClick=${() => onPick(s.id)}>
          <b>#${s.id}</b>${" "}
          <span>${s.operator_name || s.derived_name || "unnamed"}</span>${" "}
          <span class="muted">${plural(s.alarm_count, "alarm")}</span>${" "}
          <span class="age" title=${timeTitle(s.updated_at)}>${age(s.updated_at)}</span>
        </button>
      </li>`)}
      ${shown.length === 0
        ? html`<li class="hint">${all.length
            ? "No open situation matches that."
            : "There is no other open situation to use."}</li>`
        : null}
    </ul>
  </div>`;
}

const RESTRUCTURE_TEXT = {
  move: "That alarm is now asserted apart from the members it left and together with the " +
        "members it joined.",
  merge: "Merged. Every pair across the two situations is now an asserted positive.",
  split: "Split into a new situation. Every pair across the new boundary is now an asserted " +
         "negative.",
};
