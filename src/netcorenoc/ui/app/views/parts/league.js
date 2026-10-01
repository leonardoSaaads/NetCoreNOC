/* Judge → the league: who decides links, why, and how every model is doing (v0.27.0).
 *
 * The screen answers four questions, in the order an operator asks them:
 *
 *   1. **Who decides, and why?** The champion, the judge's reason, and — in two short paragraphs —
 *      the two loops (ADR #423): the fast loop groups every trap with the champion while the
 *      challengers score the same pairs in shadow; the slow loop re-ranks every model on this
 *      site's labels and replaces the champion only when the evidence says so.
 *   2. **How do the models compare?** The league table in the judge's order, with each model's
 *      role, then either every model on shared axes or one model's ten charts.
 *   3. **What do this site's labels say?** The paired comparisons, as intervals around zero.
 *   4. **What happened live?** The challengers' agreement with the champion, and every switch.
 *
 * Each block names its dataset — generated, site or live — and none shares an axis with another.
 */

import { html, Component, cx } from "../../dom.js";
import { Bars } from "../../compare.js";
import { Forest } from "../../modelcharts.js";
import { DataTable, TimeCell, cell } from "../../widgets.js";
import { ModelCharts, SUITES } from "./leaguecharts.js";
import { CompareCharts, meanF1, toneOf } from "./leaguecompare.js";

const SITE = "site data (this appliance's labels)";
const ROLE = { champion: "deciding", challenger: "in shadow", ineligible: "too slow" };
const f3 = (v) => (v == null ? "—" : Number(v).toFixed(3));

export class LeagueBoard extends Component {
  constructor(props) {
    super(props);
    // `#/promotion?model=<kind or ref>` opens one model's charts: a link an operator can share.
    const asked = (props.judge.models || []).find((m) => m.ref === props.initial || m.kind === props.initial);
    this.state = { view: asked ? asked.ref : "compare" };
  }

  render({ judge }, { view }) {
    const models = judge.models || [];
    const table = judge.members || [];
    const picked = models.find((m) => m.ref === view);
    return html`<div class="league">
      <${WhoDecides} judge=${judge} />
      <section class="judge-block" aria-labelledby="league-table">
        <header class="judge-head">
          <h3 id="league-table">The league</h3>
          <span class="dataset-chip dataset-generated">generated data</span>
          <span class="dataset-chip dataset-site">site data</span>
        </header>
        <p class="hint">Ranked by the judge's offline score: the mean pairwise F1 over the five
          suites below, measured on streams no model trained or tuned on. <b>Site Δ</b> is how much
          better (negative) or worse each challenger's log loss is than the champion's on this
          site's labels, with its 95 % interval.</p>
        <${LeagueTable} rows=${table} comparisons=${(judge.judgement || {}).comparisons || []} />
        ${(judge.refused || []).map((r) => html`<p class="err" key=${r.kind}>The ${r.kind} model was
          refused: ${r.reason}</p>`)}
      </section>
      <section class="judge-block" aria-labelledby="league-charts">
        <header class="judge-head">
          <h3 id="league-charts">Learning and comparison</h3>
          <span class="dataset-chip dataset-generated">generated data</span>
        </header>
        <div class="league-switch" role="tablist" aria-label="Which model's charts">
          <button type="button" role="tab" aria-selected=${view === "compare"}
            class=${cx("access-tab", view === "compare" && "on")}
            onClick=${() => this.setState({ view: "compare" })}>Compare all</button>
          ${models.map((m, i) => html`<button type="button" role="tab" key=${m.ref}
            aria-selected=${view === m.ref} class=${cx("access-tab", view === m.ref && "on")}
            onClick=${() => this.setState({ view: m.ref })}>
            <i class=${cx("chart-swatch", `chart-${toneOf(i)}`)} aria-hidden="true"></i>${m.name}</button>`)}
        </div>
        ${picked ? html`<${ModelCharts} key=${picked.ref} model=${picked} />`
          : html`<${CompareCharts} models=${models} />`}
      </section>
      <${SiteEvidence} judge=${judge} models=${models} />
      <${LiveShadow} judge=${judge} models=${models} />
      <${Decisions} rows=${judge.decisions || []} models=${models} />
    </div>`;
  }
}

function nameOf(models, ref) {
  const m = models.find((x) => x.ref === ref);
  return m ? m.name : ref;
}

function WhoDecides({ judge }) {
  const champ = judge.champion;
  const latest = (judge.decisions || [])[0];
  return html`<section class="judge-block league-now" aria-labelledby="league-now">
    <header class="judge-head"><h3 id="league-now">Who decides</h3></header>
    ${champ && !judge.fallback
      ? html`<p class="league-champion"><b>${champ.name}</b> is deciding every link
          ${judge.pinned ? html`<span class="badge">pinned by an admin</span>` : null}</p>
        ${latest ? html`<p class="hint">${latest.reason} — <${TimeCell} ts=${latest.at} />.</p>` : null}`
      : html`<p class="err" role="alert">No model could be loaded, so the built-in formula is
          grouping as a fail-safe. ${(judge.warnings || []).join(" ")}</p>`}
    <div class="league-loops">
      <div class="league-loop">
        <h4>Fast loop — every trap</h4>
        <p>The champion scores each new alarm against its candidates and places it. A situation an
          operator has <b>confirmed (Open)</b> is never changed by a model: alarms the model would
          add wait in <b>Pending</b> for an operator to accept or reject. Every other model scores a
          sample of the same pairs in shadow.</p>
      </div>
      <div class="league-loop">
        <h4>Slow loop — every five minutes</h4>
        <p>The judge re-scores every model on the labels operators have produced here — confirms,
          splits, moves, merges and answers to proposals — and replaces the champion only when a
          challenger's advantage has a 95 % interval entirely below zero. There is no count to wait
          for: models decide from the first trap, and labels only re-order them.</p>
      </div>
    </div>
    <p class="hint">An admin can pin a model in <a href="#/settings?tab=correlation">Settings →
      Models</a>.</p>
  </section>`;
}

