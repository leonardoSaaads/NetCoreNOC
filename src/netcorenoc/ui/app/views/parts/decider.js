/* Settings → Models: **who decides links**, in plain words, and the one choice an admin has.
 *
 * v0.27.0 (ADRs #423, #425). Five pre-trained models compete — a GAM, gradient-boosted trees, a
 * random forest, a decision tree and a logistic regression — and the judge chooses which one
 * decides. An admin has exactly one choice to make here:
 *
 *   * **Automatic** (the default): the judge picks the best model, and keeps re-checking it against
 *     this site's labels every five minutes;
 *   * **Pinned**: one named model decides regardless of the ranking — for a deliberate reason, which
 *     is recorded in the audit log, and undone by switching back to automatic.
 *
 * The additive formula is no longer a choice: it is the **fail-safe**, used only when no model can
 * load, and the screen says so when it is running. Its parameters stay readable and editable below,
 * folded, because a fail-safe nobody can inspect is not one an operator can trust.
 *
 * v0.26.0's version of this screen was reported as confusing on a desktop: three radio cards for
 * one choice, the explanation in red, a fingerprint running out of its panel. This one leads with
 * a sentence, keeps the explanation in one place, and wraps identifiers.
 */

import { html, Component, cx } from "../../dom.js";
import { post, readAll } from "../../api.js";
import { Loading, Failed, SectionHeading, DataTable, TimeCell, cell } from "../../widgets.js";
import { can } from "../../session.js";
import { Formula } from "../scorer.js";

const ROLE = { champion: "deciding", challenger: "in shadow", ineligible: "too slow for the fast loop" };

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
    if (error) return html`<${Failed} error=${error} retry=${this.read} what="who decides links" />`;
    if (!data) return html`<${Loading} label="Reading who decides links" />`;
    const decider = data.decider.value;
    return html`<div class="stack">
      <div class="settings-intro">
        <p><b>Models compete; the judge chooses.</b> Every trap is grouped by the model the judge
          ranks best. The others score the same alarms in shadow, and every five minutes the judge
          re-checks them against the groupings your operators confirmed or corrected.</p>
        <p>Full charts for each model — how it learned and how it compares — are on
          ${" "}<a href="#/promotion">Judge</a>.</p>
      </div>
      <${Now} decider=${decider} />
      <${Choice} decider=${decider} onDone=${this.read} />
      <${League} rows=${decider.members || []} />
      <${History} rows=${decider.history || []} decisions=${decider.decisions || []}
        members=${decider.members || []} />
      ${data.scorer.ok ? html`<details class="panel-block formula-folded">
        <summary>Fail-safe formula — used only when no model can load</summary>
        <p class="hint">Four weights and a threshold. It decides nothing while any model loads.</p>
        <${Formula} config=${data.scorer.value} onChanged=${this.read} />
      </details>` : null}
    </div>`;
  }
}

function Now({ decider }) {
  const champ = decider.champion;
  return html`<section class="panel-block">
    <${SectionHeading} title="Deciding now" />
    ${decider.fallback || !champ
      ? html`<p class="err" role="alert">No model could be loaded, so the fail-safe formula is
          grouping. ${(decider.warnings || []).join(" ")}</p>`
      : html`<p class="league-champion"><b>${champ.name}</b>${decider.pinned
          ? html`<span class="badge">pinned</span>` : html`<span class="badge">chosen by the judge</span>`}</p>
        ${(decider.warnings || []).map((w, i) => html`<p class="hint" key=${i}>${w}</p>`)}`}
  </section>`;
}

class Choice extends Component {
  constructor(props) {
    super(props);
    this.state = { pin: props.decider.pinned || "", reason: "", busy: false, error: null, done: null };
  }

