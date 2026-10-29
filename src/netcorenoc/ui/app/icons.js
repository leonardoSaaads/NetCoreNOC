/* The icon set: inline SVG, one module, drawn rather than acquired.
 *
 * ## Why these are drawn (DECISIONS #236)
 *
 * `tests/ui/test_build_step.py` refuses a `package.json`, so an icon package is not available;
 * `tests/security/test_supply_chain.py` would want a checksum and a licence for a vendored set, in the same
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
 * `tests/ui/test_icons.py` walks this table and the call sites and fails on an entry nobody uses. A
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
  // -- the eighteen views, in registry order ------------------------------------------
  overview: { d: ["M3 12a9 9 0 0 1 18 0", "M12 12l4.5-3"], c: [[12, 12, 1]] },
  situations: { d: ["M4 8.5 12 4l8 4.5-8 4.5-8-4.5Z", "M4 15.5 12 20l8-4.5"] },
  graph: { d: ["M7.5 8.7l4-2.2M16.5 8.7l-4-2.2M7 12v3M17 12v3M9 18h6"],
           c: [[12, 5, 2], [6, 10.5, 2], [18, 10.5, 2], [12, 19, 2]] },
  timeline: { d: ["M3 20h18", "M6 20V9", "M11 20V5", "M16 20v-8", "M21 20v-4"] },
  entities: { d: ["M4 7.5 12 3l8 4.5v9L12 21l-8-4.5v-9Z", "M4 7.5 12 12l8-4.5M12 12v9"] },
  classes: { d: ["M8 6h12M8 12h12M8 18h12", "M4 6h.01M4 12h.01M4 18h.01"] },
  // v0.21.0: planned work. A clock face with a wrench at its centre would be two marks
  // fighting at 20px, so it is a clock whose hands are set and a bracket around the hour
  // that is spoken for — the window, drawn as a window.
  maintenance: { d: ["M12 7.5V12l3 2", "M6.5 3.5 4 6M17.5 3.5 20 6"],
                 c: [[12, 13, 8]] },
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
  // ADR #454: the NOC's credential vault — a padlock, because what it holds opens things.
  vault: { d: ["M6 10.5h12v9.5H6v-9.5Z", "M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3", "M12 14.5v2"] },
  // ADR #459: a case — a folder with a tab, because a case is the file the NOC keeps.
  cases: { d: ["M3.5 7.5v11h17v-9H11l-2-2H3.5Z", "M7.5 13h9", "M7.5 16h6"] },
  // ADR #463: a playbook — a tree that branches, because that is what it is.
  playbook: { d: ["M6 4.5v15", "M6 9h6v6h6", "M12 9V4.5h6"], c: [[6, 19.5, 1.5], [18, 15, 1.5]] },
  // ADR #460: the NOC's clocks — a stopwatch.
  kpi: { d: ["M12 8.5v4.5l3 2", "M10 2.5h4", "M12 2.5v2"], c: [[12, 13, 7.5]] },
  // ADR #468: the NOC's agents — a robot's head, the mark an agent wears unless it picks another
  // below — and its organization, boxes on a tree.
  bot: { d: ["M7 9h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2Z",
             "M12 9V6", "M10 16h4", "M2.5 13.5v2M21.5 13.5v2"],
         c: [[12, 4.8, 1.2], [9.3, 12.8, 0.9], [14.7, 12.8, 0.9]] },
  org: { d: ["M9.5 3.5h5v4h-5v-4Z", "M3.5 16.5h5v4h-5v-4Z", "M15.5 16.5h5v4h-5v-4Z",
             "M12 7.5v4.5M6 16.5V12h12v4.5"] },
  // ADR #470: the NOC's overview — a dashboard's tiles, unlike the Broker's gauge.
  dashboard: { d: ["M4 4h7v8H4V4Z", "M13 4h7v5h-7V4Z", "M13 11h7v9h-7v-9Z", "M4 14h7v6H4v-6Z"] },

  // -- ADR #468: what an agent may wear (`engine/noc/agents.py` ICONS; `shield` is above) -------
  crown: { d: ["M4.5 17 3.5 8l4.5 3.5L12 5.5l4 6L20.5 8l-1 9h-15Z", "M5 20h14"] },
  router: { d: ["M4 13.5h16a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1Z",
                "M7 13.5 5.5 7.5M17 13.5l1.5-6", "M14 16.5h3.5"],
            c: [[6.5, 16.5, 0.6], [9.5, 16.5, 0.6]] },
  network: { d: ["M10.3 10.6 6 7M13.7 10.6 18 7M10.3 13.4 6 17M13.7 13.4 18 17"],
             c: [[12, 12, 2.2], [4.8, 6, 1.8], [19.2, 6, 1.8], [4.8, 18, 1.8], [19.2, 18, 1.8]] },
  wrench: { d: ["M14.5 4a4.5 4.5 0 0 0-4 6.3l-6 6a1.9 1.9 0 0 0 2.7 2.7l6-6A4.5 4.5 0 0 0 19.5 8" +
                "l-2.7 1.6-2.4-1.4V5.6L17.1 4a4.5 4.5 0 0 0-2.6 0Z"] },
  search: { d: ["M15 15l5.5 5.5"], c: [[10.5, 10.5, 6]] },
  chart: { d: ["M4 4v16h16", "M7.5 15l3.5-4.5 3 2.5 5-6.5"] },
  headset: { d: ["M4.5 14v-2a7.5 7.5 0 0 1 15 0v2",
                 "M4.5 14H7v5H6a1.5 1.5 0 0 1-1.5-1.5V14Z",
                 "M19.5 14H17v5h1a1.5 1.5 0 0 0 1.5-1.5V14Z", "M17 19c0 1.1-1.8 2-4.5 2"] },
  globe: { d: ["M3.5 12h17",
               "M12 3.5c2.3 2.4 3.5 5.2 3.5 8.5s-1.2 6.1-3.5 8.5c-2.3-2.4-3.5-5.2-3.5-8.5" +
               "s1.2-6.1 3.5-8.5Z"],
           c: [[12, 12, 8.5]] },
  cpu: { d: ["M7 7h10v10H7V7Z", "M10 10h4v4h-4v-4Z",
             "M9.5 3.5V7M14.5 3.5V7M9.5 17v3.5M14.5 17v3.5M3.5 9.5H7M3.5 14.5H7M17 9.5h3.5" +
             "M17 14.5h3.5"] },
  bolt: { d: ["M13 3 5 13.5h6L10 21l8-10.5h-6L13 3Z"] },

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
  // Four, and each replaces a word the control now says for itself. The chain is the situation
  // permalink, which read `link` beside a `#41` an operator could already see; the bell is the
  // warnings the banner strip used to spend a row on; the pulse is the health control; and the
  // panel is the sidebar's collapse.
  link: { d: ["M10 13.5a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-1 1",
              "M14 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.5 3.5 0 0 0 5 5l1-1"] },
  bell: { d: ["M6 9.5a6 6 0 0 1 12 0c0 4 1.5 5.5 1.5 5.5h-15S6 13.5 6 9.5Z",
              "M10 18.5a2 2 0 0 0 4 0"] },
  pulse: { d: ["M3 12h3.5l2-5 3.5 10 2.5-5H21"] },
  panel: { d: ["M4 5.5h16v13H4v-13Z", "M10 5.5v13"] },

  // -- ADR #468: waking an agent and holding it -------------------------------------------
  play: { d: ["M8 5.5v13l10.5-6.5L8 5.5Z"] },
  pause: { d: ["M8.5 5.5v13M15.5 5.5v13"] },

  // -- ADR #470: sending the customer update ----------------------------------------------
  send: { d: ["M20.5 3.5 10 14", "M20.5 3.5 14 20.5l-4-6.5-6.5-4 17-6.5Z"] },

  // -- ADR #471: what a credential is, at a glance — one mark per kind of access -------------
  // SSH: a prompt; a key: its bow and bit; SNMP: a device answering in waves; an API: brackets;
  // SFTP: two arrows crossing; SMTP: an envelope; an AI provider: a spark; anything else: dots.
  terminal: { d: ["M3.5 5.5h17v13h-17v-13Z", "M7 10l2.5 2L7 14", "M12 14.5h4.5"] },
  key: { d: ["M11.2 12.8 20 4M16.5 7.5l2 2M14.5 9.5l1.5 1.5"], c: [[7.5, 16.5, 4]] },
  signal: { d: ["M8.5 15.5a5 5 0 0 1 0-7M15.5 8.5a5 5 0 0 1 0 7", "M5.5 18.5a9 9 0 0 1 0-13" +
                "M18.5 5.5a9 9 0 0 1 0 13"], c: [[12, 12, 1.5]] },
  code: { d: ["M8.5 7 3.5 12l5 5", "M15.5 7l5 5-5 5", "M13.5 4.5l-3 15"] },
  transfer: { d: ["M4 8.5h13.5M14 5l3.5 3.5L14 12", "M20 15.5H6.5M10 12l-3.5 3.5L10 19"] },
  mail: { d: ["M3.5 6h17v12h-17V6Z", "M3.5 6.5 12 13l8.5-6.5"] },
  sparkle: { d: ["M12 3.5c.6 4.4 2.7 6.5 7 7-4.3.5-6.4 2.6-7 7-.6-4.4-2.7-6.5-7-7 4.3-.5 6.4-2.6 7-7Z",
                 "M18.5 3v3M17 4.5h3"] },
  more: { d: [], c: [[6, 12, 1.2], [12, 12, 1.2], [18, 12, 1.2]] },
  external: { d: ["M13.5 4.5h6v6", "M19.5 4.5 11 13", "M17 14v5.5H4.5V7H10"] },

  // -- ADR #473: the customers the NOC serves, and the four parts of each one's book ----------
  // A customer: a building. Its instructions: a book; its workbook: a handset; its documents: a
  // page; its circuits: two ports and the line between them. A document's own type: a page, a
  // sheet's grid, a picture. Moving a file: an arrow up into a tray, an arrow down out of it.
  building: { d: ["M3.5 20.5h17", "M5.5 20.5v-16h8.5v16", "M14 9.5h4.5v11", "M8.5 8h2.5M8.5 11.5h2.5" +
                  "M8.5 15h2.5"] },
  book: { d: ["M5 5.5a2 2 0 0 1 2-2h12v14H7a2 2 0 0 0-2 2v-14Z", "M5 19.5a2 2 0 0 0 2 2h12v-4",
              "M9 7.5h6M9 11h4"] },
  phone: { d: ["M6 3.5h3l1.5 4-2 1.3a10.5 10.5 0 0 0 6.7 6.7l1.3-2 4 1.5v3a2 2 0 0 1-2 2A16 16 0 0 1" +
               " 4 5.5a2 2 0 0 1 2-2Z"] },
  doc: { d: ["M6 3.5h8l4 4v13H6v-17Z", "M14 3.5v4h4", "M9 12.5h6M9 16h6"] },
  sheet: { d: ["M4 4.5h16v15H4v-15Z", "M4 9.5h16M4 14.5h16M10 4.5v15"] },
  image: { d: ["M4 5h16v14H4V5Z", "M4 16l5-5 4 4 2.5-2.5L20 17"], c: [[15.5, 9, 1.5]] },
  circuit: { d: ["M3 12h4.5M16.5 12H21", "M7.5 9h3v6h-3V9Z", "M13.5 9h3v6h-3V9Z", "M10.5 12h3"] },
  upload: { d: ["M12 15V4M7.5 8.5 12 4l4.5 4.5", "M4.5 14.5v5h15v-5"] },
  download: { d: ["M12 4v11M7.5 10.5 12 15l4.5-4.5", "M4.5 14.5v5h15v-5"] },
};

/** Every name this module can draw. Read by `tests/ui/test_icons.py`, never for dispatch. */
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
