/* The operator's gestures: move, merge, split, name — and the confidence they carry.
 *
 * ## Why the confidence control is one control and not four
 *
 * An operator restructuring a situation is doing **one** thing: saying how the incident actually
 * looks. Asking them how sure they are once, and applying that answer to whichever action they then
 * take, matches the gesture. Four sliders would be four chances to leave one at a value nobody
 * chose, and the plan's §4 registers a floor of 0.50 below which a gesture produces no training
 * row — so a control left at a stale value is not cosmetic, it decides whether the appliance learns
 * from what the operator just did.
 *
 * **The number is always on screen, with what it does to the row.** `m(c) = 0.6 + 0.4c` is
 * registered, so an operator can be told exactly what their answer is worth — and below 0.50 the
 * card says the action will still happen and will teach the correlator nothing. That sentence is
 * the whole of the honesty here: a control that silently discarded the evidence would be worse than
 * one that never asked.
 *
 * ## What is deliberately NOT here
 *
 * No situation picker. Move and merge take a **situation id**, typed, because the id is the
 * identity — it is what an operator pastes into a chat during an incident and what the permalink
 * carries — and a dropdown of every open situation is a search problem, which is v0.16.1's
 * (`docs/plans/v0.16.1-visualisation.md`). A typed id that does not exist answers 404 and says so.
 *
 * No model-proposed name. A model writing "fibre cut" above a grouping the operator is about to
 * judge contaminates that judgement, which is the `incumbent_linked` mistake in a new register
 * (`PREREGISTRATION-0.16.0.md` §1's register, one level up).
 */

import { html, Component, cx } from "../../dom.js";
import { Icon } from "../../icons.js";
import { age, percent, plural, timeTitle } from "../../format.js";

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

/**
 * The three restructuring gestures.
 *
 * Each button states what it will do to **this** situation in the words of the thing it changes,
 * and each is disabled with a reason rather than silently inert: a control that is grey for no
 * stated reason is a control an operator files a ticket about.
 */
export class Restructure extends Component {
  constructor(props) {
    super(props);
    this.state = { destination: "", source: "", confidence: 0.8, busy: false, outcome: null };
  }

  async send(kind, path, body) {
    if (this.state.busy) return;
    this.setState({ busy: true, outcome: null });
    try {
      await this.props.post(path, body);
      this.setState({ busy: false, outcome: { ok: true, kind } });
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, error } });
    }
  }

  render({ sid, marked }, { destination, source, confidence, busy, outcome }) {
    const one = marked.size === 1 ? [...marked][0] : null;
    const c = confidence;
    return html`<section class="lifecycle">
      <h3>Restructure this situation</h3>
      <p class="hint">Correcting the grouping is the strongest evidence this appliance can be
        given: moving one alarm says both where it does not belong and where it does.</p>

      <${Confidence} value=${c} disabled=${busy}
                     onChange=${(v) => this.setState({ confidence: v })} />

      <div class="lifecycle-actions">
        <div class="lifecycle-action">
          <label for="lcMoveTo">Move the marked alarm to situation</label>
          <input id="lcMoveTo" type="number" min="1" inputmode="numeric" value=${destination}
                 placeholder="id" onInput=${(e) => this.setState({ destination: e.target.value })} />
          <button type="button" disabled=${busy || one === null || !destination}
                  title=${one === null ? "Mark exactly one member to move it" : ""}
                  onClick=${() => this.send("move", `/api/situations/${sid}/move`, {
                    alarm_id: one, to_situation_id: Number(destination), confidence: c })}>
            <${Icon} name="chevron" /> Move
          </button>
        </div>

        <div class="lifecycle-action">
          <label for="lcMergeFrom">Merge situation into this one</label>
          <input id="lcMergeFrom" type="number" min="1" inputmode="numeric" value=${source}
                 placeholder="id" onInput=${(e) => this.setState({ source: e.target.value })} />
          <button type="button" disabled=${busy || !source}
                  onClick=${() => this.send("merge", `/api/situations/${sid}/merge`, {
                    from_situation_id: Number(source), confidence: c })}>
            <${Icon} name="check" /> Merge
          </button>
        </div>

        <div class="lifecycle-action">
          <label>Split the marked members out</label>
          <button type="button" disabled=${busy || marked.size === 0}
                  title=${marked.size === 0 ? "Mark the members that belong elsewhere" : ""}
                  onClick=${() => this.send("split", `/api/situations/${sid}/split`, {
                    alarm_ids: [...marked], confidence: c })}>
            <${Icon} name="cross" />${" "}
            ${marked.size
              ? `Split ${plural(marked.size, "member")} into a new situation`
              : "Split marked members out"}
          </button>
        </div>
      </div>

      ${outcome ? html`<p class=${outcome.ok ? "ok-note" : "err"} role="status">
        ${outcome.ok
          ? RESTRUCTURE_TEXT[outcome.kind]
          : `Not applied — ${outcome.error.detail || outcome.error.message}`}
      </p>` : null}
    </section>`;
  }
}

const RESTRUCTURE_TEXT = {
  move: "Moved. That alarm is now asserted apart from the members it left and together with the " +
        "members it joined.",
  merge: "Merged. Every pair across the two situations is now an asserted positive.",
  split: "Split. Every pair across the new boundary is now an asserted negative.",
};

