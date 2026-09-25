/* "Is the machine learning anything, and when does it take over?" — on the Overview, in a line.
 *
 * ## The question this answers, and why nothing answered it before
 *
 * Every piece of this was already in the database and no screen put it together. An operator who
 * confirmed a grouping got one green sentence — *"Recorded: this grouping is correct"* — and no
 * way at all to find out whether that judgement had achieved anything. The place that should have
 * said so, the Labelling screen, said the figure was **"deliberately not computed here"** and
 * pointed at a `make` target that needs shell access to the appliance. The Judge screen carried a
 * heading reading **"Not drawn, because nothing measures it"** over the loss curve.
 *
 * Both of those were honest and both were answers to the wrong question. The corpus counts are
 * read from the row the appliance already wrote, so there is still exactly one implementation of
 * them; the loss curve is drawn because v0.19.0 made the optimiser record one. Neither replaces
 * `make shadow-report`, which remains the byte-for-byte gate over frozen inputs.
 *
 * ## Compact, and what "compact" cost
 *
 * One line and one bar, always. Four bars, a curve and the two controls behind a click. The line
 * is the verdict, the bar is *"how far am I"*, and an operator who wants neither reads six words.
 *
 * ## It is not evidence
 *
 * Reading this writes nothing. The handover button posts to `/api/promotion`, where the server
 * re-derives the floors, the power condition, the sealed holdout and the verdict — this screen
 * asserts none of them and cannot. The click is the human gesture; it is not the evidence.
 */

import { Component, html } from "../../dom.js";
import { get, post } from "../../api.js";
import { Series } from "../../charts.js";
import { count, percent, score } from "../../format.js";

/**
 * The share of the way to a model, and the rule is the honest one: **the weakest floor**.
 *
 * Not the mean of the four. A corpus at 100 % of three floors and 0 % of the fourth trains
 * nothing at all, and a bar reading 75 % over it would be describing progress that does not
 * exist. `min` is what "ready" actually depends on, so `min` is what the bar shows.
 */
export function readiness(floors) {
  if (!floors || !floors.length) return 0;
  return Math.min(...floors.map((f) => (f.need > 0 ? Math.min(1, f.have / f.need) : 1)));
}

/**
 * The floor furthest from being met, which is the one an operator should act on next.
 *
 * **This is the difference between a progress bar and an instruction.** The binding floor is
 * usually `split_bags` — groupings marked *wrong* — and an operator who has been diligently
 * confirming correct ones will watch that bar stay at zero without being told why. Naming it,
 * and saying what closes it, is the whole point of showing the bars at all.
 */
export function nextStep(floors) {
  if (!floors || !floors.length) return null;
  const short = floors
    .filter((f) => f.have < f.need)
    .sort((a, b) => a.have / (a.need || 1) - b.have / (b.need || 1));
  return short.length ? short[0] : null;
}

/** What closes each floor, in the operator's own actions. Keyed by the floor, not by prose. */
const ADVICE = {
  split_bags: "Correct a grouping that is wrong — a correction teaches more than a confirmation.",
  mixed_bags: "Split the members that do not belong out of a grouping, rather than rejecting it.",
  incidents: "Judge groupings from more separate incidents, not more of the same one.",
  operators: "A second and third person need to judge some groupings too.",
};

/** The one sentence: who decides, and what would change that. */
export function summarise(data) {
  const deciding = data.deciding || {};
  const evidence = data.evidence || {};
  const learning = data.learning || {};
  if (deciding.kind === "model") {
    return {
      tone: "ok",
      text: `A trained model is deciding which alarms belong together (version
             ${deciding.model_version_id}).`,
    };
  }
  if (evidence.met && learning.fits) {
    return {
      tone: "warn",
      text: "A model has been trained and is ready to take over — nobody has approved it yet.",
    };
  }
  if (evidence.met) {
    return { tone: "warn", text: "There is enough evidence to train a model. None has fitted yet." };
  }
  const share = readiness(evidence.floors);
  if (!share) {
    return {
      tone: "quiet",
      text: `The built-in formula is deciding. Confirm or correct groupings and a model starts
             learning from them.`,
    };
  }
  return {
    tone: "quiet",
    text: `The built-in formula is deciding. Your judgements are ${percent(share, 0)} of the way
           to training a model.`,
  };
}

/** One floor as a bar. The numbers are beside it, so the bar never has to be read precisely. */
function Meter({ label, have, need }) {
  const share = need > 0 ? Math.min(1, have / need) : 1;
  return html`<div class="floor">
    <span class="floor-label">${label}</span>
    <span class="floor-track" role="img"
          aria-label=${`${label}: ${have} of ${need}`}>
      <span class="floor-fill" style=${`width:${(share * 100).toFixed(1)}%`}></span>
    </span>
    <span class="floor-value"><b>${count(have)}</b>${" "}/${" "}${count(need)}</span>
  </div>`;
}

/**
 * The card. Reads `/api/models` on mount, and again after an action, because an action is
 * precisely when the numbers it shows have changed.
 */
export class ModelHealth extends Component {
  constructor(props) {
    super(props);
    this.state = { open: false, data: null, error: null, busy: false, said: null };
  }

  componentDidMount() {
    this.read();
  }

