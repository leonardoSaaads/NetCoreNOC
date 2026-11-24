/* An agent's Overview tab (ADR #468): Paperclip's agent dashboard, from what the page read.
 *
 * Its latest run first — what woke it, what it did, what it cost — then four charts over the days
 * the page asked for (runs per day by how they ended, its cases by priority and by status, how
 * many of its runs succeeded), its cases, its place in the chain, and its month's spend.
 *
 * Every chart is drawn from the arrays the server sent, with the console's own chart rules: a day
 * before the agent existed is not drawn as a zero, and a rate with nothing ended reads as a dash.
 *
 * ## ADR #490: Paperclip's shape — four numbers, then two cards
 *
 * Five charts of different heights sat in a grid that aligned none of them: a one-day column chart
 * three bars wide, a percentage alone in a card, two bar lists. Now the four numbers an operator
 * asks first are one strip — runs, success, this month's spend, cases — and the history is two
 * cards of one height: runs per day over the whole window (each day a stacked column; a day before
 * the agent existed is an empty slot, not a zero) and its cases, by priority and by status.
 */

import { html, cx } from "../../../dom.js";
import { relative, timeTitle } from "../../../format.js";
import { Series } from "../../../charts.js";
import { RunState, Tier, money } from "./nocui.js";
import { span } from "./caseclock.js";

const DAY = 86400;
const ENDED_BAD = new Set(["failed", "timed_out"]);

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

/** Its runs, totalled by how they ended. */
function totals(activity) {
  const out = { ok: 0, bad: 0, other: 0 };
  for (const r of activity.runs) {
    const n = Number(r.n);
    if (r.status === "succeeded") out.ok += n;
    else if (ENDED_BAD.has(r.status)) out.bad += n;
    else out.other += n;
  }
  return out;
}

/** The four numbers first: runs, success, this month's spend, its cases. */
function Kpis({ activity, agent, cases }) {
  const t = totals(activity);
  const runs = t.ok + t.bad + t.other;
  const ended = t.ok + t.bad;
  const rate = ended ? Math.round((100 * t.ok) / ended) : null;
  const open = cases.filter((c) => !["resolved", "closed"].includes(c.status));
  const critical = open.filter((c) => c.priority === "critical").length;
  const budget = agent.budget_month_cents;
  return html`<div class="agent-kpis">
    <div class="agent-kpi"><span>Runs · ${activity.days} d</span><b>${runs}</b>
      <span class="agent-split" aria-label=${`${t.ok} succeeded, ${t.bad} failed, ${t.other} other`}>
        ${runs ? [["ok", t.ok], ["bad", t.bad], ["other", t.other]].map(([k, n]) => (n
          ? html`<i key=${k} class=${`agent-${k}`} style=${`flex-grow:${n}`}></i>` : null)) : html`<i class="agent-none"></i>`}</span>
      <small>${t.ok} ok · ${t.bad} failed · ${t.other} other</small></div>
    <div class="agent-kpi"><span>Succeeded</span><b class=${cx(rate != null && rate < 80 && "agent-low")}>${rate == null ? "—" : `${rate}%`}</b>
      <span class="gauge-track" aria-hidden="true"><span class=${cx("gauge-fill", rate != null && rate < 80 && "gauge-low")}
        style=${`width:${rate || 0}%`}></span></span>
      <small>${rate == null ? "no run ended yet" : `${t.ok} of ${ended} ended runs`}</small></div>
    <div class="agent-kpi"><span>This month</span><b>${money(agent.spent_month_cents)}</b>
      ${budget ? html`<span class="gauge-track" aria-hidden="true"><span class="gauge-fill"
        style=${`width:${Math.min(100, (100 * agent.spent_month_cents) / budget).toFixed(1)}%`}></span></span>` : null}
      <small>${budget ? `of a ${money(budget)} budget` : SPEND_WORD[agent.priced] || "not counted here"}</small></div>
    <div class="agent-kpi"><span>Open cases</span><b>${open.length}</b>
      <small>${critical ? html`<span class="case-priority prio-critical">${critical} critical</span>` : `${cases.length} in ${activity.days} d`}</small></div>
  </div>`;
}

const SPEND_WORD = { local: "your hardware · no charge", unpriced: "no price set", published: "published rates", set: "its admin's rate" };

/** Runs per day over the whole window: one stacked column a day; before it existed, an empty slot. */
function RunDays({ activity, agent, now }) {
  const first = Math.floor((now - (activity.days - 1) * DAY) / DAY) * DAY;
  const born = Math.floor((agent.created_at || first) / DAY) * DAY;
  const slots = Array.from({ length: activity.days }, (_, i) => {
    const t = first + i * DAY;
    const day = new Date(t * 1000).toISOString().slice(0, 10);
    const rows = activity.runs.filter((r) => r.day === day);
    const sum = (keep) => rows.filter((r) => keep(r.status)).reduce((n, r) => n + Number(r.n), 0);
    return { day, before: t < born, ok: sum((s) => s === "succeeded"), bad: sum((s) => ENDED_BAD.has(s)),
      other: sum((s) => s !== "succeeded" && !ENDED_BAD.has(s)) };
  });
  const top = Math.max(1, ...slots.map((s) => s.ok + s.bad + s.other));
  return html`<section class="panel-block agent-card" aria-label="Runs per day">
    <header class="agent-card-head"><h3>Runs per day</h3>
      <span class="agent-legend"><i class="agent-ok"></i>succeeded<i class="agent-bad"></i>failed<i class="agent-other"></i>other</span></header>
    <div class="agent-days" role="img" aria-label=${slots.filter((s) => s.ok + s.bad + s.other)
      .map((s) => `${s.day}: ${s.ok} succeeded, ${s.bad} failed, ${s.other} other`).join("; ") || "no runs"}>
      ${slots.map((s) => html`<div key=${s.day} class=${cx("agent-day", s.before && "is-before")}
          title=${s.before ? `${s.day}: before it was hired` : `${s.day}: ${s.ok} ok · ${s.bad} failed · ${s.other} other`}>
        <span class="agent-col">${[["ok", s.ok], ["other", s.other], ["bad", s.bad]].map(([k, n]) => (n
          ? html`<i key=${k} class=${`agent-${k}`} style=${`height:${((100 * n) / top).toFixed(1)}%`}></i>` : null))}</span>
      </div>`)}
    </div>
    <div class="agent-axis"><span>${slots[0].day.slice(5)}</span><span>today, UTC</span></div>
  </section>`;
}

