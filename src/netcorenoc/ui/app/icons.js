/* The icon set: inline SVG, one module, drawn rather than acquired.
 *
 * ## Why these are drawn (DECISIONS #236)
 *
 * `tests/test_build_step.py` refuses a `package.json`, so an icon package is not available;
 * `tests/test_supply_chain.py` would want a checksum and a licence for a vendored set, in the same
 * commit. Both point the same way: a table of path data in a module this project owns costs no
 * dependency, no build step, no font-stack risk and no third-party licence.
 *
 * What they replace: seventeen Unicode glyphs — `◎ ◉ ⬡ ▤ ▢ ≡ ✓ ▦ ⚖ ◍ ⚿ ⚙ ∿ ⛨ ⚑ ⛓ ◐` — from four
 * Unicode blocks, rendered at whatever weight the operator's font stack decided. #221 kept them
 * deliberately and said inline SVG was the right answer; this is that answer.
 *
 * ## The grid
 *
 * 24x24 viewBox, 1.5 stroke, `currentColor`, round caps and joins, **no fill**. One weight, one
 * corner radius, one optical size — which is what makes seventeen marks a family rather than
 * seventeen drawings. Rendered at `1em`, so an icon is the size of the text beside it and moves
 * with the type scale rather than against it.
 *
 * ## Every icon here is rendered somewhere
 *
 * `tests/test_icons.py` walks this table and the call sites and fails on an entry nobody uses. A
 * set of forty when twenty-five render is forty to maintain (VII.3), and an icon library that grew
 * past its console is how the glyph set stopped being a family in the first place.
 *
 * ## They are decoration, never the only signal
 *
 * Every call site pairs an icon with its text label and marks the icon `aria-hidden`. That was true
 * of the glyphs and is not being weakened: an icon that carried meaning alone would fail the same
 * operator the severity rules are written for.
 */

import { html } from "./dom.js";

/* Path data only. `d` is one or more `<path>` strings; `c` is optional `<circle>` triples
 * (cx, cy, r). Nothing here carries a colour, a size or a stroke width — those belong to the one
 * `<svg>` below, so a change to the family is a change in one place. */
