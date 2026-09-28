/* Settings → Correlation: **what decides links**, and the formula's weights (v0.26.0, ADR #405).
 *
 * Three families can decide, and the admin picks one here:
 *
 *   * **shipped model** — the default. Trained on generated data, validated on families it never
 *     saw, stored in the package with a manifest whose SHA-256 the appliance checks before loading
 *     it. Its provenance is on this screen because a model nobody can trace is a model nobody
 *     should run;
 *   * **site model** — the shipped model adapted to this appliance's labels. Offered only when the
 *     server's own paired comparison said `BETTER`; the request names a mode and never a verdict;
 *   * **additive formula** — v0.25.0's four numbers, opt-in. Its editor (the old *Link scorer*
 *     screen) lives here, and its hardening-only floors and preview are unchanged.
 *
 * Switching needs a reason, because an unexplained change to the thing that groups every alarm is
 * the change an audit two months later cannot read.
 */

import { html, Component, cx } from "../../dom.js";
import { post, readAll } from "../../api.js";
import { Loading, Failed, SectionHeading, DataTable, TimeCell, cell } from "../../widgets.js";
import { can } from "../../session.js";
import { Formula } from "../scorer.js";

const MODES = [
  ["shipped", "Shipped model", "Trained and validated before release; the default."],
  ["site", "Site model", "The shipped model adapted to this appliance's labels, when the judge says it is better."],
  ["additive", "Additive formula", "Four weights and a threshold, typed by an admin. Opt-in."],
];