  async read() {
    try {
      this.setState({ data: await get("/api/models"), error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  /** Register the appliance's own newest fit. Admin-only server-side; the button is too. */
  async register(runId) {
    this.setState({ busy: true, said: null });
    try {
      const out = await post("/api/models/register", { challenger_run_id: runId });
      this.setState({ said: `Registered as model version ${out.model_version_id}.` });
    } catch (error) {
      this.setState({ said: error.detail || "That could not be registered." });
    }
    this.setState({ busy: false });
    await this.read();
  }

  /**
   * Ask the server to hand correlation over. **It decides, not this button.**
   *
   * A refusal is the expected answer on a corpus below the floors and is shown as the server
   * worded it — the triggers that fired are the useful part, and softening them into "not yet"
   * would hide the one thing that says what to do next.
   */
  async handOver(versionId) {
    this.setState({ busy: true, said: null });
    try {
      const out = await post("/api/promotion", { model_version_id: versionId });
      this.setState({
        said: out.outcome === "applied"
          ? "The model is now deciding. Correlation is running it from the next trap."
          : `Refused: ${out.refusal_reason || out.verdict}.`,
      });
    } catch (error) {
      this.setState({ said: error.detail || "That could not be proposed." });
    }
    this.setState({ busy: false });
    await this.read();
  }

  render({ admin }, { open, data, error, busy, said }) {
    if (error) {
      return html`<p class="corr-line corr-quiet">
        Could not read how the models are doing (${error.detail || "request failed"}).
      </p>`;
    }
    if (!data) return null;
    const verdict = summarise(data);
    const share = readiness((data.evidence || {}).floors);
    return html`<section class="models">
      <button type="button" class=${`corr-line corr-${verdict.tone}`} aria-expanded=${open}
              aria-controls="modelPanel" onClick=${() => this.setState({ open: !open })}>
        <span class="corr-dot" aria-hidden="true"></span>
        <span class="corr-text">${verdict.text}</span>
        <span class="corr-more">${open ? "hide" : "show the learning"}</span>
      </button>
      ${/* The bar stays out of the collapse on purpose. It is the answer to "did my confirmation
            do anything", which is the question the click would be asked in order to answer.
            It carries its own caption: a bare 8-pixel track under a sentence reads as a rule
            between two paragraphs, which is what the first cut of this looked like on screen. */
        null}
      <div class="models-bar">
        <span class="floor-track floor-wide" role="img"
              aria-label=${`${(share * 100).toFixed(0)}% of the evidence a model needs`}>
          <span class="floor-fill" style=${`width:${(share * 100).toFixed(1)}%`}></span>
        </span>
        <span class="floor-value">${percent(share, 0)}${" "}ready</span>
      </div>
      ${open ? this.panel(data, admin, busy) : null}
      ${said ? html`<p class="models-said">${said}</p>` : null}
    </section>`;
  }

  panel(data, admin, busy) {
    const evidence = data.evidence || {};
    const learning = data.learning || {};
    const deciding = data.deciding || {};
    const step = nextStep(evidence.floors);
    return html`<div id="modelPanel" class="corr-panel">
      <p class="models-head">What a model needs before it can decide</p>
      ${(evidence.floors || []).map(
        (f) => html`<${Meter} key=${f.key} label=${f.label} have=${f.have} need=${f.need} />`,
      )}
      ${step && ADVICE[step.key]
        ? html`<p class="models-next"><b>Next:</b>${" "}${ADVICE[step.key]}</p>`
        : null}
      ${learning.trace && learning.trace.length > 1
        ? html`<${Series} title="Is it learning?" unit=""
            series=${[{ name: "loss", values: learning.trace }]}
            source="training loss, lower is better"
            span=${`${count(learning.iterations)} passes over ${count(learning.rows)} judged pairs`}
            note="the last point is the model that was kept" />`
        : html`<p class="models-none">
            ${learning.attempts
              ? html`Training has run ${count(learning.attempts)}${" "}
                     ${learning.attempts === 1 ? "time" : "times"} and fitted nothing yet — the
                     bars above say what is missing.`
              : "Training has not run yet."}
          </p>`}
      ${admin ? this.controls(data, deciding, busy) : null}
    </div>`;
  }

  /** The two admin controls, and the reason each is or is not available right now. */
  controls(data, deciding, busy) {
    const runId = data.registerable_run_id;
    const versionId = data.promotable_version_id;
    return html`<div class="models-acts">
      ${runId
        ? html`<button type="button" class="primary" disabled=${busy}
                  onClick=${() => this.register(runId)}>Register this fit</button>`
        : null}
      ${versionId
        ? html`<button type="button" class="primary" disabled=${busy}
                  onClick=${() => this.handOver(versionId)}>
                 Let the model decide
               </button>`
        : null}
      ${!runId && !versionId
        ? html`<p class="models-none">Nothing to register or hand over yet.</p>`
        : null}
      <p class="models-none">
        ${/* A literal `&`, not `&amp;`. `htm` does not decode entities in a template literal, so
              the panel printed `Judge &amp; promotion` on screen — found by reading the rendered
              text rather than the source, which is the only place the two differ. */ null}
        Handing over asks the server, which re-derives the evidence and may refuse. Rolling back
        is one click on ${" "}<b>Judge & promotion</b>.
      </p>
    </div>`;
  }
}