const ICONS = {
  // -- the seventeen views, in registry order ------------------------------------------
  overview: { d: ["M3 12a9 9 0 0 1 18 0", "M12 12l4.5-3"], c: [[12, 12, 1]] },
  situations: { d: ["M4 8.5 12 4l8 4.5-8 4.5-8-4.5Z", "M4 15.5 12 20l8-4.5"] },
  graph: { d: ["M7.5 8.7l4-2.2M16.5 8.7l-4-2.2M7 12v3M17 12v3M9 18h6"],
           c: [[12, 5, 2], [6, 10.5, 2], [18, 10.5, 2], [12, 19, 2]] },
  timeline: { d: ["M3 20h18", "M6 20V9", "M11 20V5", "M16 20v-8", "M21 20v-4"] },
  entities: { d: ["M4 7.5 12 3l8 4.5v9L12 21l-8-4.5v-9Z", "M4 7.5 12 12l8-4.5M12 12v9"] },
  classes: { d: ["M8 6h12M8 12h12M8 18h12", "M4 6h.01M4 12h.01M4 18h.01"] },
  labelling: { d: ["M4 12.5 9 17.5 20 6.5"] },
  corpus: { d: ["M4 6c0-1.4 3.6-2.5 8-2.5S20 4.6 20 6s-3.6 2.5-8 2.5S4 7.4 4 6Z",
                "M4 6v12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5V6", "M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5"] },
  promotion: { d: ["M12 4v16M7 20h10", "M4 9h16", "M4 9 1.5 15h5L4 9Z", "M20 9l-2.5 6h5L20 9Z"] },
  users: { d: ["M3 20v-1.5A4.5 4.5 0 0 1 7.5 14h3A4.5 4.5 0 0 1 15 18.5V20",
               "M16.5 14h.5a4.5 4.5 0 0 1 4.5 4.5V20", "M15.5 5.3a3 3 0 0 1 0 5.4"],
           c: [[9, 8, 3.2]] },
  tokens: { d: ["M13.5 10.5 21 3M18 6l2 2M16 8l1.5 1.5"], c: [[8.5, 15.5, 5]] },
  settings: { d: ["M4 7h5M13 7h7M4 17h9M17 17h3",
                  "M11 4.5v5M15 14.5v5"] },
  scorer: { d: ["M3 15c3 0 3-8 6-8s3 8 6 8 3-6 6-6"] },
  governance: { d: ["M12 3.5 20 6v6c0 4.2-3.2 7.4-8 8.5-4.8-1.1-8-4.3-8-8.5V6l8-2.5Z",
                    "M9 12l2.2 2.2L15.5 10"] },
  quarantine: { d: ["M6 21V4", "M6 4.5h11l-2.2 4 2.2 4H6"] },
  audit: { d: ["M10 13.5a4 4 0 0 0 5.7.3l2.6-2.6a4 4 0 0 0-5.7-5.7l-1.5 1.5",
               "M14 10.5a4 4 0 0 0-5.7-.3l-2.6 2.6a4 4 0 0 0 5.7 5.7l1.5-1.5"] },
  account: { d: ["M5 20v-1a4.5 4.5 0 0 1 4.5-4.5h5A4.5 4.5 0 0 1 19 19v1"], c: [[12, 8, 3.5]] },

  // -- the theme control's three states ------------------------------------------------
  moon: { d: ["M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"] },
  sun: { d: ["M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2" +
             "M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"], c: [[12, 12, 4]] },
  // "system": half of each, because the state is "whichever the operating system says".
  auto: { d: ["M12 3.5v17", "M12 3.5a8.5 8.5 0 0 1 0 17"], c: [[12, 12, 8.5]] },

  // -- actions ------------------------------------------------------------------------
  eye: { d: ["M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"],
         c: [[12, 12, 3]] },
  "eye-off": { d: ["M4 4l16 16", "M9.9 5.9A9.8 9.8 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-3.3 4",
                   "M6.4 8A17 17 0 0 0 2.5 12S6 18.5 12 18.5c1 0 1.9-.2 2.7-.5",
                   "M10 10a3 3 0 0 0 4 4"] },
  chevron: { d: ["M9 5.5 15.5 12 9 18.5"] },
  check: { d: ["M4.5 12.5 9.5 17.5 19.5 6.5"] },
  cross: { d: ["M6 6l12 12M18 6 6 18"] },
  warn: { d: ["M12 4.5 21 19.5H3L12 4.5Z", "M12 10v4"], c: [[12, 16.6, 0.6]] },
  info: { d: ["M12 11v5"], c: [[12, 12, 8.5], [12, 8, 0.6]] },
  shield: { d: ["M12 3.5 20 6v6c0 4.2-3.2 7.4-8 8.5-4.8-1.1-8-4.3-8-8.5V6l8-2.5Z", "M12 9v4"],
            c: [[12, 15.8, 0.6]] },

  // -- v0.16.4: the shell's own marks ---------------------------------------------------
  //
  // Each replaces a word the control now says for itself. The chain is the situation permalink,
  // which read `link` beside a `#41` an operator could already see.
  link: { d: ["M10 13.5a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-1 1",
              "M14 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.5 3.5 0 0 0 5 5l1-1"] },
};

/** Every name this module can draw. Read by `tests/test_icons.py`, never for dispatch. */
export const ICON_NAMES = Object.keys(ICONS);

/**
 * One icon. `aria-hidden` always: every call site pairs it with a text label, and a call site that
 * did not would be relying on an icon to carry meaning alone.
 *
 * An unknown name renders **nothing** rather than a placeholder box. A missing icon should look
 * like a missing icon in the diff and like an ordinary label on the screen; a fallback glyph would
 * ship and nobody would notice.
 */
export function Icon({ name, className }) {
  const icon = ICONS[name];
  if (!icon) return null;
  return html`<svg class=${className ? `icon-svg ${className}` : "icon-svg"}
       viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true" focusable="false"
       fill="none" stroke="currentColor" stroke-width="1.5"
       stroke-linecap="round" stroke-linejoin="round">
    ${(icon.d || []).map((d, i) => html`<path key=${`p${i}`} d=${d} />`)}
    ${(icon.c || []).map(([cx, cy, r], i) =>
      html`<circle key=${`c${i}`} cx=${cx} cy=${cy} r=${r} />`)}
  </svg>`;
}