function LeagueTable({ rows, comparisons }) {
  const byRef = Object.fromEntries(comparisons.map((c) => [c.challenger, c]));
  return html`<${DataTable} kind="league" caption="The league, in the judge's order"
    columns=${[
      { key: "rank", label: "#", numeric: true },
      { key: "model", label: "model" },
      { key: "role", label: "role" },
      { key: "score", label: "score", numeric: true },
      ...Object.entries(SUITES).map(([k, label]) => ({ key: k, label, numeric: true })),
      { key: "latency", label: "µs / pair", numeric: true },
      { key: "site", label: "site Δ (95 %)" },
    ]}
    rows=${rows.map((r, i) => {
      const c = byRef[r.ref];
      return {
        key: r.ref,
        cells: {
          rank: String(r.rank),
          model: html`<span class="league-model"><i class=${cx("chart-swatch", `chart-${toneOf(i)}`)}
            aria-hidden="true"></i>${r.name}</span>`,
          role: html`<span class=${cx("league-role", `league-${r.role}`)}>${ROLE[r.role] || r.role}</span>`,
          score: f3(r.score),
          ...Object.fromEntries(Object.keys(SUITES).map((k) => [k, f3((r.suites || {})[k])])),
          latency: r.latency_us == null ? "—" : r.latency_us.toFixed(1),
          site: c && c.mean != null
            ? `${c.mean > 0 ? "+" : ""}${f3(c.mean)} [${f3(c.low)}, ${f3(c.high)}] ${c.verdict}`
            : r.role === "champion" ? "the reference" : "no labels yet",
        },
      };
    })} />`;
}

function SiteEvidence({ judge, models }) {
  const site = judge.site || {};
  const ev = site.evidence || {};
  const comparisons = (judge.judgement || {}).comparisons || [];
  const proposals = site.proposals || {};
  return html`<section class="judge-block" aria-labelledby="league-site">
    <header class="judge-head"><h3 id="league-site">This site</h3>
      <span class="dataset-chip dataset-site">site data</span></header>
    <p class="hint">${ev.incidents ? `${ev.incidents} labelled incident(s), ${ev.rows} labelled pairs.`
      : "No labels yet. Confirming, splitting, moving, merging and answering proposals all label."}
      Labels never gate a model; they re-order the league.</p>
    <div class="judge-grid">
      <${Forest} title="Challenger − champion, log loss per incident"
        rows=${comparisons.map((c) => ({ label: nameOf(models, c.challenger), mean: c.mean,
          low: c.low, high: c.high, verdict: c.verdict }))}
        unit="nats" source=${SITE} n=${`${(comparisons[0] || {}).incidents ?? 0} incidents, paired`}
        note="left of zero: the challenger is better here; the whole interval must be left to switch" />
      <${Bars} title="Proposals accepted by operators" max=${100} unit="%"
        rows=${Object.entries(proposals).map(([ref, v]) => ({ key: ref, label: nameOf(models, ref),
          face: `${nameOf(models, ref)} — ${v.accept} of ${v.accept + v.reject}`,
          value: v.accept + v.reject ? (100 * v.accept) / (v.accept + v.reject) : null, tone: null }))}
        source=${SITE} span="answers to Pending proposals, per proposing model"
        note="the fast loop's own report card" />
    </div>
  </section>`;
}

function LiveShadow({ judge, models }) {
  const shadow = judge.shadow || {};
  const rows = Object.entries(shadow.challengers || {});
  return html`<section class="judge-block" aria-labelledby="league-live">
    <header class="judge-head"><h3 id="league-live">Live shadow</h3>
      <span class="dataset-chip dataset-live">live traffic</span></header>
    <${Bars} title="How often each challenger agrees with the champion" max=${100} unit="%"
      rows=${rows.map(([ref, t]) => ({ key: ref, label: nameOf(models, ref),
        value: t.agreement == null ? null : 100 * t.agreement, tone: null }))}
      source="live traffic since the process started" span=${`${shadow.sampled ?? 0} of
        ${shadow.activations ?? 0} activations sampled, up to ${shadow.sample_pairs ?? 12} pairs each`}
      note="agreement is not accuracy — labels decide who is right" />
  </section>`;
}

function Decisions({ rows, models }) {
  if (!rows.length) return null;
  return html`<section class="judge-block" aria-labelledby="league-decisions">
    <header class="judge-head"><h3 id="league-decisions">Champion history</h3></header>
    <${DataTable} kind="decisions" columns=${[
      { key: "when", label: "when" }, { key: "champion", label: "champion" },
      { key: "previous", label: "previous" }, { key: "reason", label: "why" },
    ]} rows=${rows.map((r) => ({
      key: r.id,
      cells: {
        when: cell(html`<${TimeCell} ts=${r.at} />`),
        champion: nameOf(models, r.champion),
        previous: r.previous ? nameOf(models, r.previous) : "—",
        reason: r.pinned ? `pinned — ${r.reason}` : r.reason,
      },
    }))} />
  </section>`;
}

export { meanF1 };
