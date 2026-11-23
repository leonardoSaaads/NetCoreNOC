/* A broker's Learning: its labels, its judge, its decider and its site model (ADR #495).
 *
 * Each broker learns from its own network: the groupings its operators judged are its labels, its
 * judge ranks the league on those labels alone, and the model that decides its links is its own —
 * model A in one broker, model B in another. Four tabs, in the order an operator works:
 *
 *   * **To judge** — what waits for a verdict in this network (the store follows this broker);
 *   * **Models** — who decides here, and an admin's pin of one member for this broker only;
 *   * **Judge** — every member measured, and the paired comparisons on this broker's labels;
 *   * **Site training** — adapting the shipped model to this broker's labels.
 *
 * The tab is in the address (`?view=`), beside each tab's own `?tab=`.
 */

import { html, cx } from "../../dom.js";
import { Icon } from "../../icons.js";
import { can } from "../../session.js";
import { Learning } from "../learning.js";
import { Promotion } from "../promotion.js";
import { Correlation } from "./decider.js";
import { SearchPanel } from "./searchpanel.js";

const VIEWS = [
  ["judge", "To judge", "labelling", ["situations.read"]],
  ["models", "Models", "scorer", ["scorer.read"]],
  ["league", "Judge", "promotion", ["promotion.read", "model.read"]],
  ["training", "Site training", "cpu", ["model.read"]],
];

export function BrokerLearning({ brokerId, base, query, navigate }) {
  const allowed = VIEWS.filter(([, , , caps]) => caps.every((c) => can(c)));
  const asked = query && query.get("view");
  const current = allowed.find(([key]) => key === asked) || allowed[0];
  if (!current) return null;
  const key = current[0];
  const go = (view) => navigate(`${base}?view=${view}`);
  const judgeHref = `${base}?view=league`;
  const here = `${base}?view=${key}`;
  return html`<div class="brk-learning">
    <div class="access-tabs" role="tablist" aria-label="Learning in this broker">
      ${allowed.map(([view, label, icon]) => html`<button type="button" key=${view} role="tab"
        aria-selected=${view === key} class=${cx("access-tab", view === key && "on")}
        onClick=${() => go(view)}><${Icon} name=${icon} /> ${label}</button>`)}
    </div>
    ${key === "judge" ? html`<${Learning} key="judge" brokerId=${brokerId} base=${here} judgeHref=${judgeHref}
        query=${query} navigate=${navigate} />`
      : key === "models" ? html`<${Correlation} key="models" brokerId=${brokerId} judgeHref=${judgeHref} />`
        : key === "league" ? html`<${Promotion} key="league" brokerId=${brokerId} base=${here}
            query=${query} navigate=${navigate} params=${[]} />`
          : html`<${SearchPanel} key="training" brokerId=${brokerId} />`}
  </div>`;
}
