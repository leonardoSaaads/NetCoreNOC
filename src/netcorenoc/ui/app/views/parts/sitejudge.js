/* Judge → this site: **this appliance's labels**, and what the search made of them (v0.26.0).
 *
 * The site block answers one question — *is a model adapted to these labels better here than the
 * shipped one?* — with a paired comparison on the newest labelled incidents (held out by time),
 * and a do-no-harm check on the shipped benchmark. The sufficiency bars say how far the labels are
 * from a comparison that can say anything; they stay, and they are one chart among the others.
 *
 * Every caption here says **site data**. Nothing on this block shares an axis with the generated
 * numbers above it: a site's forty labels and a generator's hundred thousand pairs are different
 * claims, and one chart holding both would invite reading them as one.
 */

import { html } from "../../dom.js";
import { Bars } from "../../compare.js";
import { Versus } from "../../modelcharts.js";
import { SearchCharts } from "./shippedjudge.js";

const SITE = "site data (this appliance's labels)";
const FLOOR_LABELS = {
  incidents: "labelled incidents",
  test_incidents: "incidents in the newest 30 %",
  negative_bags: "splits asserting a negative",
  label_days: "distinct days labelled",
};

export function SiteJudge({ site }) {
  if (!site) return null;
  const stats = site.stats || {};
  const floors = site.floors || {};
  const latest = (site.runs || [])[0];
  const judgement = latest && latest.judgement;
  const trials = (site.trials || []).map((t) => ({ ...t, valid_loss: t.valid_loss }));
  return html`<section class="judge-block" aria-labelledby="judge-site">
    <header class="judge-head">
      <h3 id="judge-site">This site</h3>
      <span class="dataset-chip dataset-site">site data</span>
      <span class=${site.unmet && site.unmet.length ? "hint" : "ok-note"}>${site.unmet && site.unmet.length
        ? `${site.unmet.length} floor(s) unmet — a comparison would be INSUFFICIENT_EVIDENCE`
        : "every floor met — a search can be judged"}</span>
    </header>
    <div class="judge-grid">
      <${Bars} title="Sufficiency" max=${1}
        rows=${Object.entries(FLOOR_LABELS).map(([k, label]) => {
          const have = stats[k] ?? 0;
          const need = floors[k] ?? 1;
          return { key: k, label, face: `${label} — ${have} of ${need}`,
                   value: Math.min(have / need, 1), tone: have >= need ? null : "warn" };
        })}
        source=${SITE} span=${`${stats.rows ?? 0} labelled pairs`}
        note="floors from the paired comparison's power (ADR #411)" />
      ${judgement ? html`<${Judgement} j=${judgement} run=${latest} />`
        : html`<section class="chart-block"><h4 class="chart-title">Latest comparison</h4>
            <p class="hint">No search has been judged on this appliance. Settings → Search starts one.</p>
            <p class="judge-caption">${SITE}</p></section>`}
    </div>
    ${trials.length ? html`<h4 class="judge-sub">The latest search on this site</h4>
      <${SearchCharts} search=${{ trials }} fit=${null} source=${`${SITE}, search #${latest.id}`} />` : null}
    ${site.labelled_pairs_without_features ? html`<p class="hint">${site.labelled_pairs_without_features}
      labelled pair(s) predate v0.26.0 and carry no feature vector; they count toward nothing here.</p>` : null}
  </section>`;
}

function Judgement({ j, run }) {
  const d = j.difference || {};
  const b = j.benchmark || {};
  return html`<section class="chart-block">
    <h4 class="chart-title">Latest comparison: <b class="mono">${j.verdict}</b></h4>
    <p>${j.reason}</p>
    <div class="versus-row">
      ${d.mean != null ? html`<${Versus} label="log-loss change, site − shipped"
        model=${{ point: d.mean, low: d.low, high: d.high }} lower=${true} />` : null}
      ${b.shipped != null ? html`<${Versus} label="benchmark log loss (site model)"
        model=${{ point: b.site, low: b.site, high: b.site }} formula=${null} lower=${true} />` : null}
    </div>
    <p class="judge-caption">${SITE}, search #${run.id}; ${d.incidents ?? 0} newest incidents, paired;
      95 % bootstrap over incidents. ${b.shipped != null
        ? `Do-no-harm: shipped ${b.shipped.toFixed(4)} on ${b.rows} generated benchmark pairs, margin ${b.margin}.`
        : ""}</p>
  </section>`;
}