/**
 * The operator's own name for a situation.
 *
 * Written to a **different column** from the name the server derives: a derived name is a
 * projection of membership and evidence of nothing, an operator's name is a label and carries
 * provenance. Clearing the field withdraws the label and the derived name shows through again.
 *
 * The `id` remains the identity. The heading still says `#12`, the permalink is unchanged, and a
 * name is never a key.
 */
export class NameField extends Component {
  constructor(props) {
    super(props);
    this.state = { draft: props.operatorName || "", busy: false, outcome: null };
  }

  async save() {
    const { sid, post, onDone } = this.props;
    if (this.state.busy) return;
    this.setState({ busy: true, outcome: null });
    const name = this.state.draft.trim();
    try {
      await post(`/api/situations/${sid}/name`, { name: name || null });
      this.setState({ busy: false, outcome: { ok: true, cleared: !name } });
      onDone();
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, error } });
    }
  }

  render({ derivedName }, { draft, busy, outcome }) {
    return html`<section class="lifecycle-name">
      <label for="lcName">Name this situation</label>
      <input id="lcName" type="text" maxlength="120" value=${draft} disabled=${busy}
             placeholder=${derivedName || "#id"} aria-describedby="lcNameNote"
             onInput=${(e) => this.setState({ draft: e.target.value })} />
      <button type="button" disabled=${busy} onClick=${() => this.save()}>Save</button>
      <p id="lcNameNote" class="hint">
        ${derivedName
          ? `Without a name of your own this situation is called “${derivedName}”, which the ` +
            "appliance derives from its members and recomputes when they change."
          : "The appliance has no members to derive a name from."}
      </p>
      ${outcome ? html`<p class=${outcome.ok ? "ok-note" : "err"} role="status">
        ${outcome.ok
          ? (outcome.cleared ? "Name withdrawn." : "Named.")
          : `Not saved — ${outcome.error.detail || outcome.error.message}`}
      </p>` : null}
    </section>`;
  }
}

/* What has been done to this situation, and by whom.
 *
 * Five columns and no more, because the server sends five: the row also carries member digests and
 * a peer situation id, and a scoped reader must not learn either from a history panel. See
 * `store/situation_events.py::situation_events`.
 *
 * **v0.16.1: `by user:2` became `by alice`, where the server was willing to say so.** The actor is
 * a principal reference — correct, unforgeable, and unhelpful to a human reading their own work.
 * `actor_name` is the username *if the account still exists* and is withheld from a viewer by
 * `FIELD_RULES`, so this renders whichever the server sent and never guesses: a deleted account
 * and a service token both fall back to the reference, which is the honest answer rather than
 * "unknown", because the reference is still exactly who did it.
 *
 * ## v0.16.4, Bug 2: `by admin` and `2m` were one word, and the word looked like a counter
 *
 * The maintainer reported that *"`admin2` became `admin3`"*. Nobody was renaming anything: the row
 * rendered `by admin` immediately followed by the age, so `admin` + `2m` read as `admin2m` and, a
 * minute later, as `admin3m`. Two things were wrong and each needed its own repair, because each
 * is invisible to the thing that would have caught the other.
 *
 *   * **The layout.** `.age` carries `margin-left: auto`, and `style.css` had no `.history-list`
 *     rule at all — so the `<li>` was `display: list-item`, the auto margin did nothing, and the
 *     age sat against the name with up to 1 002 px of empty row beside it. The stylesheet now gives
 *     the row a flex context; measured, the age moved to the right edge at all three widths.
 *   * **The text.** A separator in CSS is not a separator in `textContent`, so a screen reader, a
 *     copy-paste and the DOM harness all still read `edt1s` after the layout was fixed. The
 *     explicit `${" "}` below is what separates the runs *as text*; a whitespace-only text node is
 *     not rendered as a flex item, so it costs nothing visually.
 *
 * The second half is why this is not a one-line CSS commit. Appendix B's blind spot is exactly
 * *"the DOM harness cannot see whitespace"*, and half a repair reads as a whole one until the other
 * half is measured. */
export function History({ events }) {
  return html`<section class="history">
    <h3>What has been done to this situation</h3>
    <ol class="history-list">
      ${events.map((e, index) => html`<li key=${index}>
        <span class="history-kind">${e.kind.replace("_", " ")}</span>
        ${e.actor ? html`<span class="muted" title=${e.actor_name ? e.actor : ACTOR_TITLE}
                         >${" by "}${e.actor_name || e.actor}</span>` : null}
        ${e.confidence != null
          ? html`<span class="muted">${` at ${percent(e.confidence)} confidence`}</span>` : null}
        ${" "}<span class="age" title=${timeTitle(e.at)}>${age(e.at)}</span>
      </li>`)}
    </ol>
  </section>`;
}

/** Shown where the console has only the reference: an account that is gone, a token, or a viewer
 * whose responses withhold the name. Never "unknown" — the reference IS who did it. */
const ACTOR_TITLE =
  "The principal that made this change. A name is shown where the account still exists and your " +
  "role is shown it; otherwise the reference stands, and it is the record either way.";

/** What a resolved situation says about **why** it left. */
export const RESOLUTION_TEXT = {
  operator: "an operator closed it",
  self_cleared: "every alarm cleared — the network fixed it",
  idle: "nobody looked at it, and it timed out",
  merged: "it was merged into another situation",
  manual_clear: "an operator hand-cleared the last active alarm",
  unattributed: "it closed before this appliance recorded why",
};
