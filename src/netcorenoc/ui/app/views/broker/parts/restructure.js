/* Merging and moving: the two gestures that change which alarms are in a situation (ADR #489).
 *
 * They sit in the decision bar, beside *Confirm grouping*, where *Grouping is wrong* used to be —
 * an answer the maintainer measured as almost never given: an operator who finds a grouping
 * wrong does not say so, they fix it, by merging it with the situation it belongs to or by moving
 * the members that do not belong somewhere else. Neither opens the situation: only *Confirm
 * grouping* does (`store/situations.py` TRANSITIONS).
 *
 * ## The picker: which kind of situation first, then the likely ones
 *
 *   1. **Merge another situation in** (or **Move N members elsewhere**, once members are ticked)
 *      asks first whether the other situation is **New** or **Open** — the operator knows which,
 *      and the two lists are different questions: a New one nobody has confirmed, an Open one
 *      somebody is working;
 *   2. the list is the situations of that status **most likely related** to this one —
 *      `GET /api/situations/{sid}/related`, ranked by devices in common, the same customer and
 *      how close they started — each with the reason it ranks where it does;
 *   3. **Back** returns to the choice; picking a row does the gesture.
 *
 * ## Which situation survives a merge
 *
 * The button says *in*, and that is what happens — the picked situation joins this one — with one
 * exception the operator would otherwise be punished for: a **New** situation merged with an
 * **Open** one goes *into the Open one*. An Open situation is confirmed work, perhaps with a NOC
 * case; it never disappears into a grouping nobody has confirmed. The list says which way it goes
 * before anything is pressed.
 *
 * ## The confidence, and why it stays
 *
 * `m(c) = 0.6 + 0.4c` is registered and below 0.50 a gesture teaches nothing; the slider says so
 * beside the list that fires the gesture. **Shown, never applied here**: the weight that reaches a
 * fit is derived server-side.
 */

import { html, Component, cx } from "../../../dom.js";
import { get } from "../../../api.js";
import { Icon } from "../../../icons.js";
import { age, percent, plural, timeTitle } from "../../../format.js";
import { navigate } from "../../../router.js";
import * as store from "../../../store.js";

/** The registered floor. Below it a gesture is recorded in full and produces no training row. */
export const CONFIDENCE_FLOOR = 0.5;

/** `m(c) = 0.6 + 0.4c`, the registered multiplier — shown, never applied here. */
export function weightAt(confidence) {
  return 0.6 + 0.4 * confidence;
}

/** The confidence a gesture carries, with the number and its consequence both on screen.
 *
 * ADR #506: the slider works in whole percent (0-100, step 5) and the gesture carries the fraction.
 * It stepped 0.05 up to 1: a fractional step is rounded differently by each browser engine, and the
 * owner could not move it to 100% — an integer step reaches its maximum everywhere. */
export function Confidence({ value, onChange, disabled }) {
  const below = value < CONFIDENCE_FLOOR;
  return html`<div class=${cx("confidence", below && "confidence-below")}>
    <label for="fbConfidence">How sure are you?</label>
    <input id="fbConfidence" type="range" min="0" max="100" step="5" value=${Math.round(value * 100)}
           disabled=${disabled} aria-describedby="fbConfidenceNote" aria-valuetext=${percent(value)}
           onInput=${(e) => onChange(Math.min(100, Math.max(0, Number(e.target.value))) / 100)} />
    <output for="fbConfidence" class="confidence-value">${percent(value)}</output>
    <p id="fbConfidenceNote" class=${cx("hint", below && "err")}>
      ${below
        ? "Below 50%, the change is made and recorded, and it teaches the correlator nothing."
        : `Counts toward training at ${percent(weightAt(value))} of a full weight.`}
    </p>
  </div>`;
}

/** The situations of one status in the list the console already holds, newest first. */
function peers(sid, kind) {
  return (store.get().situations || [])
    .filter((s) => s.id !== sid && (s.status === kind || (kind === "new" && s.status === "pending"))
      && (s.alarm_count ?? 0) > 0)
    .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
}

/** How many of each kind there are to choose from, read from the same list. */
function kindCount(sid, kind) {
  return peers(sid, kind).length;
}

/** Why a candidate ranks where it does, in the words an operator checks by eye. */
function reasons(s) {
  const out = [];
  if (s.shared_devices) out.push(`${plural(s.shared_devices, "device")} in common`);
  if (s.same_customer) out.push("same customer");
  if (s.gap_s != null) out.push(s.gap_s < 60 ? "started together" : `started ${age(0, s.gap_s)} apart`);
  return out;
}

