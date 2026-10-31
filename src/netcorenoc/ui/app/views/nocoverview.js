/* NOC → Overview: the NOC at a glance (ADR #470), as the Broker's Overview is the Broker's.
 *
 * Salesforce's service dashboard, in the handbook's words. First the three numbers a NOC lead asks
 * for before anything else — **Queue** (nobody has taken it), **Working** and **Pending** (waiting
 * on someone outside the NOC) — each split by priority. Then where the cases are: a matrix of tier
 * against state (Tier 1 to 4, then the NOC team, past the agents), every cell a link to exactly
 * those cases; the same by priority; the clocks a queue cannot show (takes running late, next
 * updates due); and who holds what, agents and people.
 *
 * One read, `/api/noc/overview`, scoped as the queue is, again every fifteen seconds without the
 * loading state. A reader below editor is told the counts and not who holds them (the server
 * withholds the names, `named: false`).
 *
 * **Equipment reachability** (ADR #476): which elements stopped answering on management. No case
 * says it — a switch that loses power or its management path sends no trap — so the vault's
 * endpoint checks do, here, beside the clocks, each a link to the element (and, for a custodian,
 * to the credential).
 */

import { html, cx } from "../dom.js";
import { get } from "../api.js";
import { Loader } from "../widgets.js";
import { age, count, plural } from "../format.js";
import { Icon } from "../icons.js";
import { Avatar } from "../avatar.js";
import { roster } from "../roster.js";
import { AgentGlyph } from "./parts/nocui.js";
import { StatusLight } from "./parts/secretdetail.js";

const REFRESH_MS = 15000;
const STATES = [
  ["queue", "Queue", "Nobody has taken it yet", "cases"],
  ["working", "Working", "Taken and being worked", "play"],
  ["pending", "Pending", "Waiting on someone outside the NOC", "pause"],
];
const PRIORITIES = ["critical", "major", "minor"];

function casesHref(view, filters = {}) {
  const extra = Object.entries(filters).map(([k, v]) => `&${k}=${v}`).join("");
  return `#/cases?view=${view}${extra}`;
}

/** A row's three priorities as one bar: the word beside it says it, the bar shows the share. */
function Mix({ by, total }) {
  if (!total) return html`<span class="nov-mix nov-mix-empty" aria-hidden="true"></span>`;
  return html`<span class="nov-mix" aria-hidden="true">${PRIORITIES.filter((p) => by[p])
    .map((p) => html`<span key=${p} class=${`nov-mix-${p}`}
      style=${`width:${((by[p] / total) * 100).toFixed(1)}%`}></span>`)}</span>`;
}

function StateTile({ id, label, hint, icon, state, priorities }) {
  return html`<a class=${cx("nov-tile", `nov-tile-${id}`)} href=${casesHref(id)}
      aria-label=${`${label}: ${plural(state.total, "case")}. Open them.`}>
    <span class="nov-tile-head"><span class="nov-tile-icon"><${Icon} name=${icon} /></span>
      <span class="nov-tile-label">${label}</span></span>
    <b class="nov-num">${count(state.total)}</b>
    <span class="nov-tile-hint">${hint}</span>
    <${Mix} by=${state.by_priority} total=${state.total} />
    <span class="nov-tile-prio">${PRIORITIES.map((p) => html`<span key=${p}
      class=${cx("nov-prio", `prio-dot-${p}`)}><b>${count(state.by_priority[p])}</b> ${priorities[p]}</span>`)}</span>
  </a>`;
}

