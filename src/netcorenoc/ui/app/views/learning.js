/* Learning: what waits for your judgement, and what the models learn from (ADR #484).
 *
 * One screen where there were two (Labelling and Corpus), built around the one idea they shared:
 * a person's judgement on a grouping becomes a label, labels are kept in the corpus, and the judge
 * re-ranks every model on the corpus. The UML diagram at the top says that once, with this
 * appliance's numbers in it, and each class is a way in:
 *
 *   * **To judge** — the proposals waiting for an answer and the groupings waiting for a verdict,
 *     in one list, each with a button to the situation where the decision is made (the evidence
 *     lives on the situation card).
 *   * **Corpus** (admins: capture runs inside the engine, unscoped) — what capture holds, as tiles
 *     and bars, and the three retention tiers. Read-only; the tiers are edited in Settings.
 *
 * The tab is in the address (`?tab=`). `#/labelling` and `#/corpus` still open the right tab.
 */

import { html, Component, cx } from "../dom.js";
import { get } from "../api.js";
import { Badge } from "../widgets.js";
import { Bars } from "../compare.js";
import { corpusFact, count, relative, timeTitle } from "../format.js";
import { Icon } from "../icons.js";
import { can } from "../session.js";
import * as store from "../store.js";
import { situationName } from "./parts/card.js";
import { ModelHealth } from "./parts/models.js";
import { LearningDiagram } from "./parts/learnuml.js";

const ABOUT = {
  alarm: ["Every trap becomes an alarm, learned from the stream.", null],
  situation: ["Related alarms grouped together. Confirm, split, move or merge it on its card.", "judge"],
  label: ["What a judgement teaches: confirm makes every pair positive; split makes the marked members negative.", "judge"],
  corpus: ["The evidence of every grouping, with the labels beside it. The models learn from this.", "corpus"],
  model: ["The judge re-ranks every model on the labels every five minutes; the best one decides links.", "promotion"],
};

