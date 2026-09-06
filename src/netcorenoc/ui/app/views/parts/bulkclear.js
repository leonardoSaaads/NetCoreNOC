/* The bulk hand-clear: one gesture over every active member of a situation.
 *
 * Its own module because `judge.js` reached **19 499 bytes** against the module graph's 17 579-byte
 * ceiling, and because the seam is real rather than arithmetic: `judge.js` is the surface for
 * saying whether a grouping is right, and a hand-clear says nothing whatever about a grouping —
 * `manual_clear` is deliberately absent from `ASSERTING_KINDS` and reaches no training row
 * (`PREREGISTRATION-0.16.0.md` §1). Two questions, two files.
 */

import { html } from "../../dom.js";
import { Icon } from "../../icons.js";
import { plural } from "../../format.js";

/**
 * The bulk hand-clear, and the confirmation it does not get to skip.
 *
 * **Two steps, inline.** The gesture writes an audit row and a lifecycle event per alarm and there
 * is no undo, so a single press is the wrong shape — and `window.confirm` is the wrong dialogue: it
 * is unstyleable, it blocks the event loop the SSE stream runs on, and browsers suppress it in
 * contexts an operator cannot predict. The second press is a different button in a different place,
 * so it cannot be reached by double-clicking the first.
 *
 * **What it says it will do is what the server will do.** With members marked it offers only the
 * marked ones; with none marked it offers every active member. The count is in the button, in the
 * confirmation, and in the result, because "clear all" over a situation holding 1 051 alarms and
 * one holding 3 are different decisions and the operator should not have to scroll to tell which
 * they are making.
 */
export function BulkClear({ active, marked, confirming, sending, onAsk, onGo }) {
  const n = marked > 0 ? marked : active;
  const what = marked > 0 ? `${plural(n, "marked alarm")}` : `all ${plural(n, "active alarm")}`;
  if (confirming) {
    return html`<div class="bulkclear bulkclear-confirm" role="group"
         aria-label="Confirm the hand-clear">
      <span class="bulkclear-ask">Hand-clear ${what}? This cannot be undone.</span>
      <button type="button" class="warn" disabled=${sending} onClick=${onGo}>
        Yes, clear ${n}
      </button>
      <button type="button" disabled=${sending} onClick=${() => onAsk(false)}>Cancel</button>
    </div>`;
  }
  return html`<div class="bulkclear">
    <button type="button" disabled=${sending} title=${BULK_CLEAR_TITLE}
            onClick=${() => onAsk(true)}>
      <${Icon} name="cross" />${" "}
      ${marked > 0 ? `Clear ${plural(n, "marked alarm")}` : `Clear all ${n} active`}
    </button>
    ${marked > 0
      ? null
      : html`<span class="hint bulkclear-hint">or tick members to clear only those</span>`}
  </div>`;
}

const BULK_CLEAR_TITLE =
  "Hand-clears zombie alarms — ones whose device never sent the clear. It records a lifecycle " +
  "fact per alarm and asserts NOTHING about whether they belong together, so it does not touch " +
  "the correlator's training data. If it clears the last active member, the situation resolves " +
  "as manual_clear, which an audit can tell apart from a network that fixed itself.";
