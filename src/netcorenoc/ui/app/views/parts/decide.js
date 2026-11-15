/* The decision bar: everything an operator can decide about a situation, in one row.
 *
 * **Split out of `views/parts/judge.js` in v0.20.0**, at the module graph's 17 579-byte ceiling —
 * `judge.js` reached 19 164 — and on the seam this release created. That file is the *state
 * machine*: what has been marked, what is being sent, what the server answered, and which of the
 * five writes each gesture performs. This one is the *surface*: given those facts, what may be
 * pressed, where, and in what words. They change for different reasons — one changes when a route
 * changes, the other when the maintainer reads the screen — and `judge.js`'s own header records
 * the same split being made twice before (DECISIONS #239's seam, a fourth time).
 *
 * ## One bar, above the table, and what that fixed (v0.20.0)
 *
 * Before this release the promote sat in a `.fb` row of its own **above** the judged note, the
 * verdicts in a second `.fb` row **below** it, and the bulk clear in a third block above both:
 * three separate strips of buttons around one member table. *"The Clear button feels completely
 * tacked on"* and *"'Start working this' feels disconnected"* are two readings of that layout.
 * They are one decision surface and they now look like one.
 *
 * ## What `.fb` means, and why the promote and the clear are not in it
 *
 * `.fb` is exactly the controls that assert something about the **grouping**. That is what a
 * judged situation's disclosure folds (#291), and it is what
 * `test_every_gesture_stays_reachable_in_every_status_the_server_accepts_it_in` reads. Widening
 * it to mean "the buttons near the top of the card" would make the disclosure fold a hand-clear,
 * which asserts nothing about a grouping and reaches no training row
 * (`PREREGISTRATION-0.16.0.md` §1). So the promote sits beside `.fb` and the bulk clear after it,
 * inside the bar and outside the class.
 */

import { html } from "../../dom.js";
import { Icon } from "../../icons.js";
import { BulkClear } from "./bulkclear.js";
import { age, timeTitle } from "../../format.js";

/**
 * The bar, and the judged note above it.
 *
 * Every argument is a fact the caller already computed, or a callback into the caller's own
 * write. Nothing here posts, holds state or reads the store: a surface that decided when to send
 * would be a second copy of the rule `judge.js` owns.
 */
export function Decide({
  status, judged, adjusting, grouping, restructurable, sending, restructure,
  activeCount, markedActive, confirmingClear, kase, canWork,
  onAdjust, onPromote, onVerdict, onClose, onAskClear, onClearMany,
}) {
  return html`<div>
    ${judged && !adjusting ? html`<div class="judged">
      <p class="judged-note">
        <${Icon} name="check" />${" "}
        <b>${JUDGED_TEXT[judged.kind] ?? "This grouping has been judged"}</b>
        ${judged.actor ? ` by ${judged.actor_name || judged.actor}` : ""}${" "}
        <span class="age" title=${timeTitle(judged.at)}>${age(judged.at)} ago</span>
      </p>
      <button type="button" onClick=${onAdjust} title=${ADJUST_TITLE}>Adjust the grouping</button>
    </div>` : null}

    <div class="decide">
      ${/* ADR #480: *Start working this* opens the NOC case, and only an OPEN situation —
            one whose grouping somebody confirmed or corrected — offers it. Of ten situations a
            storm opens, the NOC works the one the others propagate from; a new situation is
            still a question about the grouping. Once the case exists the button is its link. */ null}
      ${status === "open" && !kase && canWork ? html`<button type="button" class="tap promote"
        disabled=${sending} title=${PROMOTE_TITLE} onClick=${onPromote}>
        <${Icon} name="headset" /> Start working this
      </button>` : null}
      ${kase ? html`<a class="btn-link case-link" href=${`#/cases/${kase.sid}`}
        title="This situation's case in the NOC">
        <${Icon} name="headset" /> Case in the NOC · ${CASE_WORDS[kase.status] ?? kase.status}
      </a>` : null}

      ${/* **What an operator does about a grouping** (ADR #489). *Confirm grouping* accepts it —
            the only press that opens a situation. *Grouping is wrong* is gone: the maintainer
            measured it as almost never pressed, because an operator who finds a grouping wrong
            fixes it — merges it with the situation it belongs to, or moves the members that do
            not belong — and those two gestures sit here now, in its place (`restructure.js`).
            Negative evidence is not lost with it: a move and a split assert their departing
            members apart from the rest, exactly as the marked verdict did. The `split` verdict
            route stays for programs.

            The CLASS is what the DOM harness locates these by, so the next rewording costs a
            label and not four scenarios. */ null}
      ${grouping ? html`<div class="fb">
        <button type="button" class="primary verdict verdict-confirm" disabled=${sending}
                onClick=${() => onVerdict("confirm")}>
          <${Icon} name="check" /> Confirm grouping
        </button>
        ${restructure}
        ${restructurable ? html`<button type="button" class="fb-close" disabled=${sending}
                onClick=${onClose}>Close situation</button>` : null}
      </div>` : null}

      ${activeCount > 0
        ? html`<${BulkClear} active=${activeCount} marked=${markedActive}
                   confirming=${confirmingClear} sending=${sending}
                   onAsk=${onAskClear} onGo=${onClearMany} />`
        : null}
    </div>
    ${/* ADR #480: what each step does, said once where it is decided. */ null}
    ${status === "new" || status === "pending" ? html`<p class="hint decide-note">New until somebody presses
      Confirm grouping — merging and moving members keep it New.</p>` : null}
    ${status === "open" && !kase && canWork ? html`<p class="hint decide-note">Open, and no NOC case
      yet. Start working this when this is the situation the NOC should work.</p>` : null}
  </div>`;
}

const PROMOTE_TITLE =
  "Opens this situation's case in the NOC queue, where a person or an AI agent takes it. It " +
  "records nothing about whether the grouping is right — Confirm is how you say that.";

/** A case's status in a word, for the link (`engine/noc/cases.py` STATUSES, shortened). */
const CASE_WORDS = {
  new: "opening", auto: "automated checks", queue: "in the queue", working: "working",
  validate: "validate resolution", resolved: "resolved", closed: "closed",
};

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
