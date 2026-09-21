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
import { age, plural, timeTitle } from "../../format.js";

/**
 * The bar, and the judged note above it.
 *
 * Every argument is a fact the caller already computed, or a callback into the caller's own
 * write. Nothing here posts, holds state or reads the store: a surface that decided when to send
 * would be a second copy of the rule `judge.js` owns.
 */
export function Decide({
  status, judged, adjusting, grouping, restructurable, sending, marked,
  activeCount, markedActive, confirmingClear,
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
      ${status === "new" ? html`<button type="button" class="tap promote"
        disabled=${sending} title=${PROMOTE_TITLE} onClick=${onPromote}>
        <${Icon} name="chevron" /> Start working this
      </button>` : null}

      ${/* **The two answers to one question**, and the question is "is this grouping right?"
            (v0.20.0, DECISIONS #359). The second button read `Split (wrong grouping)`, which the
            maintainer said *"will likely never be used"* — and they are right about the word.
            `split` is the name of the ROUTE; what the operator is being asked is whether the
            appliance got the grouping right.

            Renamed and NOT removed. `confirm` asserts every pair positive and `split` asserts
            the negatives, so a console offering only the first would feed the correlator a
            corpus with no negative evidence in it — a worse defect than a badly-named button,
            and an invisible one. The route, the payload and `PREREGISTRATION-0.16.0.md` §2's row
            are untouched.

            The CLASS is what the DOM harness locates these by, so the next rewording costs a
            label and not four scenarios. */ null}
      ${grouping ? html`<div class="fb">
        <button type="button" class="primary verdict verdict-confirm" disabled=${sending}
                onClick=${() => onVerdict("confirm")}>
          <${Icon} name="check" /> Confirm grouping
        </button>
        <button type="button" class="warn verdict verdict-split" disabled=${sending}
                onClick=${() => onVerdict("split")}>
          <${Icon} name="cross" />${" "}
          ${marked
            ? `Grouping is wrong — ${plural(marked, "member")} do not belong`
            : "Grouping is wrong"}
        </button>
        ${restructurable ? html`<button type="button" disabled=${sending}
                onClick=${onClose}>Close situation</button>` : null}
      </div>` : null}

      ${activeCount > 0
        ? html`<${BulkClear} active=${activeCount} marked=${markedActive}
                   confirming=${confirmingClear} sending=${sending}
                   onAsk=${onAskClear} onGo=${onClearMany} />`
        : null}
    </div>
  </div>`;
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
