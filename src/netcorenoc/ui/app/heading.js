/* The work area's heading: the screen's name, one line on what it is for, and its control.
 *
 * Split from `shell.js` (ADR #494), where it sat as a helper, when a screen's heading began to
 * depend on its address: a broker's screens are titled with the broker's name and the screen —
 * "Claro Nordeste · Network graph" — from the view's `heading(params)`, and the Overview draws its
 * broker at the heading's right (`aside`), as a Grafana dashboard draws its variables.
 *
 * The heading is `tabindex="-1"`: the shell moves focus to it on every route change, so a keyboard
 * operator lands on the screen they asked for rather than back in the navigation.
 */

import { html, cx } from "./dom.js";

export function WorkHeading({ decision, headingRef, navigate: go }) {
  const own = decision.kind === "view" && decision.view.heading
    ? decision.view.heading(decision.params) : null;
  const title = own ? own.title
    : decision.kind === "view" ? decision.view.label
      : decision.kind === "refused" ? decision.view.label
        : "Not found";
  const summary = own ? own.summary : decision.kind === "view" ? decision.view.summary : null;
  const Aside = decision.kind === "view" ? decision.view.aside : null;
  return html`<div class=${cx("work-heading", Aside && "has-aside")}>
    <div class="work-heading-text">
      <h1 tabindex="-1" ref=${headingRef}>${title}</h1>
      ${summary ? html`<p class="work-summary">${summary}</p>` : null}
    </div>
    ${Aside ? html`<div class="work-heading-aside"><${Aside} query=${decision.query} navigate=${go} /></div>` : null}
  </div>`;
}
