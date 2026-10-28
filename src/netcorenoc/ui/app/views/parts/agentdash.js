/* An agent's Overview tab (ADR #468): Paperclip's agent dashboard, from what the page read.
 *
 * Its latest run first — what woke it, what it did, what it cost — then four charts over the days
 * the page asked for (runs per day by how they ended, its cases by priority and by status, how
 * many of its runs succeeded), its cases, its place in the chain, and its month's spend.
 *
 * Every chart is drawn from the arrays the server sent, with the console's own chart rules: a day
 * before the agent existed is not drawn as a zero, and a rate with nothing ended reads as a dash.
 */

import { html, cx } from "../../dom.js";
import { relative, timeTitle } from "../../format.js";
import { Series } from "../../charts.js";
import { Bars } from "../../compare.js";
import { RunState, Tier, money } from "./nocui.js";
import { span } from "./caseclock.js";

const DAY = 86400;
const ENDED_BAD = new Set(["failed", "timed_out"]);
const PRIORITY_TONE = { critical: "alarm", major: "warn", minor: null };

/** The days the agent existed within the window, oldest first, as UTC `YYYY-MM-DD`. */
function days(window, createdAt, now) {
  const first = Math.max(now - (window - 1) * DAY, createdAt || now);
  const out = [];
  for (let t = Math.floor(first / DAY) * DAY; t <= now; t += DAY) {
    out.push(new Date(t * 1000).toISOString().slice(0, 10));
  }
  return out;
}