/** Tier (or the NOC team) against state: the matrix a lead reads the floor from. */
function LevelMatrix({ d }) {
  const levels = Object.entries(d.levels);
  const max = Math.max(1, ...levels.map(([lv]) => STATES.reduce((n, [s]) => n + d.states[s].by_level[lv], 0)));
  return html`<section class="panel-block nov-panel" aria-labelledby="nov-levels">
    <h3 id="nov-levels">By tier</h3>
    <p class="muted nov-sub">Where each case is: the agents' tier it has climbed to, or the NOC team
      once it is past the agents.</p>
    <div class="table-scroll"><table class="data nov-matrix">
      <thead><tr><th scope="col">Level</th>${STATES.map(([s, label]) => html`<th scope="col" key=${s}
        class="num">${label}</th>`)}<th scope="col" class="num">Total</th>
        <th scope="col" class="wide-only"><span class="visually-hidden">Share</span></th></tr></thead>
      <tbody>${levels.map(([lv, label]) => {
        const tier = lv === "team" ? 0 : Number(lv);
        const row = STATES.map(([s]) => d.states[s].by_level[lv]);
        const total = row.reduce((a, b) => a + b, 0);
        return html`<tr key=${lv} class=${cx(!total && "nov-row-empty")}>
          <th scope="row"><span class=${cx("nov-level", `nov-level-${lv}`)}>${label}</span></th>
          ${STATES.map(([s], i) => html`<td key=${s} class="num">${row[i]
            ? html`<a href=${casesHref(s, { tier })}>${count(row[i])}</a>` : html`<span class="muted">0</span>`}</td>`)}
          <td class="num"><b>${count(total)}</b></td>
          <td class="wide-only"><span class="nov-share" aria-hidden="true">${STATES.map(([s], i) => row[i]
            ? html`<span key=${s} class=${`nov-share-${s}`} style=${`width:${((row[i] / max) * 100).toFixed(1)}%`}></span>`
            : null)}</span></td>
        </tr>`;
      })}</tbody>
    </table></div>
  </section>`;
}

function PriorityMatrix({ d }) {
  return html`<section class="panel-block nov-panel" aria-labelledby="nov-prio">
    <h3 id="nov-prio">By priority</h3>
    <p class="muted nov-sub">The case's priority follows the Broker's severity until someone sets it.</p>
    <div class="table-scroll"><table class="data nov-matrix">
      <thead><tr><th scope="col">Priority</th>${STATES.map(([s, label]) => html`<th scope="col" key=${s}
        class="num">${label}</th>`)}</tr></thead>
      <tbody>${PRIORITIES.map((p) => html`<tr key=${p}>
        <th scope="row"><span class=${cx("case-priority", `prio-${p}`)}>${d.priorities[p]}</span></th>
        ${STATES.map(([s]) => {
          const n = d.states[s].by_priority[p];
          return html`<td key=${s} class="num">${n ? html`<a href=${casesHref(s, { priority: p })}>${count(n)}</a>`
            : html`<span class="muted">0</span>`}</td>`;
        })}
      </tr>`)}</tbody>
    </table></div>
  </section>`;
}

function Fact({ value, label, tone, href }) {
  const body = html`<b class="nov-fact-num">${count(value)}</b><span>${label}</span>`;
  return href && value
    ? html`<a class=${cx("nov-fact", value && tone)} href=${href}>${body}</a>`
    : html`<div class=${cx("nov-fact", value && tone)}>${body}</div>`;
}

function Clocks({ d }) {
  const { takes, updates, resolved } = d;
  return html`<section class="panel-block nov-panel" aria-labelledby="nov-clocks">
    <h3 id="nov-clocks">Clocks</h3>
    <h4 class="nov-h4">Take — the NOC's first promise</h4>
    <div class="nov-facts">
      <${Fact} value=${takes.late} label="past their target" tone="nov-bad" href=${casesHref("queue")} />
      <${Fact} value=${takes.near} label="near it (75%)" tone="nov-warn" href=${casesHref("queue")} />
      <${Fact} value=${takes.running} label="within it" />
    </div>
    <h4 class="nov-h4">Next updates</h4>
    <div class="nov-facts">
      <${Fact} value=${updates.overdue} label="overdue" tone="nov-bad" />
      <${Fact} value=${updates.due_soon} label="due within the hour" tone="nov-warn" />
    </div>
    <h4 class="nov-h4">Resolved in the last day</h4>
    <div class="nov-facts">
      <${Fact} value=${resolved.total} label="resolved" href=${casesHref("resolved")} />
      <${Fact} value=${resolved.by_agents} label="by agents" />
      <${Fact} value=${resolved.without_noc} label="cleared without the NOC" />
    </div>
  </section>`;
}