function JudgeList({ pending, judgeable }) {
  const rows = [
    ...pending.map((s) => ({ s, kind: "proposal" })),
    ...judgeable.map((s) => ({ s, kind: "verdict" })),
  ];
  if (!rows.length) {
    return html`<div class="learn-empty"><span class="learn-empty-mark"><${Icon} name="check" /></span>
      <div><b>Nothing to judge.</b><p class="muted">Groupings of two or more alarms appear here.</p></div></div>`;
  }
  return html`<ul class="learn-list">
    ${rows.map(({ s, kind }) => html`<li key=${s.id} class="learn-row">
      <span class=${cx("learn-kind", kind === "proposal" && "is-proposal")}>${kind === "proposal" ? "Proposal" : "Verdict"}</span>
      <a class="learn-name" href=${`#/situations/${s.id}`}>#${s.id} ${situationName(s) || "Situation"}</a>
      <span class="learn-meta">${kind === "proposal"
        ? html`would join <a href=${`#/situations/${s.proposed_into}`}>#${s.proposed_into}</a>
            ${s.proposal_confidence == null ? "" : ` · ${Math.round(100 * s.proposal_confidence)}% sure`}`
        : html`${s.alarm_count} alarms · <span title=${timeTitle(s.updated_at)}>${relative(s.updated_at)}</span>`}</span>
      <${Badge} tone=${s.status === "pending" ? "warn" : "alarm"}>${s.status}<//>
      <a class="btn-ghost learn-go" href=${`#/situations/${s.id}`}>${kind === "proposal" ? "Answer" : "Judge"} <${Icon} name="chevron" /></a>
    </li>`)}
  </ul>`;
}

function CorpusTab({ data }) {
  if (!data) return html`<p class="muted">Reading what capture is holding…</p>`;
  const entries = Object.entries(data.stats || {});
  const rows = entries.filter(([k, v]) => !k.startsWith("sink_") && typeof v === "number");
  return html`<div class="learn-corpus">
    <div class="learn-tiles">
      ${entries.map(([key, value]) => {
        const fact = corpusFact(key, value);
        return html`<div key=${key} class="learn-tile" title=${fact.title || ""}><b>${fact.text}</b><span>${fact.label}</span></div>`;
      })}
    </div>
    ${rows.some(([, v]) => v) ? html`<${Bars} title="Rows held" unit="rows"
      rows=${rows.sort((a, b) => b[1] - a[1]).map(([key, value]) => ({ key, label: corpusFact(key, value).label, value }))}
      source="aggregates only, never a row" />` : null}
    <div class="learn-tiers">
      ${[["sink", `${count(data.sink_days)} d / ${count(data.sink_rows)} rows`, "Raw capture buffer"],
         ["training", `${count(data.training_days)} d`, "How far back a model reads"],
         ["audit", `${count(data.audit_days)} d`, "Outer bound; lowering it deletes"]].map(([tier, bound, what]) => html`
        <div key=${tier} class=${cx("learn-tier", tier === "audit" && "is-danger")}>
          <span class="learn-tier-name">${tier}</span><b>${bound}</b><small class="muted">${what}</small></div>`)}
    </div>
    <p class="muted learn-foot">Capture is <b>${data.capture_enabled ? "on" : "off"}</b> · tiers are changed in${" "}
      <a href="#/settings?tab=system">Settings → System</a>, which previews what a change deletes.</p>
  </div>`;
}

export class Learning extends Component {
  constructor(props) {
    super(props);
    this.state = { live: store.get(), corpus: null, picked: "situation" };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
    if (can("config.read")) {
      get("/api/dataset/retention").then((corpus) => this.setState({ corpus })).catch(() => {});
    }
  }

  componentWillUnmount() { if (this.unsubscribe) this.unsubscribe(); }

  tab() {
    const asked = this.props.query && this.props.query.get("tab");
    if (asked === "corpus" && can("config.read")) return "corpus";
    if (this.props.preset === "corpus" && !asked && can("config.read")) return "corpus";
    return "judge";
  }

  go(tab) { if (this.props.navigate) this.props.navigate(`#/learning?tab=${tab}`); }

  pick(id) {
    this.setState({ picked: id });
    const target = ABOUT[id][1];
    if (target === "promotion") return;
    if (target) this.go(target === "corpus" && !can("config.read") ? "judge" : target);
  }

  render(_props, { live, corpus, picked }) {
    const all = (live.situations || []).filter((s) => s.status !== "resolved");
    const pending = all.filter((s) => s.status === "pending");
    const judgeable = all.filter((s) => s.alarm_count >= 2 && s.status !== "pending");
    const stats = (corpus && corpus.stats) || {};
    const numbers = {
      alarms: live.stats ? live.stats.active_alarms : null,
      judge: judgeable.length,
      proposals: pending.length,
      pairs: corpus ? stats["dataset_pair.dataset"] ?? 0 : null,
      judged: corpus ? stats.feedback_member ?? 0 : null,
      capture: corpus ? (corpus.capture_enabled ? "on" : "off") : null,
    };
    const tab = this.tab();
    const [about, where] = ABOUT[picked];
    return html`<div class="learning">
      <section class="panel-block learn-diagram">
        <div class="learn-diagram-head">
          <h3><${Icon} name="sparkle" /> How your judgements teach the models</h3>
          <${ModelHealth} line=${true} />
        </div>
        <${LearningDiagram} n=${numbers} active=${picked} onPick=${(id) => this.pick(id)} />
        <p class="learn-about" role="status"><b>${picked[0].toUpperCase() + picked.slice(1)}</b> — ${about}
          ${where === "promotion" ? html` <a href="#/promotion">Judge & promotion <${Icon} name="chevron" /></a>` : null}</p>
      </section>
      <div class="access-tabs" role="tablist" aria-label="Learning">
        <button type="button" role="tab" aria-selected=${tab === "judge"} class=${cx("access-tab", tab === "judge" && "on")}
          onClick=${() => this.go("judge")}><${Icon} name="labelling" /> To judge${" "}
          <span class="tab-count">${pending.length + judgeable.length}</span></button>
        ${can("config.read") ? html`<button type="button" role="tab" aria-selected=${tab === "corpus"}
          class=${cx("access-tab", tab === "corpus" && "on")} onClick=${() => this.go("corpus")}>
          <${Icon} name="corpus" /> Corpus</button>` : null}
      </div>
      ${tab === "corpus" ? html`<${CorpusTab} data=${corpus} />` : html`<${JudgeList} pending=${pending} judgeable=${judgeable} />`}
    </div>`;
  }
}

/** `#/labelling` and `#/corpus`, kept for links and bookmarks: the same screen on its tab. */
export function LabellingAlias(props) { return html`<${Learning} ...${props} preset="judge" />`; }
export function CorpusAlias(props) { return html`<${Learning} ...${props} preset="corpus" />`; }
