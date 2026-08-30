/* Settings — **the screen where principle 9 either lives or dies quietly** (draft §6, §7).
 *
 * Three things this screen owes an operator, and each is structural rather than stylistic:
 *
 *   1. **Three columns, always** (§7.1): environment default, database override, effective value.
 *      An operator's real question during an incident is *"why is this value what it is?"*, and
 *      two sources with one displayed number cannot answer it. A two-source configuration that
 *      hides its precedence is more dangerous than a one-source one.
 *   2. **An impact statement per setting** (§7.2), following the retention precedent — *lowering
 *      retention deletes rows and there is no rollback* — and a preview before anything
 *      destructive.
 *   3. **What needs a restart, said honestly** (§7.3). `Settings` is read once at startup and
 *      never mutated, so most of it does. A settings page that accepted a new `trap_port` and
 *      appeared to succeed, while the receiver kept listening on the old one, is worse than no
 *      settings page: the operator believes traps are arriving.
 *
 * The three classes are *visibly* different, not differently styled: a structural value is a
 * fact with no control, never a greyed-out input. A greyed input says "you may not"; a fact says
 * "this is what is true", and they are different sentences (§6.1).
 */

import { html, Component } from "../dom.js";
import { get, post, readAll } from "../api.js";
import { Loading, Failed, Partial, DataTable, SectionHeading } from "../widgets.js";
import { can } from "../session.js";
import { Destructive } from "../destructive.js";
import { SETTINGS, MECHANISM, HARDENING, STRUCTURAL } from "../parameters.js";
import { DatasetRetention } from "./parts/retention.js";
import { Hardening, Structural, RestartRequired } from "./parts/facts.js";

export class Settings extends Component {
  constructor(props) {
    super(props);
    this.state = { status: "loading", results: null, error: null };
    this.reload = this.reload.bind(this);
  }

  componentDidMount() { this.reload(); }

  async reload() {
    this.setState({ status: "loading" });
    try {
      const results = await readAll({
        config: "/api/config",
        retention: "/api/dataset/retention",
        scorer: "/api/scorer",
      });
      if (!results.config.ok) {
        this.setState({ status: "error", error: results.config.error });
        return;
      }
      this.setState({ status: "ready", results });
    } catch (error) {
      this.setState({ status: "error", error });
    }
  }

  render(_props, { status, results, error }) {
    if (status === "loading") return html`<${Loading} label="Reading configuration" />`;
    if (status === "error") {
      return html`<${Failed} error=${error} retry=${this.reload} what="the configuration" />`;
    }
    const config = results.config.value;
    return html`<div class="settingsview">
      <${Partial} results=${results} />
      <${ClassLegend} />
      ${can("config.write")
        ? html`<${Mechanism} config=${config} onSaved=${this.reload} />`
        : html`<${ReadOnlyMechanism} config=${config} />`}
      ${results.retention.ok && can("config.write")
        ? html`<${DatasetRetention} data=${results.retention.value} onSaved=${this.reload} />`
        : null}
      <${Hardening} scorer=${results.scorer.ok ? results.scorer.value : null} />
      <${Structural} config=${config} scorer=${results.scorer.ok ? results.scorer.value : null} />
      <${RestartRequired} config=${config} />
    </div>`;
  }
}

/* `config.read` without `config.write` is a real, reachable state: the two are separate
 * capabilities precisely so a policy can grant one (F9 — reading the allowlist reveals
 * network-security posture, and is audited on denial). A principal in that state gets the
 * precedence table, which is the part that answers "why is this value what it is?", and no form. */
function ReadOnlyMechanism({ config }) {
  const precedence = config.precedence || {};
  return html`<section class=${`panel-block param-${MECHANISM}`}>
    <${SectionHeading} title="Live configuration"
      hint=${"Your account can read this configuration but not change it: it holds " +
             "config.read and not config.write."} />
    <${PrecedenceTable} rows=${[
      ["NETCORENOC_ALLOWLIST", precedence.allowlist, config.allowlist,
       "empty = accept every source"],
      ["NETCORENOC_RETENTION_DAYS", precedence.retention_days, config.retention_days, "days"],
    ]} />
  </section>`;
}

function ClassLegend() {
  return html`<section class="panel-block legend-block">
    <p class="hint">Every parameter on this screen belongs to one of three classes, and the class
      decides what this console will let you do with it.</p>
    <dl class="class-legend">
      <dt class=${`param-${MECHANISM}`}>mechanism</dt>
      <dd>Yours to set. The cost of each choice is stated beside it; where a change destroys
        something, it is previewed first.</dd>
      <dt class=${`param-${HARDENING}`}>hardening-only</dt>
      <dd>You may make these stricter and not looser. The project floor is shown; a looser value
        is refused here <em>and</em> at the API, with the reason.</dd>
      <dt class=${`param-${STRUCTURAL}`}>structural</dt>
      <dd>Facts about this appliance, shown for transparency. There is no control because there is
        no choice — these are guarantees rather than preferences.</dd>
    </dl>
  </section>`;
}