export class Restructure extends Component {
  constructor(props) {
    super(props);
    this.state = { asking: null, kind: null, list: null, filter: "", confidence: 0.8, busy: false, outcome: null };
  }

  /** Open or close one of the two pickers; either way the kind is asked again. */
  ask(which) {
    const asking = this.state.asking === which ? null : which;
    this.setState({ asking, kind: null, list: null, filter: "", outcome: null });
  }

  /** The kind chosen: the likely ones from the server, or the console's own list if it cannot answer. */
  async choose(kind) {
    const { sid } = this.props;
    this.setState({ kind, list: null, filter: "" });
    if (kind === "split") return;
    let list;
    try { list = await get(`/api/situations/${sid}/related?status=${kind}`); } catch { list = null; }
    if (this.state.kind === kind) this.setState({ list: Array.isArray(list) ? list : peers(sid, kind) });
  }

  /** One request; `open` names the situation to show afterwards when this one merged away. */
  async send(kind, path, body, open = null) {
    if (this.state.busy) return;
    this.setState({ busy: true, outcome: null });
    try {
      const out = await this.props.post(path, body);
      const count = out?.moved ?? this.props.marked.size;
      this.setState({ busy: false, asking: null, kind: null, list: null, outcome: { ok: true, kind, count } });
      this.props.onDone();
      if (open !== null) navigate(`situations/${open}`);
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, error } });
    }
  }

  /* Move every marked member as ONE gesture (ADR #436): every member is a merge of this situation
   * into the destination (the server refuses a move that empties its source); some members are one
   * `/move`; no destination (`null`) is a split into a new situation. */
  move(target) {
    const { sid, marked, total } = this.props;
    const c = this.state.confidence;
    const ids = [...marked];
    if (target === null) {
      return this.send("split", `/api/situations/${sid}/split`, { alarm_ids: ids, confidence: c });
    }
    if (ids.length >= total) {
      return this.send("merge", `/api/situations/${target}/merge`, { from_situation_id: sid, confidence: c }, target);
    }
    return this.send("move", `/api/situations/${sid}/move`,
                     { alarm_ids: ids, to_situation_id: target, confidence: c });
  }

  /** Merge: the picked one comes in — unless this is New and it is Open, which this joins. */
  merge(target) {
    const { sid, status } = this.props;
    const c = this.state.confidence;
    if (status !== "open" && this.state.kind === "open") {
      return this.send("merge", `/api/situations/${target}/merge`, { from_situation_id: sid, confidence: c }, target);
    }
    return this.send("merge", `/api/situations/${sid}/merge`, { from_situation_id: target, confidence: c });
  }

  render({ sid, status, marked, total }, { asking, kind, list, filter, confidence, busy, outcome }) {
    const n = marked.size;
    const everything = n > 0 && n >= total;
    const into = status !== "open" && kind === "open";
    return html`<span class="restructure">
      <span class="lifecycle-actions">
        ${n > 0 ? html`<button type="button" class=${cx("tap", asking === "move" && "on")}
          disabled=${busy} aria-expanded=${asking === "move" ? "true" : "false"}
          onClick=${() => this.ask("move")}>
          <${Icon} name="chevron" />${" "}
          ${everything ? "Merge into another situation" : `Move ${plural(n, "member")} elsewhere`}
        </button>` : null}
        <button type="button" class=${cx("tap", asking === "merge" && "on")}
          disabled=${busy} aria-expanded=${asking === "merge" ? "true" : "false"}
          title="Bring another situation's alarms into this one"
          onClick=${() => this.ask("merge")}>
          <${Icon} name="link" />${" "}Merge another situation in
        </button>
      </span>

      ${asking ? html`<div class="destinations" role="group" aria-label="Where to">
        ${!kind ? html`<${Kinds} sid=${sid} move=${asking === "move" && !everything}
            onPick=${(k) => this.choose(k)} />`
          : kind === "split" ? html`<${SplitHere} n=${n} busy=${busy}
              onBack=${() => this.setState({ kind: null })} onGo=${() => this.move(null)} />`
          : html`<${Candidates} kind=${kind} list=${list} filter=${filter} busy=${busy}
              note=${asking === "merge"
                ? (into ? "This situation's alarms join the Open one you pick." : "The one you pick joins this situation.")
                : everything ? "Every member is ticked: this situation joins the one you pick."
                : `${plural(n, "member")} move to the one you pick.`}
              onBack=${() => this.setState({ kind: null, list: null })}
              onFilter=${(v) => this.setState({ filter: v })}
              onPick=${(target) => (asking === "move" ? this.move(target) : this.merge(target))} />`}
        ${kind ? html`<${Confidence} value=${confidence} disabled=${busy}
                       onChange=${(v) => this.setState({ confidence: v })} />` : null}
      </div>` : null}

      ${outcome ? html`<p class=${cx("restructure-outcome", outcome.ok ? "ok-note" : "err")} role="status">
        ${outcome.ok
          ? (outcome.kind === "move" ? `${plural(outcome.count, "member")} moved.` : DONE_TEXT[outcome.kind])
          : `Not applied — ${outcome.error.detail || outcome.error.message}`}
      </p>` : null}
    </span>`;
  }
}

