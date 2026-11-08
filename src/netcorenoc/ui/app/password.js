/* The password surface: confirmation, a length meter from the server's own policy, and reveal.
 *
 * ## Why this is one module and not two forms
 *
 * V.2 asks for the same three affordances on the forced first change (`login.js`) and on the
 * signed-in change (`account.js`). Written twice they would drift, and the one that drifted would
 * be the sign-in card — the screen nobody revisits, and the one where a typo costs the appliance.
 *
 * ## The meter measures LENGTH, and says so
 *
 * `crosscutting/auth.py` follows NIST SP 800-63B: **length only, no composition rules, no expiry.**
 * So the meter reports characters against the bound the appliance enforces and nothing else. A
 * meter demanding an uppercase and a digit would be inventing a rule the server does not have —
 * the same defect as showing the wrong minimum, in the other direction — and an operator who
 * satisfied it would still be refused by a server that never asked for it.
 *
 * The bounds are **served** (`session.passwordPolicy()`, from `/api/me` or from the login route's
 * `must_change_password` response). When the server has not said, the meter renders nothing rather
 * than assuming: a hard-coded 12 here is exactly the second source of truth this avoids.
 *
 * ## Reveal
 *
 * A real `<button>` — keyboard-reachable like everything else — that flips `type` between
 * `password` and `text`. Off by default, and it never persists: revealing is a deliberate act each
 * time, not a preference that follows the operator onto a screen somebody is standing behind.
 */

import { html, Component } from "./dom.js";
import { passwordPolicy } from "./session.js";
import { Icon } from "./icons.js";

/** The mismatch message, in one place so both screens refuse in the same words. */
export const MISMATCH = "The two new passwords do not match.";

/**
 * Is this pair submittable? Returns null when it is, or the reason it is not.
 *
 * The order matters: an empty confirmation is "not finished yet" rather than "wrong", so it must
 * not be reported as a mismatch while the operator is still typing it.
 */
export function pairProblem(next, confirm) {
  const policy = passwordPolicy();
  if (!next) return "Enter a new password.";
  if (policy && next.length < policy.min) {
    return `The appliance requires at least ${policy.min} characters.`;
  }
  if (policy && next.length > policy.max) {
    return `The appliance allows at most ${policy.max} characters.`;
  }
  if (!confirm) return "Type the new password a second time to confirm it.";
  if (next !== confirm) return MISMATCH;
  return null;
}

/** A password input with a reveal button beside it. */
export class PasswordInput extends Component {
  constructor(props) {
    super(props);
    this.state = { shown: false };
  }

  render({ id, label, value, autocomplete, onInput, describedBy }, { shown }) {
    return html`<div class="pw-field">
      <label for=${id}>${label}</label>
      <div class="pw-row">
        <!-- \`spellcheck=\${false}\`, the boolean, NOT the string "false" (F88). \`spellcheck\` is an
             IDL boolean; Preact sets the property, and the non-empty string "false" coerces to
             true — so the rendered attribute read \`spellcheck="true"\` and the browser was
             offering to spell-check a password, which on some builds means sending it to a
             remote service. Visible in the DOM the whole time; nothing was reading it. -->
        <input id=${id} type=${shown ? "text" : "password"} value=${value}
               autocomplete=${autocomplete} autocapitalize="none" spellcheck=${false}
               aria-describedby=${describedBy || null}
               onInput=${(e) => onInput(e.target.value)} />
        <button type="button" class="icon pw-reveal"
                aria-pressed=${shown ? "true" : "false"}
                aria-label=${shown ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
                title=${shown ? "Hide" : "Show"}
                onClick=${() => this.setState({ shown: !shown })}>
          <${Icon} name=${shown ? "eye-off" : "eye"} />
        </button>
      </div>
    </div>`;
  }
}

/** How many segments the meter draws: three fill on the way to the minimum, the fourth at it. */
const SEGMENTS = 4;

/**
 * The length meter (ADR #480): four segments, a sentence and a count. Renders nothing at all until
 * the server has stated its policy.
 *
 * Four states, and "too long" is one of them: `MAX_PASSWORD` is a real bound the server enforces
 * and a passphrase can reach it, so a meter that only ever counted upwards would leave an operator
 * refused with no idea why. The segments are never the only signal: the sentence changes with the
 * state, and the count says where the bound is.
 */
export function PasswordMeter({ id, value }) {
  const policy = passwordPolicy();
  if (!policy) return null;
  const n = value.length;
  const state = n === 0 ? "empty" : n > policy.max ? "over" : n < policy.min ? "short" : "ok";
  const lit = state === "ok" || state === "over"
    ? SEGMENTS
    : Math.min(SEGMENTS - 1, Math.ceil((n / policy.min) * (SEGMENTS - 1)));
  const more = policy.min - n;
  const words = {
    empty: `${policy.min}–${policy.max} characters. Length is the only rule.`,
    short: `${more} more character${more === 1 ? "" : "s"} needed`,
    ok: "Accepted — length is the only rule",
    over: `${n - policy.max} too many — the limit is ${policy.max}`,
  };
  const count = {
    empty: "", short: `${n} / ${policy.min}`, ok: `${n} characters`, over: `${n} / ${policy.max}`,
  };
  return html`<p id=${id} class=${`pw-meter pw-meter-${state}`} role="status">
    <span class="pw-segs" aria-hidden="true">
      ${Array.from({ length: SEGMENTS }, (_, i) =>
        html`<span class=${i < lit ? "pw-seg pw-seg-on" : "pw-seg"}></span>`)}
    </span>
    <span class="pw-words">
      ${state === "ok" ? html`<${Icon} name="check" />` : null}${words[state]}
    </span>
    ${count[state] ? html`<span class="pw-count">${count[state]}</span>` : null}
  </p>`;
}