export class Correlation extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null };
    this.read = this.read.bind(this);
  }

  componentDidMount() { this.read(); }

  async read() {
    try {
      const data = await readAll({ decider: "/api/decider", scorer: "/api/scorer" });
      if (!data.decider.ok) throw data.decider.error;
      this.setState({ data, error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  render(_props, { data, error }) {
    if (error) return html`<${Failed} error=${error} retry=${this.read} what="the decider" />`;
    if (!data) return html`<${Loading} label="Reading what decides links" />`;
    const decider = data.decider.value;
    return html`<div class="stack">
      <${Picker} decider=${decider} onDone=${this.read} />
      <${Provenance} shipped=${decider.shipped} />
      <${SiteModels} models=${decider.site_models} />
      ${data.scorer.ok ? html`<section class=${cx("panel-block", decider.mode !== "additive" && "dimmed")}>
        <${SectionHeading} title="The additive formula"
          hint=${decider.mode === "additive"
            ? "Deciding now. Changes apply at the next engine reload."
            : "Stored, not deciding: choose the additive formula above for these numbers to group anything."} />
        <${Formula} config=${data.scorer.value} onChanged=${this.read} />
      </section>` : null}
      <${History} rows=${decider.history} />
    </div>`;
  }
}

class Picker extends Component {
  constructor(props) {
    super(props);
    this.state = { mode: props.decider.mode, reason: "", busy: false, error: null, done: null };
  }

  async submit(e) {
    e.preventDefault();
    this.setState({ busy: true, error: null, done: null });
    try {
      const out = await post("/api/decider", { mode: this.state.mode, reason: this.state.reason });
      this.setState({ busy: false, reason: "", done: out.effective });
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render({ decider }, { mode, reason, busy, error, done }) {
    const writable = can("decider.write");
    const siteReady = (decider.site_models || []).some((m) => m.verdict && m.verdict.verdict === "BETTER");
    const changed = mode !== decider.mode;
    return html`<section class="panel-block">
      <${SectionHeading} title="What decides links"
        hint=${`Running now: ${decider.running}.`} />
      ${(decider.warnings || []).map((w, i) => html`<p class="err" role="alert" key=${i}>${w}</p>`)}
      <form class="decider-form" onSubmit=${(e) => this.submit(e)}>
        <fieldset class="decider-modes" disabled=${!writable || busy}>
          <legend class="sr-only">Decider</legend>
          ${MODES.map(([key, label, note]) => {
            const unavailable = (key === "site" && !siteReady) ||
              (key === "shipped" && decider.shipped && decider.shipped.available === false);
            return html`<label key=${key} class=${cx("decider-mode", mode === key && "on", unavailable && "off")}>
              <input type="radio" name="decider" value=${key} checked=${mode === key}
                disabled=${unavailable} onChange=${() => this.setState({ mode: key })} />
              <span class="decider-mode-label">${label}${decider.mode === key ? html` <em>current</em>` : null}</span>
              <span class="decider-mode-note">${unavailable && key === "site"
                ? "No site model has been judged better yet."
                : unavailable ? "The shipped model could not be loaded." : note}</span>
            </label>`;
          })}
        </fieldset>
        ${writable && changed ? html`<label for="decider-reason">Reason (recorded in the audit log)</label>
          <input id="decider-reason" value=${reason} minlength="3" required
            placeholder="why is this changing?" onInput=${(e) => this.setState({ reason: e.target.value })} />
          <button type="submit" disabled=${busy || reason.trim().length < 3}>
            ${busy ? "Switching…" : "Switch"}</button>` : null}
        ${writable ? null : html`<p class="hint">Only an admin can change what decides links.</p>`}
      </form>
      ${done ? html`<p class="ok-note" role="status">Switched. Effective ${done}.</p>` : null}
      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </section>`;
  }
}

/** Where the shipped model came from: the questions an auditor asks, answered from the manifest. */
function Provenance({ shipped }) {
  if (!shipped || shipped.available === false) {
    return html`<section class="panel-block"><${SectionHeading} title="Shipped model" />
      <p class="err">${shipped ? shipped.reason : "not available"}</p></section>`;
  }
  const p = shipped.provenance || {};
  const verdict = shipped.verdict || {};
  const rows = [
    ["reference", shipped.ref],
    ["SHA-256", shipped.sha256],
    ["trained with", p.trained_with_version ? `NetCoreNOC ${p.trained_with_version}` : "—"],
    ["commit", p.commit],
    ["data", p.data],
    ["dataset digest", p.dataset_digest],
    ["seed", p.seed],
    ["training / validation rows", p.training_rows != null ? `${p.training_rows} / ${p.validation_rows}` : "—"],
    ["held-out families", (p.held_out_families || []).join(", ")],
    ["features", (shipped.features || []).join(", ")],
    ["quality bar", verdict.passed ? `met on ${verdict.checked} checks` : "—"],
  ];
  return html`<section class="panel-block param-structural">
    <${SectionHeading} title="Shipped model — provenance"
      hint="A file in the package, checked against its manifest's SHA-256 before it loads. It is data: a table of numbers, never code." />
    <dl class="facts">
      ${rows.map(([k, v]) => html`<div class="fact" key=${k}><dt>${k}</dt><dd class="mono">${v == null || v === "" ? "—" : String(v)}</dd></div>`)}
    </dl>
    <p class="hint">Its measured quality is on <a href="#/promotion">Judge & promotion</a>.</p>
  </section>`;
}

function SiteModels({ models }) {
  if (!models || !models.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="Site models" hint="Fitted on this appliance by the search. Each is judged against the shipped model on the newest labels." />
    <${DataTable} columns=${[
      { key: "id", label: "#", numeric: true },
      { key: "when", label: "fitted" },
      { key: "verdict", label: "verdict" },
      { key: "note", label: "note" },
    ]} rows=${models.map((m) => ({
      key: m.id,
      cells: {
        id: m.active ? html`<b>${m.id} (active)</b>` : String(m.id),
        when: cell(html`<${TimeCell} ts=${m.created_at} />`),
        verdict: m.verdict ? html`<b class="mono">${m.verdict.verdict}</b>` : "—",
        note: (m.verdict && m.verdict.reason) || m.note || "—",
      },
    }))} />
  </section>`;
}

function History({ rows }) {
  if (!rows || !rows.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="Switches" hint="Append-only: every change of decider, by whom and why." />
    <${DataTable} columns=${[
      { key: "when", label: "when" }, { key: "mode", label: "mode" },
      { key: "by", label: "by" }, { key: "reason", label: "reason" },
    ]} rows=${rows.map((r) => ({
      key: r.id,
      cells: {
        when: cell(html`<${TimeCell} ts=${r.set_at} />`),
        mode: html`<b class="mono">${r.mode}</b>`,
        by: r.set_by || "—",
        reason: r.reason || "—",
      },
    }))} />
  </section>`;
}