/** Its cases over the window, by priority and by status — two lists in one card. */
function CaseMix({ activity }) {
  const sum = (by) => {
    const out = {};
    for (const row of activity.cases) out[row[by]] = (out[row[by]] || 0) + Number(row.n);
    return out;
  };
  const prio = sum("priority");
  const status = sum("status");
  const list = (rows, tone) => {
    const top = Math.max(1, ...rows.map(([, n]) => n));
    return html`<ul class="agent-mix">${rows.map(([k, n]) => html`<li key=${k}>
      <span>${k.replace(/_/g, " ")}</span>
      <span class="agent-bar"><i class=${tone(k)} style=${`width:${((100 * n) / top).toFixed(1)}%`}></i></span>
      <b>${n}</b></li>`)}</ul>`;
  };
  const statuses = Object.entries(status).filter(([, n]) => n);
  return html`<section class="panel-block agent-card" aria-label="Its cases">
    <header class="agent-card-head"><h3>Its cases</h3><span class="muted">${activity.days} days</span></header>
    ${activity.cases.length ? html`<div class="agent-mixes">
      <div><h4>By priority</h4>${list(["critical", "major", "minor"].map((k) => [k, prio[k] || 0]),
        (k) => `agent-prio-${k}`)}</div>
      <div><h4>By status</h4>${list(statuses, () => "agent-status")}</div>
    </div>` : html`<p class="muted">No case routed to it in ${activity.days} days.</p>`}
  </section>`;
}

/** What its runs cost each day (ADR #472), drawn only when it is priced. */
function SpendActivity({ activity, agent, now }) {
  const shown = days(activity.days, agent.created_at, now);
  const per = shown.map((day) => Math.round(activity.runs.filter((r) => r.day === day)
    .reduce((n, r) => n + Number(r.cost_cents || 0), 0) * 100) / 100);
  return html`<section class="panel-block agent-card agent-wide"><${Series} title="Spend per day (US cents)" mark="column"
    series=${[{ name: "spend", tone: null, values: per }]} height=${80}
    labels=${shown.map((d) => d.slice(5))} source=${PRICED_SOURCE[agent.priced] || "its runs"}
    span=${`${shown.length} day${shown.length === 1 ? "" : "s"}, UTC`} /></section>`;
}

const PRICED_SOURCE = {
  published: "Anthropic's published rates",
  set: "the price per million tokens its admin set",
};

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

/** How its spend is counted, in a sentence — so $0.00 never reads as "free" when it is unpriced. */
const PRICED_NOTE = {
  published: "Priced at Anthropic's published rates, cache reads and writes included.",
  set: "Priced at the rate its admin set per million tokens.",
  local: "Runs on your own hardware: its tokens are counted, no charge is.",
  unpriced: "No price is set: its tokens are counted, its spend is not, so a budget cannot stop it. Set its price per million tokens under Configuration.",
  elsewhere: "It runs elsewhere: what it spends there is not counted here.",
};

function Spend({ agent }) {
  const budget = agent.budget_month_cents;
  const used = budget ? Math.min(100, (100 * agent.spent_month_cents) / budget) : null;
  return html`<section class="panel-block spend-panel" aria-label="This month's spend">
    <h3>This month</h3>
    <p class="success-figure">${money(agent.spent_month_cents)}</p>
    ${budget ? html`<span class="gauge-track" aria-hidden="true"><span
        class=${cx("gauge-fill", used >= 80 && used < 100 && "gauge-low", used >= 100 && "gauge-over")}
        style=${`width:${used.toFixed(1)}%`}></span></span>
      <p class="muted">${Math.round(used)}% of a ${money(budget)} budget; reached, it is paused until
        someone resumes it.</p>`
      : html`<p class="muted">No budget is set.</p>`}
    <p class="hint">${PRICED_NOTE[agent.priced] || PRICED_NOTE.elsewhere}</p>
  </section>`;
}

export function AgentOverview({ d, onRuns }) {
  const now = Date.now() / 1000;
  return html`<div class="agent-overview">
    <div class="agent-overview-main">
      <${LatestRun} run=${d.runs[0]} onRuns=${onRuns} />
      <${Kpis} activity=${d.activity} agent=${d.agent} cases=${d.cases} />
      <div class="agent-cards">
        <${RunDays} activity=${d.activity} agent=${d.agent} now=${now} />
        <${CaseMix} activity=${d.activity} />
        ${["published", "set"].includes(d.agent.priced)
          ? html`<${SpendActivity} activity=${d.activity} agent=${d.agent} now=${now} />` : null}
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
