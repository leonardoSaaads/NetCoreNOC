/* Settings → Autonomy: four grades, each off until an admin switches it on (v0.26.0, ADR #412).
 *
 *   * **grouping** — accept a model-made situation as an incident without waiting for a person;
 *   * **naming** — give it a name from its members;
 *   * **closing** — close it when every member has stayed clear;
 *   * **severity** — set its severity from its members.
 *
 * Every act is attributed (the decider that made it), explained (the evidence it acted on) and
 * later judged by what the operators did next. When the agreement over the last `window` judged
 * acts falls below `agreement_floor`, autonomy **turns itself off** and says why; only an admin
 * turns it back on. The kill switch is in the top bar of every screen, and here.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { Loading, Failed, SectionHeading, DataTable, TimeCell, Stat, cell } from "../../widgets.js";
import { can } from "../../session.js";
import { percent } from "../../format.js";

const GRADES = [
  ["grouping", "Grouping", "Accept a situation the model made as an incident."],
  ["naming", "Naming", "Name a situation from its members."],
  ["closing", "Closing", "Close a situation whose members have all stayed clear."],
  ["severity", "Severity", "Set a situation's severity from its members. An operator's severity always wins."],
];

const NUMBERS = [
  ["agreement_floor", "Agreement floor", "Autonomy stops itself below this share of agreed acts.", 0.05],
  ["window", "Window", "How many recent judged acts the agreement is measured over.", 1],
  ["min_judged", "Judged before it can stop", "No verdict on fewer judged acts than this.", 1],
  ["confidence_floor", "Confidence floor", "An act below this model confidence is left to a person.", 0.01],
];

export class Autonomy extends Component {
  constructor(props) {
    super(props);
    this.state = { status: null, decisions: null, error: null };
    this.read = this.read.bind(this);
  }

  componentDidMount() { this.read(); }

  async read() {
    try {
      const status = await get("/api/autonomy");
      const decisions = can("autonomy.audit") ? (await get("/api/autonomy/decisions")).decisions : null;
      this.setState({ status, decisions, error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  render(_props, { status, decisions, error }) {
    if (error) return html`<${Failed} error=${error} retry=${this.read} what="autonomy" />`;
    if (!status) return html`<${Loading} label="Reading autonomy" />`;
    return html`<div class="stack">
      <div class="settings-intro">
        <p><b>Let the deciding model act on its own, one kind of act at a time.</b> Each grade below
          is off until an admin turns it on. Every act names the model that made it and the
          evidence it used, and is judged by what operators do next.</p>
        <p>If operators disagree with too many recent acts, autonomy turns itself off and says why.
          Anyone who can edit can stop it from the top bar of every screen. A situation an operator
          has confirmed is never regrouped by a model, autonomous or not.</p>
      </div>
      <${Status} status=${status} onStopped=${this.read} />
      ${can("autonomy.write") ? html`<${Form} settings=${status.settings} onSaved=${this.read} />` : null}
      ${decisions ? html`<${Decisions} rows=${decisions} />` : null}
      <${History} rows=${status.history} />
    </div>`;
  }
}

function Status({ status, onStopped }) {
  const a = status.agreement || {};
  return html`<section class="panel-block">
    <${SectionHeading} title="Autonomy now"
      hint=${status.model_decider
        ? `Acts are made by ${status.decider}.`
        : "The additive formula is deciding: autonomy acts only on a trained model's decisions."} />
    <div class="stat-row">
      <${Stat} label="state" value=${status.active ? "on" : status.suspended ? "stopped itself" : "off"}
        tone=${status.active ? "warn" : status.suspended ? "alarm" : "quiet"}
        note=${status.active ? status.grades.join(", ") : status.reason || "no grade is on"} />
      <${Stat} label="agreement" value=${a.rate == null ? "—" : percent(a.rate, 0)}
        note=${`${a.agreed ?? 0} of ${a.judged ?? 0} judged acts; floor ${percent(a.floor ?? 0, 0)}`}
        tone=${a.rate != null && a.rate < a.floor ? "alarm" : null} />
      <${Stat} label="judged window" value=${`${a.judged ?? 0} / ${a.window ?? 0}`}
        note=${`no verdict before ${a.min_judged ?? 0} judged acts`} />
    </div>
    ${status.suspended ? html`<p class="err" role="alert">Autonomy stopped itself: ${status.reason}.
      An admin must switch it back on.</p>` : null}
    ${status.active && can("autonomy.stop") ? html`<${StopButton} onStopped=${onStopped} />` : null}
  </section>`;
}

/** **The kill switch.** One click, no preview: turning autonomy off destroys nothing. */
export class StopButton extends Component {
  constructor(props) {
    super(props);
    this.state = { busy: false, error: null };
  }