  async submit(e) {
    e.preventDefault();
    this.setState({ busy: true, error: null, done: null });
    try {
      const pin = this.state.pin || null;
      await post("/api/decider", { mode: "shipped", pin, reason: this.state.reason });
      this.setState({ busy: false, reason: "", done: pin ? "pinned" : "automatic" });
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render({ decider }, { pin, reason, busy, error, done }) {
    const writable = can("decider.write");
    const members = decider.members || [];
    const current = decider.pinned || "";
    const changed = pin !== current;
    return html`<section class="panel-block">
      <${SectionHeading} title="How the model is chosen" />
      <form class="pin-form" onSubmit=${(e) => this.submit(e)}>
        <fieldset class="pin-choice" disabled=${!writable || busy}>
          <legend class="sr-only">How the model is chosen</legend>
          <label class=${cx("decider-mode", !pin && "on")}>
            <input type="radio" name="pin" checked=${!pin} onChange=${() => this.setState({ pin: "" })} />
            <span class="decider-mode-label">Automatic${!current ? html` <em>current</em>` : null}</span>
            <span class="decider-mode-note">Recommended. The judge picks the best model and switches
              only when your site's labels show, with 95 % confidence, that another one is better.</span>
          </label>
          <label class=${cx("decider-mode", pin && "on")}>
            <input type="radio" name="pin" checked=${Boolean(pin)}
              onChange=${() => this.setState({ pin: pin || (members[0] || {}).ref || "" })} />
            <span class="decider-mode-label">Pinned${current ? html` <em>current</em>` : null}</span>
            <span class="decider-mode-note">One model decides whatever the ranking says. For a
              deliberate reason — an investigation, a comparison — and recorded in the audit log.</span>
          </label>
        </fieldset>
        ${pin ? html`<label for="pin-model">Model to pin</label>
          <select id="pin-model" value=${pin} disabled=${!writable || busy}
            onChange=${(e) => this.setState({ pin: e.target.value })}>
            ${members.map((m) => html`<option key=${m.ref} value=${m.ref}>${m.rank}. ${m.name}</option>`)}
          </select>` : null}
        ${writable && changed ? html`<label for="decider-reason">Reason (recorded in the audit log)</label>
          <input id="decider-reason" value=${reason} minlength="3" required
            placeholder="why is this changing?" onInput=${(e) => this.setState({ reason: e.target.value })} />
          <div><button type="submit" class="primary" disabled=${busy || reason.trim().length < 3}>
            ${busy ? "Saving…" : pin ? "Pin this model" : "Let the judge choose"}</button></div>` : null}
        ${writable ? null : html`<p class="hint">Only an admin can change how the model is chosen.</p>`}
      </form>
      ${done ? html`<p class="ok-note" role="status">Saved (${done}). It takes effect within seconds,
        at the next maintenance pass.</p>` : null}
      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </section>`;
  }
}

function League({ rows }) {
  if (!rows.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="The models, in the judge's order"
      hint="Score: mean pairwise F1 on five test suites no model trained on, including a hand-labelled corpus. Higher is better." />
    <${DataTable} kind="league" columns=${[
      { key: "rank", label: "#", numeric: true },
      { key: "name", label: "model" },
      { key: "role", label: "role" },
      { key: "score", label: "score", numeric: true },
      { key: "latency", label: "µs per pair", numeric: true },
    ]} rows=${rows.map((r) => ({
      key: r.ref,
      cells: {
        rank: String(r.rank),
        name: r.name,
        role: html`<span class=${cx("league-role", `league-${r.role}`)}>${ROLE[r.role] || r.role}</span>`,
        score: r.score == null ? "—" : r.score.toFixed(3),
        latency: r.latency_us == null ? "—" : r.latency_us.toFixed(1),
      },
    }))} />
  </section>`;
}

function History({ rows, decisions, members }) {
  const name = (ref) => (members.find((m) => m.ref === ref) || {}).name || ref;
  const merged = [
    ...decisions.map((d) => ({ at: d.at, what: `${name(d.champion)} decides`, by: d.actor, why: d.reason })),
    ...rows.map((r) => ({ at: r.set_at, what: r.pinned ? `pinned ${name(r.pinned)}` : `mode ${r.mode}`,
      by: r.set_by, why: r.reason })),
  ].sort((a, b) => (b.at || 0) - (a.at || 0)).slice(0, 15);
  if (!merged.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="History" hint="Every change of model — by the judge or an admin — and why. Append-only." />
    <${DataTable} columns=${[
      { key: "when", label: "when" }, { key: "what", label: "what" },
      { key: "by", label: "by" }, { key: "why", label: "why" },
    ]} rows=${merged.map((r, i) => ({
      key: i,
      cells: {
        // A seeded row carries 0.0 — as old as the database — so it says so in words, not 1970.
        when: r.at ? cell(html`<${TimeCell} ts=${r.at} />`) : "at upgrade",
        what: r.what,
        by: r.by || "—",
        why: r.why || "—",
      },
    }))} />
  </section>`;
}
