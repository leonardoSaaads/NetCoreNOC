/* "Are these groupings any good right now?" — one line on Situations, detail behind a click.
 *
 * ## Why this is not a screen
 *
 * v0.18.0 first built this as a whole view in the Operations group. **That was wrong and the
 * maintainer said so**: Situations already explains *this* grouping — `parts/why.js` gives the
 * three named terms of every link — and an operator does not want a second place to read
 * correlation numbers. What was missing is narrower and it is the only thing added here:
 *
 * > the grouping you are looking at is produced by a scorer, and nothing told you whether that
 * > scorer is currently doing a good job.
 *
 * So the answer is **one sentence**, on the screen the question occurs on, and everything behind
 * it is collapsed. An operator who only wants *"is correlation healthy?"* reads six words and
 * moves on; an engineer who wants the distribution clicks once.
 *
 * ## The verdict is derived, and the rule is on screen
 *
 * `summarise` states its own rule in the text it returns, and the numbers it judged are in the
 * panel underneath. Nothing here invents a grade: the two inputs are counted by
 * `engine/correlate/monitor.py` as the champion decides, and a figure the appliance has not
 * measured renders `—` rather than a zero.
 *
 * **Not evidence.** Read-only, aggregate, in memory, and no promotion path can reach it.
 */

import { html } from "../../dom.js";
import { Component } from "../../dom.js";
import { get } from "../../api.js";
import { count, percent, score } from "../../format.js";

/** A rate the appliance has not measured renders `—`, never `0 %` (#289). */
function rate(value) {
  return value == null ? "—" : percent(value, 1);
}

/**
 * The one sentence, and the rule that produced it.
 *
 * Two signals, because they are the two ways this scorer fails and they fail differently:
 *
 * * **close calls** — decisions within `near_threshold_band` of the threshold. A correlator
 *   whose verdicts cluster on the line is guessing, and it looks exactly like a confident one
 *   unless someone counts.
 * * **a moved accept rate** — the recent window against the lifetime. Correlation that suddenly
 *   links far more or far less than it used to has had something change underneath it.
 *
 * The bands are deliberately loose. This is a "look here" marker, not a gate, and a console that
 * cried drift at every wobble is one nobody reads.
 */
export function summarise(data) {
  const life = data.lifetime || {};
  const recent = data.recent || {};
  if (!life.activations) {
    return { tone: "quiet", text: "No grouping decisions yet." };
  }
  const near = life.near_threshold_rate;
  const moved =
    recent.accept_rate != null && life.accept_rate != null
      ? Math.abs(recent.accept_rate - life.accept_rate)
      : null;
  if (near != null && near > 0.25) {
    return {
      tone: "warn",
      text: `${rate(near)} of grouping decisions were close calls — the correlator is often near
             its own threshold.`,
    };
  }
  if (moved != null && moved > 0.15) {
    return {
      tone: "warn",
      text: `The share of pairs being linked has moved ${rate(moved)} against its lifetime
             average.`,
    };
  }
  return {
    tone: "ok",
    text: `Grouping looks steady — ${rate(near)} of decisions were close calls.`,
  };
}

/**
 * The compact control: a summary line that is also the button, and a panel that is not rendered
 * at all until it is opened.
 *
 * Reads `/api/correlation` **once, on first open**. A screen an operator leaves open all shift
 * must not poll for a figure they are not looking at, and the collapsed line needs the read too
 * — so the read happens on mount and the panel costs nothing more.
 */
export class CorrelationHealth extends Component {
  constructor(props) {
    super(props);
    this.state = { open: false, data: null, error: null };
  }

  componentDidMount() {
    this.read();
  }

  async read() {
    try {
      this.setState({ data: await get("/api/correlation"), error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  render(_props, { open, data, error }) {
    // A failed read is stated and never rendered as a healthy verdict — an absent measurement
    // and a good one must not look the same (#289).
    if (error) {
      return html`<p class="corr-line corr-quiet">
        Could not read how the correlator is deciding (${error.detail || "request failed"}).
      </p>`;
    }
    if (!data) return null;
    const verdict = summarise(data);
    return html`<div class="corr">
      <button type="button" class=${`corr-line corr-${verdict.tone}`} aria-expanded=${open}
              aria-controls="corrPanel"
              onClick=${() => this.setState({ open: !open })}>
        <span class="corr-dot" aria-hidden="true"></span>
        <span class="corr-text">${verdict.text}</span>
        <span class="corr-more">${open ? "hide" : "how it decided"}</span>
      </button>
      ${open ? this.panel(data) : null}
    </div>`;
  }

  /** The detail, for the engineer who clicked. Deliberately a list, not a dashboard. */
  panel(data) {
    const life = data.lifetime || {};
    const recent = data.recent || {};
    const run = data.running || {};
    const carried = Object.entries(data.carried_by || {}).sort((a, b) => b[1] - a[1]);
    const total = carried.reduce((sum, [, n]) => sum + n, 0);
    return html`<div id="corrPanel" class="corr-panel">
      <dl class="corr-facts">
        <div><dt>Pairs scored</dt><dd>${count(life.evaluated)}</dd></div>
        <div><dt>Linked</dt><dd>${count(life.linked)} (${rate(life.accept_rate)})</dd></div>
        <div><dt>Linked, last ${count(recent.window_activations)} alarms</dt>
             <dd>${rate(recent.accept_rate)}</dd></div>
        ${/* The explicit spaces are F112's lesson: adjacent interpolations are adjacent text
              nodes, and a newline inside the template does not survive into one. `0.05of the`
              is what the browser showed before these were here. */ null}
        <div><dt>Close calls</dt>
             <dd>${count(life.near_threshold)}${" "}within${" "}
                 ${score(data.near_threshold_band)}${" "}of the${" "}
                 ${score(data.threshold)}${" "}threshold</dd></div>
        <div><dt>Groups merged by an arriving alarm</dt><dd>${count(life.merges)}</dd></div>
        <div><dt>Scorer</dt>
             <dd>${run.scorer_id || "—"}${data.degraded ? " (degraded to the defaults)" : ""}</dd>
        </div>
      </dl>
      ${total
        ? html`<p class="corr-carried">
            What carried the links:
            ${carried.map(([name, n], i) => html`<span key=${name}>${i ? ", " : " "}<b
              >${name.replace(/_/g, " ")}</b>${" "}${percent(n / total, 0)}</span>`)}.${" "}
            ${carried[0] && carried[0][1] / total > 0.8
              ? html`<span class="corr-note">One term is carrying almost every link.</span>`
              : null}
          </p>`
        : null}
      <p class="corr-since">
        Counted as the scorer decided, since this appliance started. Not stored, and not evidence.
      </p>
    </div>`;
  }
}