  async stop() {
    this.setState({ busy: true, error: null });
    try {
      await post("/api/autonomy/stop", {});
      this.setState({ busy: false });
      if (this.props.onStopped) this.props.onStopped();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render({ compact }, { busy, error }) {
    return html`<span class=${cx("killswitch", compact && "killswitch-compact")}>
      <button type="button" class="danger" disabled=${busy} onClick=${() => this.stop()}
        aria-label="Stop autonomy now: every grade off">
        ${busy ? "Stopping…" : compact ? "Stop autonomy" : "Stop autonomy now"}</button>
      ${error ? html`<span class="err" role="alert">${error.detail || error.message}</span>` : null}
    </span>`;
  }
}

class Form extends Component {
  constructor(props) {
    super(props);
    this.state = { values: { ...props.settings }, reason: "", busy: false, error: null, saved: false };
  }

  async save(e) {
    e.preventDefault();
    this.setState({ busy: true, error: null, saved: false });
    const body = { reason: this.state.reason };
    for (const [key] of GRADES) body[key] = Boolean(this.state.values[key]);
    for (const [key] of NUMBERS) body[key] = Number(this.state.values[key]);
    try {
      await post("/api/autonomy", body);
      this.setState({ busy: false, saved: true, reason: "" });
      this.props.onSaved();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render(_props, { values, reason, busy, error, saved }) {
    const set = (key, value) => this.setState({ values: { ...values, [key]: value } });
    return html`<section class="panel-block param-mechanism">
      <${SectionHeading} title="Grades"
        hint="Each grade is its own switch. Every act is recorded with its explanation and the model that made it." />
      <form class="stack" onSubmit=${(e) => this.save(e)}>
        <div class="grade-list">
          ${GRADES.map(([key, label, note]) => html`<label key=${key} class="grade">
            <input type="checkbox" checked=${Boolean(values[key])}
              onChange=${(e) => set(key, e.target.checked)} />
            <span><b>${label}</b><span class="hint">${note}</span></span>
          </label>`)}
        </div>
        <div class="grid-form">
          ${NUMBERS.map(([key, label, note, step]) => html`<div key=${key}>
            <label for=${`au-${key}`}>${label}</label>
            <input id=${`au-${key}`} type="number" step=${step} value=${values[key]}
              onInput=${(e) => set(key, e.target.value)} />
            <p class="impact">${note}</p>
          </div>`)}
        </div>
        <label for="au-reason">Reason (recorded in the audit log)</label>
        <input id="au-reason" value=${reason} required minlength="3"
          onInput=${(e) => this.setState({ reason: e.target.value })} />
        <button type="submit" disabled=${busy || reason.trim().length < 3}>${busy ? "Saving…" : "Save"}</button>
      </form>
      ${saved ? html`<p class="ok-note" role="status">Saved. The next autonomy pass uses it.</p>` : null}
      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </section>`;
  }
}

function explain(text) {
  try {
    const e = JSON.parse(text);
    return Object.entries(e).slice(0, 4).map(([k, v]) =>
      `${k}: ${Array.isArray(v) ? v.slice(0, 3).map((x) => (typeof x === "object" ? JSON.stringify(x) : x)).join(", ") : typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ");
  } catch {
    return text;
  }
}

function Decisions({ rows }) {
  return html`<section class="panel-block">
    <${SectionHeading} title="Recent acts"
      hint="What autonomy did, which model did it, on what evidence, and what the operators made of it." />
    <${DataTable} empty="Autonomy has not acted." columns=${[
      { key: "when", label: "when" }, { key: "what", label: "act" },
      { key: "sid", label: "situation", numeric: true }, { key: "conf", label: "confidence", numeric: true },
      { key: "decider", label: "model" }, { key: "why", label: "evidence" }, { key: "verdict", label: "verdict" },
    ]} rows=${rows.map((r) => ({
      key: r.id,
      tone: r.verdict === "disagreed" ? "warn" : null,
      cells: {
        when: cell(html`<${TimeCell} ts=${r.at} />`),
        what: `${r.grade}: ${r.action}`,
        sid: html`<a href=${`#/situations/${r.situation_id}`}>${r.situation_id}</a>`,
        conf: percent(r.confidence, 0),
        decider: html`<code class="mono">${r.decider}</code>`,
        why: html`<span class="mono small">${explain(r.explanation)}</span>`,
        verdict: r.verdict ? `${r.verdict}${r.verdict_why ? ` — ${r.verdict_why}` : ""}` : "pending",
      },
    }))} />
  </section>`;
}

function History({ rows }) {
  if (!rows || !rows.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="Changes" hint="Append-only. A row by “autonomy” is autonomy stopping itself." />
    <${DataTable} columns=${[
      { key: "when", label: "when" }, { key: "on", label: "grades on" },
      { key: "by", label: "by" }, { key: "reason", label: "reason" },
    ]} rows=${rows.map((r) => ({
      key: r.id,
      cells: {
        when: cell(html`<${TimeCell} ts=${r.set_at} />`),
        on: GRADES.filter(([k]) => r[k]).map(([k]) => k).join(", ") || "none",
        by: r.set_by,
        reason: r.reason || "—",
      },
    }))} />
  </section>`;
}