/** The two live-reloadable values, with the three-column precedence draft §7.1 requires. */
class Mechanism extends Component {
  constructor(props) {
    super(props);
    const precedence = props.config.precedence || {};
    this.state = {
      allowlist: props.config.allowlist ?? "",
      retention_days: props.config.retention_days ?? 0,
      busy: false, outcome: null, precedence,
    };
  }

  async save(overrides) {
    this.setState({ busy: true, outcome: null });
    const body = {
      allowlist: this.state.allowlist,
      retention_days: Number(this.state.retention_days),
      ...overrides,
    };
    try {
      await post("/api/config", body);
      this.setState({ busy: false, outcome: { ok: true } });
      this.props.onSaved();
    } catch (error) {
      this.setState({ busy: false, outcome: { ok: false, error } });
    }
  }

  render({ config }, { busy, outcome, precedence }) {
    const lowering = Number(this.state.retention_days) < Number(config.retention_days);
    return html`<section class=${`panel-block param-${MECHANISM}`}>
      <${SectionHeading} title="Live configuration"
        hint=${"These two take effect without a restart: an admin saving here writes a database " +
               "row that takes precedence over the environment, and the running receiver and " +
               "maintenance loop are refreshed from it. Every change is audited."} />

      <${PrecedenceTable} rows=${[
        ["NETCORENOC_ALLOWLIST", precedence.allowlist, config.allowlist,
         "empty = accept every source"],
        ["NETCORENOC_RETENTION_DAYS", precedence.retention_days, config.retention_days, "days"],
      ]} />

      <form class="stack" onSubmit=${(e) => { e.preventDefault(); if (!lowering) this.save(); }}>
        <label for="cfgAllow">Trap allowlist (CIDRs, comma-separated; empty accepts every source)</label>
        <input id="cfgAllow" value=${this.state.allowlist}
               placeholder="e.g. 10.0.0.0/8,192.168.1.0/24"
               onInput=${(e) => this.setState({ allowlist: e.target.value })} />
        <p class="impact">${SETTINGS[0].impact}</p>

        <label for="cfgRet">Operational retention (days)</label>
        <input id="cfgRet" type="number" step="0.1" value=${this.state.retention_days}
               onInput=${(e) => this.setState({ retention_days: e.target.value })} />
        <p class="impact">How long cleared and closed history is kept. <b>Lowering this deletes
          rows and there is no rollback.</b> The maintenance loop applies it in the background.</p>

        ${lowering ? null : html`<button type="submit" disabled=${busy}>
          ${busy ? "Saving…" : "Save"}
        </button>`}
      </form>

      ${lowering ? html`<${Destructive}
        title="Lower the operational retention"
        hint=${`From ${config.retention_days} days to ${this.state.retention_days} days.`}
        previewLabel="Show me what this destroys"
        confirmLabel=${`Lower retention to ${this.state.retention_days} days`}
        consequence=${"Cleared and closed history older than the new bound is deleted by the " +
                      "maintenance loop. There is no rollback for a delete: history that is " +
                      "removed cannot be reconstructed from this appliance."}
        preview=${async () => ({
          measured: false,
          message: "The operational-retention route has no preview mode: the appliance does not " +
                   "offer a row count for this bound without applying it. The dataset tiers " +
                   "below DO preview, and this console does not pretend otherwise here.",
        })}
        apply=${async () => this.save()}
        onDone=${this.props.onSaved} />` : null}

      ${outcome && outcome.ok ? html`<p class="ok-note" role="status">Saved. The change is audited
        and the running receiver and maintenance loop have already picked it up.</p>` : null}
      ${outcome && !outcome.ok ? html`<p class="err" role="alert">
        ${outcome.error.detail || outcome.error.message}</p>` : null}
    </section>`;
  }
}

/** The three columns. One component, so no screen can render two of them and call it precedence. */
function PrecedenceTable({ rows }) {
  const columns = [
    { key: "setting", label: "setting" },
    { key: "env", label: "environment default", title: "what the process started with" },
    { key: "db", label: "database override", title: "what an admin saved from this console" },
    { key: "effective", label: "effective", title: "what the appliance is actually using" },
  ];
  return html`<${DataTable}
    caption="Why each value is what it is: the environment sets the default, a saved override wins."
    columns=${columns}
    rows=${rows.map(([env, override, effective, note]) => ({
      key: env,
      cells: {
        setting: html`<code class="mono">${env}</code>`,
        env: override && override.env != null && override.env !== ""
          ? html`<code class="mono">${override.env}</code>`
          : html`<span class="muted">(unset${note ? ` — ${note}` : ""})</span>`,
        db: override && override.override != null && override.override !== ""
          ? html`<code class="mono">${override.override}</code>`
          : html`<span class="muted">(none)</span>`,
        // An empty effective value must SAY it is empty. A blank cell in a precedence table is
        // exactly the ambiguity the table exists to remove: the operator cannot tell "no value"
        // from "the console failed to render one". Found by looking at the running console.
        effective: effective === "" || effective == null
          ? html`<span class="muted">(empty — every source accepted)</span>`
          : html`<b class="mono">${String(effective)}</b>`,
      },
    }))} />`;
}


export { PrecedenceTable };
