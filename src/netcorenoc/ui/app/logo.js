/* The brand mark (ADR #479) — the logotype's one drawing, kept apart from the icon family
 * (`icons.js`), whose single geometry it does not share: a filled tile, not a 1.5 stroke. */

import { html } from "./dom.js";

/**
 * **The brand mark** (ADR #479): an N drawn as a network — four nodes, three links — on a blue
 * tile. The diagonal is the broker's work: the link that joins one side's alarms to the other's.
 * The top-right node is a ring, not a dot: the probable root cause the correlation points at.
 * Simple enough to read at 16 px in a tab, as Paperclip's mark is. `favicon.svg` is this drawing
 * with its colours written out, because a standalone file cannot read the stylesheet's tokens.
 */
export function LogoMark({ size = 24 }) {
  return html`<svg class="logo-mark" viewBox="0 0 32 32" width=${size} height=${size}
       aria-hidden="true" focusable="false">
    <defs><linearGradient id="ncn-logo-tile" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#6aa0ff" /><stop offset="1" stop-color="#3355e8" />
    </linearGradient></defs>
    <rect width="32" height="32" rx="8" fill="url(#ncn-logo-tile)" />
    <path d="M9.5 22.5V9.5L22.5 22.5V12.4" fill="none" stroke="#ffffff" stroke-width="2.4"
          stroke-linecap="round" stroke-linejoin="round" />
    <circle cx="9.5" cy="22.5" r="2.6" fill="#ffffff" />
    <circle cx="9.5" cy="9.5" r="2.6" fill="#ffffff" />
    <circle cx="22.5" cy="22.5" r="2.6" fill="#ffffff" />
    <circle cx="22.5" cy="9.5" r="2.5" fill="none" stroke="#ffffff" stroke-width="2" />
  </svg>`;
}