function HolderRow({ face, name, href, entry }) {
  return html`<li class="nov-holder">
    ${face}
    ${href ? html`<a class="nov-holder-name" href=${href}>${name}</a>` : html`<span class="nov-holder-name">${name}</span>`}
    <span class="nov-holder-counts">${STATES.map(([s, label]) => html`<span key=${s}
      class=${cx("nov-count", `nov-count-${s}`, !entry[s] && "nov-count-zero")}
      title=${`${label}: ${entry[s]}`}><b>${count(entry[s])}</b> ${label.toLowerCase()}</span>`)}</span>
  </li>`;
}

function Holders({ d }) {
  const live = STATES.reduce((n, [s]) => n + d.states[s].total, 0);
  const kinds = ["agent", "person", "none"].map((k) => [k,
    STATES.reduce((n, [s]) => n + d.states[s].by_holder[k], 0)]);
  const words = { agent: "with an agent", person: "with a person", none: "with nobody yet" };
  const known = roster() || [];
  return html`<section class="panel-block nov-panel nov-holders" aria-labelledby="nov-holders">
    <h3 id="nov-holders">Who holds what</h3>
    <span class="nov-mix" aria-hidden="true">${live ? kinds.filter(([, n]) => n).map(([k, n]) => html`<span
      key=${k} class=${`nov-kind-bar-${k}`} style=${`width:${((n / live) * 100).toFixed(1)}%`}></span>`) : null}</span>
    <p class="nov-kinds">${kinds.map(([k, n]) => html`<span key=${k} class=${`nov-kind nov-kind-${k}`}>
      <b>${count(n)}</b> ${words[k]}</span>`)}</p>
    ${!d.named ? html`<p class="muted">Who holds each case is shown to editors and admins.</p>` : html`
      ${d.agents.length ? html`<h4 class="nov-h4">Agents</h4><ul class="nov-holder-list">
        ${d.agents.map((a) => {
          const agent = known.find((k) => k.id === a.id) || { role: "engineer", icon: "bot" };
          return html`<${HolderRow} key=${`a${a.id}`} entry=${a} name=${a.name} href=${`#/agents/${a.id}`}
            face=${html`<${AgentGlyph} agent=${agent} size="sm" />`} />`;
        })}</ul>` : null}
      ${d.people.length ? html`<h4 class="nov-h4">People</h4><ul class="nov-holder-list">
        ${d.people.map((p) => html`<${HolderRow} key=${p.ref} entry=${p} name=${p.name}
          face=${html`<${Avatar} id=${p.user_id} digest=${p.avatar} username=${p.name} size=${24} />`} />`)}
      </ul>` : null}
      ${!d.agents.length && !d.people.length ? html`<p class="muted">Nobody holds a case right now.</p>` : null}`}
  </section>`;
}

/** The elements the NOC can no longer reach over management, worst and longest first. */
function Reach({ r }) {
  if (!r || !r.monitored) return null;
  const down = r.trouble.filter((e) => e.state === "down").length;
  const shaky = r.trouble.length - down;
  return html`<section class=${cx("panel-block nov-panel nov-reach", down && "nov-reach-alert")}
      aria-labelledby="nov-reach">
    <h3 id="nov-reach">Equipment reachability</h3>
    <p class="muted nov-sub">The management endpoints the vault checks every few minutes. A switch
      that loses power or its management path sends no trap: this is where it shows.</p>
    <p class="nov-reach-sum"><b>${count(r.answering)}</b> of ${plural(r.monitored, "element")} answer${
      shaky ? html`${" · "}<b>${count(shaky)}</b> intermittent` : null}${
      down ? html`${" · "}<b class="nov-reach-bad">${count(down)}</b> not answering` : null}</p>
    ${r.trouble.length ? html`<ul class="nov-reach-list">${r.trouble.map((e) => html`<li key=${e.host}>
      <${StatusLight} status=${{ state: e.state, reason: e.reason || "" }} />
      <span class="nov-reach-what">
        ${e.ne_id ? html`<a href=${`#/entities?ne=${e.ne_id}`}>${e.element || e.host}</a>`
          : html`<b>${e.element || e.host}</b>`}
        <span class="muted">${e.port ? `${e.host}:${e.port}` : e.host} · ${e.protocol}${
          e.since ? ` · no answer for ${age(e.since)}`
            : e.reason ? ` · ${e.reason.replace(/^[A-Za-z]+: /, "")}` : ""}</span>
      </span>
      ${e.secret_id ? html`<a class="nov-reach-cred" href=${`#/secrets/${e.secret_id}`}>Credential</a>` : null}
    </li>`)}</ul>` : html`<p class="muted">Every monitored element answers.</p>`}
  </section>`;
}