/** Step 1: New or Open — and, for a move, a situation of their own. */
function Kinds({ sid, move, onPick }) {
  const choice = (key, title, sub, count) => html`<button type="button" class="dest-kind" data-kind=${key}
      onClick=${() => onPick(key)}>
      <b>${title}${count == null ? "" : html` <span class="tab-count">${count}</span>`}</b>
      <span class="muted">${sub}</span>
    </button>`;
  return html`<div class="dest-kinds">
    ${choice("new", "New", "Formed by the model, not confirmed yet", kindCount(sid, "new"))}
    ${choice("open", "Open", "Confirmed, and being worked", kindCount(sid, "open"))}
    ${move ? choice("split", "A new situation", "The ticked members on their own", null) : null}
  </div>`;
}

/** A move into a situation that does not exist yet: one press, after saying what it makes. */
function SplitHere({ n, busy, onBack, onGo }) {
  return html`<div class="dest-head">
      <button type="button" class="btn-ghost dest-back" onClick=${onBack}><${Icon} name="chevron" /> Back</button>
      <span>${plural(n, "member")} become a New situation of their own.</span>
    </div>
    <ul class="destination-list"><li>
      <button type="button" class="destination destination-new" disabled=${busy} onClick=${onGo}>
        <b>A new situation</b> <span class="muted">${plural(n, "member")} split out on their own</span>
      </button>
    </li></ul>`;
}

/** Step 2: the likely ones of that kind, the reason each ranks where it does, and Back. */
function Candidates({ kind, list, filter, busy, note, onBack, onFilter, onPick }) {
  const needle = filter.trim().toLowerCase();
  const all = list || [];
  const shown = needle
    ? all.filter((s) => String(s.id).includes(needle)
        || (s.operator_name || s.model_name || s.derived_name || "").toLowerCase().includes(needle))
    : all;
  return html`<div class="dest-head">
      <button type="button" class="btn-ghost dest-back" onClick=${onBack}><${Icon} name="chevron" /> Back</button>
      <b>${kind === "open" ? "Open" : "New"} situations, most likely first</b>
      <span class="muted">${note}</span>
    </div>
    ${all.length > 6 ? html`<input type="search" class="dest-filter" value=${filter} disabled=${busy}
        aria-label="Filter by id or name" placeholder="Filter by id or name"
        onInput=${(e) => onFilter(e.target.value)} />` : null}
    <ul class="destination-list">
      ${list === null ? html`<li class="hint">Finding the likely ones…</li>` : null}
      ${shown.map((s) => html`<li key=${s.id}>
        <button type="button" class="destination" disabled=${busy} onClick=${() => onPick(s.id)}>
          <b>#${s.id}</b>
          <span class="dest-name">${s.operator_name || s.model_name || s.derived_name || "unnamed"}</span>
          ${reasons(s).map((r) => html`<span key=${r} class="dest-reason">${r}</span>`)}
          <span class="muted">${plural(s.alarm_count, "alarm")}</span>
          <span class="age" title=${timeTitle(s.updated_at)}>${age(s.updated_at)}</span>
        </button>
      </li>`)}
      ${list !== null && shown.length === 0
        ? html`<li class="hint">${all.length ? "None matches that." : `No other ${kind === "open" ? "Open" : "New"} situation.`}</li>`
        : null}
    </ul>`;
}

const DONE_TEXT = {
  merge: "Merged. Only Confirm grouping opens a situation.",
  split: "Split into a new situation, New until confirmed.",
};