function LatestRun({ run, onRuns }) {
  if (!run) {
    return html`<section class="panel-block latest-run"><h3>Latest run</h3>
      <p class="muted">It has not run yet. It runs when a case is routed to it, on its heartbeat, or
        when someone runs it now.</p></section>`;
  }
  const took = run.ended_at && run.started_at ? span(run.ended_at - run.started_at) : null;
  return html`<section class="panel-block latest-run" aria-label="Latest run">
    <div class="latest-run-head">
      <h3>Latest run <span class="mono muted">#${run.id}</span></h3>
      <${RunState} status=${run.status} label=${run.status_label} />
    </div>
    <p class="muted latest-run-facts">${run.trigger_label}${run.situation_id
      ? html` · case <a href=${`#/cases/${run.situation_id}`}>#${run.situation_id}</a>` : null}${" · "}<span
        title=${timeTitle(run.started_at || run.queued_at)}>${relative(run.started_at || run.queued_at)}</span>${
      `${took ? ` · took ${took}` : ""} · ${run.tool_calls} tool call${run.tool_calls === 1 ? "" : "s"} · ${money(run.cost_cents)}`}</p>
    ${run.summary ? html`<p class="run-summary">${run.summary}</p>` : null}
    ${run.error ? html`<p class="err">${run.error}</p>` : null}
    <button type="button" class="linkish" onClick=${onRuns}>Every run and its steps →</button>
  </section>`;
}

function RunActivity({ activity, agent, now }) {
  const shown = days(activity.days, agent.created_at, now);
  const per = (keep) => shown.map((day) => activity.runs
    .filter((r) => r.day === day && keep(r.status)).reduce((n, r) => n + Number(r.n), 0));
  return html`<${Series} title="Runs per day" mark="column"
    series=${[
      { name: "succeeded", tone: "quiet", values: per((s) => s === "succeeded") },
      { name: "failed", tone: "alarm", values: per((s) => ENDED_BAD.has(s)) },
      { name: "other", tone: "warn", values: per((s) => !ENDED_BAD.has(s) && s !== "succeeded") },
    ]}
    labels=${shown.map((d) => d.slice(5))} source="the agent's runs"
    span=${`${shown.length} day${shown.length === 1 ? "" : "s"}, UTC`} />`;
}

function CaseBars({ activity, by }) {
  const totals = {};
  for (const row of activity.cases) totals[row[by]] = (totals[row[by]] || 0) + Number(row.n);
  const keys = by === "priority" ? ["critical", "major", "minor"] : Object.keys(totals);
  const rows = keys.filter((k) => by === "priority" || totals[k]).map((k) => ({
    key: k, label: by === "priority" ? k[0].toUpperCase() + k.slice(1) : k.replace(/_/g, " "),
    value: totals[k] || 0, tone: by === "priority" ? PRIORITY_TONE[k] : null,
  }));
  const any = rows.some((r) => r.value);
  return html`<${Bars} title=${by === "priority" ? "Its cases by priority" : "Its cases by status"}
    rows=${any ? rows : []} source="cases it held or ran on"
    span=${`${activity.days} days`} note=${any ? null : "no case yet"} />`;
}

function SuccessRate({ activity }) {
  let ok = 0;
  let ended = 0;
  for (const r of activity.runs) {
    if (r.status === "succeeded") ok += Number(r.n);
    if (r.status === "succeeded" || ENDED_BAD.has(r.status)) ended += Number(r.n);
  }
  const rate = ended ? Math.round((100 * ok) / ended) : null;
  return html`<section class="chart-block success-rate">
    <h4 class="chart-title">Runs that succeeded</h4>
    <p class="success-figure">${rate == null ? "—" : `${rate}%`}</p>
    <span class="gauge-track" aria-hidden="true"><span class=${cx("gauge-fill", rate != null && rate < 80 && "gauge-low")}
      style=${`width:${rate || 0}%`}></span></span>
    <p class="chart-caption"><span class="chart-source">${rate == null
      ? `no run ended in ${activity.days} days` : `${ok} of ${ended} ended runs`}</span></p>
  </section>`;
}

function Cases({ cases }) {
  if (!cases.length) return html`<p class="muted">No case has been routed to it yet.</p>`;
  return html`<div class="table-scroll"><table class="data compact-table">
    <thead><tr><th scope="col">Case</th><th scope="col">Priority</th><th scope="col">Status</th>
      <th scope="col">Level</th><th scope="col" class="wide-only">Updated</th></tr></thead>
    <tbody>${cases.slice(0, 10).map((c) => html`<tr key=${c.situation_id}>
      <td><a href=${`#/cases/${c.situation_id}`}><span class="mono">#${c.situation_id}</span>${" "}${c.title
        || c.situation_name || "Untitled"}</a></td>
      <td><span class=${cx("case-priority", `prio-${c.priority}`)}>${c.priority}</span></td>
      <td>${c.status === "queue" ? "NOC queue" : c.status.replace(/_/g, " ")}</td><td><${Tier} tier=${c.tier} /></td>
      <td class="wide-only" title=${timeTitle(c.updated_at)}>${relative(c.updated_at)}</td>
    </tr>`)}</tbody></table></div>`;
}

function Chain({ agent }) {
  return html`<section class="panel-block chain-panel" aria-label="Chain of command">
    <h3>Chain of command</h3>
    <ol class="chain">
      <li class="chain-people">The NOC team</li>
      ${[...agent.chain_of_command].reverse().map((b) => html`<li key=${b.id}>
        <a href=${`#/agents/${b.id}`}>${b.name}</a></li>`)}
      <li class="chain-self" aria-current="true">${agent.name}</li>
    </ol>
    <p class="muted">${agent.reports.length
      ? `${agent.reports.length} agent${agent.reports.length === 1 ? " reports" : "s report"} to it.`
      : "Nobody reports to it."}</p>
  </section>`;
}

function Spend({ agent }) {
  const budget = agent.budget_month_cents;
  const used = budget ? Math.min(100, (100 * agent.spent_month_cents) / budget) : null;
  return html`<section class="panel-block spend-panel" aria-label="This month's spend">
    <h3>This month</h3>
    <p class="success-figure">${money(agent.spent_month_cents)}</p>
    ${budget ? html`<span class="gauge-track" aria-hidden="true"><span
        class=${cx("gauge-fill", used >= 90 && "gauge-low")} style=${`width:${used.toFixed(1)}%`}></span></span>
      <p class="muted">of a ${money(budget)} budget; reached, it is paused.</p>`
      : html`<p class="muted">No budget is set.</p>`}
    ${agent.adapter !== "claude" ? html`<p class="hint">It runs elsewhere: what it spends there is not counted here.</p>` : null}
  </section>`;
}

export function AgentOverview({ d, onRuns }) {
  const now = Date.now() / 1000;
  return html`<div class="agent-overview">
    <div class="agent-overview-main">
      <${LatestRun} run=${d.runs[0]} onRuns=${onRuns} />
      <div class="chart-grid agent-charts">
        <${RunActivity} activity=${d.activity} agent=${d.agent} now=${now} />
        <${SuccessRate} activity=${d.activity} />
        <${CaseBars} activity=${d.activity} by="priority" />
        <${CaseBars} activity=${d.activity} by="status" />
      </div>
      <section class="agent-cases" aria-label="Its cases"><h3>Its cases</h3><${Cases} cases=${d.cases} /></section>
    </div>
    <aside class="agent-overview-side">
      <${Chain} agent=${d.agent} />
      <${Spend} agent=${d.agent} />
      ${d.agent.capabilities ? html`<section class="panel-block"><h3>What it can do</h3>
        <p class="case-text">${d.agent.capabilities}</p></section>` : null}
    </aside>
  </div>`;
}
