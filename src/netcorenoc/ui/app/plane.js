/* **Broker | NOC** — the two halves of the product, switched from the top bar (ADR #454).
 *
 * The **Broker** is phase 1: traps in, situations out, the models that decide, the inventory. The
 * **NOC** is phase 2: working the situations that reach the operations centre, beginning with the
 * credentials people and agents use to reach equipment. The switch changes what the sidebar offers;
 * it changes no permission — the router still decides every screen from the capability set alone.
 *
 * ## The plane is the route's, not a preference
 *
 * Which plane is "on" is read from the screen being shown (`registry.planeOf`), so a link into a
 * NOC screen lands in the NOC and the browser's back button crosses planes like any other step.
 * Choosing a plane goes back to the last screen seen in it, or to its first reachable screen —
 * held in memory only: this console keeps nothing in browser storage (F2).
 *
 * ## Absent when there is nothing to switch to
 *
 * A principal who can reach no NOC screen (every viewer, today) sees no switch at all — not a
 * greyed half. Absence is absence, the rule the sidebar has kept since v0.13.0.
 */

import { html, cx } from "./dom.js";
import { reachableViews } from "./router.js";

/** The two halves of the product, in the order the top bar shows them. Kept out of `registry.js`,
 *  whose entries are views and are parsed as such by `tests/ui/test_security_ui.py`. */
export const PLANES = [
  {
    key: "broker", label: "Broker",
    title: "Phase 1 — receives traps, correlates them into situations, trains the models that " +
      "do it, and keeps the inventory.",
  },
  {
    key: "noc", label: "NOC",
    title: "Phase 2 — works the situations that reach the NOC: the credentials people and " +
      "agents use to reach equipment, and what they do with them.",
  },
];

/** The planes this principal can reach at least one screen of, in display order. */
export function reachablePlanes(capabilities) {
  return PLANES.filter((plane) => reachableViews(capabilities, plane.key).length > 0);
}

/** Where choosing `plane` goes: the last screen seen in it, else its first reachable one. */
export function landing(capabilities, plane, last) {
  if (last && last[plane]) return last[plane];
  const first = reachableViews(capabilities, plane)[0];
  return first ? `#/${first.id}` : null;
}

export function PlaneSwitch({ capabilities, plane, last, onNavigate }) {
  const planes = reachablePlanes(capabilities);
  if (planes.length < 2) return null;
  return html`<nav class="plane-switch" aria-label="Product area">
    ${planes.map((p) => {
      const on = p.key === plane;
      const to = landing(capabilities, p.key, on ? null : last);
      return html`<a key=${p.key} class=${cx("plane", `plane-${p.key}`, on && "plane-on")}
        href=${to} title=${p.title} aria-current=${on ? "true" : null}
        onClick=${(event) => { event.preventDefault(); if (to) onNavigate(to); }}>${p.label}</a>`;
    })}
  </nav>`;
}
