/* The decision bar: everything an operator can decide about a situation, in one row.
 *
 * **Split out of `views/broker/parts/judge.js` in v0.20.0**, at the module graph's 17 579-byte ceiling —
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

import { html } from "../../../dom.js";
import { Icon } from "../../../icons.js";
import { BulkClear } from "./bulkclear.js";

/**
 * The bar, and the judged note above it.
 *
 * Every argument is a fact the caller already computed, or a callback into the caller's own
 * write. Nothing here posts, holds state or reads the store: a surface that decided when to send
 * would be a second copy of the rule `judge.js` owns.
 */
export function Decide({
  status, sending, restructure,
  activeCount, markedActive, confirmingClear, kase, canWork,
  onPromote, onVerdict, onAskClear, onClearMany,
}) {
  // ADR #491: Confirm only where a grouping still waits for it — a New situation. Never on Open (it
  // is confirmed), on Pending (Accept or Reject answer it) or once it is over (Resolved).
  const confirmable = status === "new";
  return html`<div>
    <div class="decide">
      ${/* ADR #480: *Start working this* opens the NOC case of an OPEN situation. Once the case
            exists the button is its link. */ null}
      ${status === "open" && !kase && canWork ? html`<button type="button" class="tap promote"
        disabled=${sending} title=${PROMOTE_TITLE} onClick=${onPromote}>
        <${Icon} name="headset" /> Start working this
      </button>` : null}
      ${kase ? html`<a class="btn-link case-link" href=${`#/cases/${kase.sid}`}
        title="This situation's case in the NOC">
        <${Icon} name="headset" /> Case in the NOC · ${CASE_WORDS[kase.status] ?? kase.status}
      </a>` : null}

      ${/* **What an operator does about a grouping** (ADRs #489, #491): *Confirm grouping* accepts
            it — the only press that opens a situation — and merging or moving members corrects
            it (`restructure.js`). *Close situation* is gone: *Clear all active* is the hygiene that
            ends a situation, and a close left its alarms active under a situation already over.
            The CLASS is what the DOM harness locates these by. */ null}
      ${confirmable || restructure ? html`<div class="fb">
        ${confirmable ? html`<button type="button" class="primary verdict verdict-confirm" disabled=${sending}
                onClick=${() => onVerdict("confirm")}>
          <${Icon} name="check" /> Confirm grouping
        </button>` : null}
        ${restructure}
      </div>` : null}

      ${activeCount > 0
        ? html`<${BulkClear} active=${activeCount} marked=${markedActive}
                   confirming=${confirmingClear} sending=${sending}
                   onAsk=${onAskClear} onGo=${onClearMany} />`
        : null}
    </div>
    ${status === "new" ? html`<p class="hint decide-note">New until somebody presses Confirm
      grouping — merging and moving members keep it New.</p>` : null}
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