/** What the NOC still lacks for its agents to work cases on their own (ADR #478), in the order a
 * new install does it. Each step says done or to do in words, never by colour alone. */
const SETUP = [
  ["ai_provider", "Connect an AI provider", "A model for the agents to think with — a local Ollama needs no key.",
    "#/secrets?tab=ai", "Secrets → AI providers"],
  ["agent", "Hire an agent", "Its job comes prefilled; a name and a model are enough.", "#/agents?hire=1", "Hire an agent"],
  ["entry", "Make one agent the entry point", "Cases from the queue start with it. The first one hired is.",
    "#/agents", "Agents"],
  ["device_access", "Let the agents reach your devices", "Store an SSH or SNMP credential for each device and switch agents on for it. Until then they set the case Pending connectivity.",
    "#/secrets", "Secrets"],
];

function Setup({ s }) {
  if (!s || s.ready) return null;
  return html`<section class="panel-block nov-setup" aria-labelledby="nov-setup">
    <h3 id="nov-setup">Get the NOC's agents working</h3>
    <ol class="nov-setup-list">${SETUP.map(([key, title, why, href, link]) => html`<li key=${key}
        class=${cx("nov-setup-step", s[key] && "done")}>
      <span class="nov-setup-mark"><${Icon} name=${s[key] ? "check" : "info"} /></span>
      <span><b>${title}</b>${s[key] ? html` <span class="muted">— done</span>`
        : html`<span class="muted"> — ${why} </span><a href=${href}>${link}</a>`}</span>
    </li>`)}</ol>
  </section>`;
}

export class NocOverview extends Loader {
  constructor(props) {
    super(props);
    this.what = "the NOC overview";
    this.loadingLabel = "Counting the cases";
  }

  async load() { return get("/api/noc/overview"); }

  routeKey() { return "noc"; }

  componentDidMount() {
    super.componentDidMount();
    this.timer = setInterval(async () => {
      try { this.setState({ data: await this.load() }); } catch { /* the next tick reads again */ }
    }, REFRESH_MS);
  }

  componentWillUnmount() { clearInterval(this.timer); }

  view(d) {
    const live = STATES.reduce((n, [s]) => n + d.states[s].total, 0);
    return html`<div class="nov">
      ${d.named ? html`<${Setup} s=${d.setup} />` : null}
      ${d.scoped ? html`<p class="warnbox">Your view is scoped: these are the cases on the network
        elements you can see.</p>` : null}
      <div class="nov-states">${STATES.map(([id, label, hint, icon]) => html`<${StateTile} key=${id}
        id=${id} label=${label} hint=${hint} icon=${icon} state=${d.states[id]} priorities=${d.priorities} />`)}</div>
      ${live ? null : html`<p class="hint nov-quiet">No case is open. A situation the Broker opens
        arrives in the queue with its take clock running.</p>`}
      <div class="nov-grid">
        <div class="nov-col-wide"><${LevelMatrix} d=${d} /><${PriorityMatrix} d=${d} /></div>
        <div class="nov-col"><${Reach} r=${d.reachability} /><${Clocks} d=${d} /><${Holders} d=${d} /></div>
      </div>
    </div>`;
  }
}
