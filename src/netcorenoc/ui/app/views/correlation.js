/* Correlator: is the thing deciding my links any good, and how would I know?
 *
 * **The screen v0.18.0's Part II exists for.** Until this release the console drew the promotion
 * history, the feedback corpus and the judge's verdict — everything about a *challenger* — and
 * nothing whatsoever about the scorer that was actually running. An operator could read the five
 * parameters on the Link scorer screen and could not find out whether they were working.
 *
 * Every figure comes from `GET /api/correlation`, which counts the champion's own decisions as it
 * makes them. Nothing here is stored, so every number is *since this appliance started* and the
 * captions say so rather than implying a history the appliance does not keep.
 *
 * **Two horizons on every rate.** One number cannot answer *"is this normal?"*. Lifetime and the
 * last N activations can, and the gap between them is the drift signal — with no stored history
 * and no second mechanism to keep in step.
 *
 * **This screen is an observability surface and it is not evidence.** It offers no control that
 * records an opinion, and the route it reads is read-only. Only a human gesture is evidence for
 * promotion; measuring the champion is not labelling it.
 */

import { html } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty, SectionHeading, Stat, DataTable } from "../widgets.js";
import { Bars } from "../compare.js";
import { count, percent, plural, score } from "../format.js";

/** A rate the appliance has not measured renders `—`, never `0 %` (#289). */
function rate(value) {
  return value == null ? "—" : percent(value, 1);
}

/**
 * How the two horizons compare, as a word rather than a second percentage.
 *
 * An operator reading `48.2 %` beside `49.6 %` has to do the arithmetic to learn that nothing
 * has changed. The comparison is the point of showing both, so the screen does it. The band is
 * generous on purpose: this is a "look here" marker, not a test, and a console that cried drift
 * at every wobble would be one nobody reads.
 */
function drift(recent, lifetime) {
  if (recent == null || lifetime == null) return null;
  const delta = recent - lifetime;
  if (Math.abs(delta) < 0.05) return { tone: null, text: "steady" };
  return {
    tone: "warn",
    text: `${delta > 0 ? "up" : "down"} ${percent(Math.abs(delta), 1)} on lifetime`,
  };
}

export class Correlation extends Loader {
  constructor(props) {
    super(props);
    this.what = "the correlator's own figures";
    this.loadingLabel = "Reading what the correlator is deciding";
  }

  async load() { return get("/api/correlation"); }

  view(data) {
    const life = data.lifetime || {};
    const recent = data.recent || {};
    if (!life.activations) {
      return html`<${Empty}
        title="The correlator has not decided anything yet."
        will=${"This screen fills in as soon as a second alarm arrives: it counts the pairs the " +
              "scorer evaluates, how many it links, how sure it is, and which of the three terms " +
              "is carrying the decisions."}
        meanwhile=${"Send some traps — `make replay` sends a bundled scenario — and come back. " +
                    "Every figure here is since this appliance started and is not stored."} />`;
    }
    const acceptDrift = drift(recent.accept_rate, life.accept_rate);
    // **No heading here.** The shell's `work-heading` already renders the view's label and its
    // registry summary; a second `<h2>Correlator</h2>` printed the title twice, which the live
    // browser pass caught and no other view does. What is left is the sentence the summary has
    // no room for and an operator needs: the horizon, and that none of this is evidence.
    return html`<div class="correlation">
      <p class="hint">
        Every figure is since this appliance started — nothing on this screen is stored, and
        nothing on it is evidence.
      </p>

      ${this.running(data)}
      ${this.deciding(life, recent, acceptDrift)}
      ${this.confidence(data, life, recent)}
      ${this.carrying(data)}
      ${this.grouping(life, recent)}
    </div>`;
  }

  /** What is actually running — the identity half of "is it any good". */
  running(data) {
    const run = data.running || {};
    return html`<section class="panel-block">
      <${SectionHeading} title="What is running"
        hint=${`the active scorer, and the window and candidate cap it decides inside`} />
      <div class="stat-row">
        <${Stat} label="Scorer" value=${run.scorer_id || "—"}
          note=${run.tunable ? "tunable" : "fixed parameters"} />
        <${Stat} label="Contract" value=${run.contract_version || "—"}
          note=${data.config_id == null ? "coded defaults" : `config #${data.config_id}`} />
        <${Stat} label="Link threshold" value=${score(data.threshold)}
          note="a pair links above this" />
        <${Stat} label="Window" value=${`${count(data.window_s)} s`}
          note=${`at most ${count(data.max_candidates)} candidates per alarm`} />
      </div>
      ${data.degraded
        ? html`<p class="warnbox">
            The configured scorer failed and the appliance fell back to the built-in defaults:
            ${" "}${data.degraded_reason}. Every figure below is the fallback's behaviour.
          </p>`
        : null}
    </section>`;
  }

  /** Is it deciding, and how often does it say yes? */
  deciding(life, recent, acceptDrift) {
    return html`<section class="panel-block">
      <${SectionHeading} title="What it is deciding"
        hint="a candidate pair is one alarm scored against one other alarm in the window" />
      <div class="stat-row">
        <${Stat} label="Activations" value=${count(life.activations)}
          note="alarms that reached the correlator" />
        <${Stat} label="Pairs evaluated" value=${count(life.evaluated)}
          note=${`${count(life.linked)} linked, ${count(life.refused)} refused`} />
        <${Stat} label="Accept rate" value=${rate(life.accept_rate)}
          note="lifetime" />
        <${Stat} label="Accept rate, recent" value=${rate(recent.accept_rate)}
          tone=${acceptDrift && acceptDrift.tone}
          note=${acceptDrift
            ? `${acceptDrift.text} · last ${plural(recent.window_activations, "activation")}`
            : `last ${plural(recent.window_activations, "activation")}`} />
      </div>
    </section>`;
  }

  /** How sure is it? The distribution, and the band where it is closest to guessing. */
  confidence(data, life, recent) {
    const hist = data.histogram || { buckets: [], edges: [] };
    const rows = (hist.buckets || []).map((value, i) => ({
      key: String(i),
      label: `${score(hist.edges[i])}–${score(hist.edges[i + 1])}`,
      value,
      // The bucket the threshold falls in is the one an operator should look at hardest.
      tone: hist.edges[i] <= data.threshold && data.threshold < hist.edges[i + 1] ? "warn" : null,
      title: "candidate pairs whose score landed in this range",
    }));
    return html`<section class="panel-block">
      <${SectionHeading} title="How sure it is"
        hint=${`a decision within ${score(data.near_threshold_band)} of the threshold would flip ` +
               `on a small move in either affinity — it is close to a coin toss`} />
      <div class="stat-row">
        <${Stat} label="Near the threshold" value=${rate(life.near_threshold_rate)}
          tone=${(life.near_threshold_rate ?? 0) > 0.25 ? "warn" : null}
          note=${`${count(life.near_threshold)} of ${count(life.evaluated)} pairs, lifetime`} />
        <${Stat} label="Near the threshold, recent" value=${rate(recent.near_threshold_rate)}
          note=${`last ${plural(recent.window_activations, "activation")}`} />
      </div>
      <${Bars} title="Candidate pairs by score" rows=${rows} unit="pairs"
        source="/api/correlation, counted as the scorer decided"
        note=${`the highlighted row contains the ${score(data.threshold)} threshold; ` +
               `pairs above it linked`} />
    </section>`;
  }

  /** Which term is carrying the links — the drift signal that matters most. */
  carrying(data) {
    const carried = data.carried_by || {};
    const total = Object.values(carried).reduce((sum, n) => sum + n, 0);
    const rows = Object.entries(carried).map(([name, n]) => ({
      key: name,
      label: name.replace(/_/g, " "),
      value: n,
      tone: name === "entity_affinity" && total && n / total > 0.8 ? "warn" : null,
      title: "activations whose strongest accepted link was carried by this term",
    }));
    return html`<section class="panel-block">
      <${SectionHeading} title="What is carrying the links"
        hint=${"the term contributing most to the strongest link each activation made"} />
      ${total
        ? html`<${Bars} title="Strongest link, by dominant term" rows=${rows} unit="activations"
            source="/api/correlation, since this appliance started"
            note=${"if learned cross-element affinity carries almost everything, the appliance " +
                   "is grouping on co-occurrence alone — six alarms are enough to establish it"} />`
        : html`<p class="hint">No link has been accepted yet, so no term has carried one.</p>`}
    </section>`;
  }

  /** What the decisions did to the grouping. */
  grouping(life, recent) {
    const columns = [
      { key: "metric", label: "" },
      { key: "life", label: "Lifetime", numeric: true },
      { key: "recent", label: "Recent", numeric: true },
    ];
    // `DataTable` takes each row's values under `cells`, keyed by column. Writing them at the
    // top level renders nothing and throws on the first column — which is how the live browser
    // pass found this before the screen ever shipped.
    const per = (value) => (value == null ? "—" : score(value, 3));
    const rows = [
      {
        key: "merges",
        cells: {
          metric: "Situations fused by an arriving alarm",
          life: count(life.merges),
          recent: count(recent.merges),
        },
      },
      {
        key: "per",
        cells: {
          metric: "Fusions per activation",
          life: per(life.merges_per_activation),
          recent: per(recent.merges_per_activation),
        },
      },
      {
        key: "storm",
        cells: {
          metric: "Activations during a storm",
          life: count(life.storm_activations),
          recent: count(recent.storm_activations),
        },
      },
      {
        key: "subtree",
        cells: {
          metric: "Pairs refused the cross-element term (different vendor subtrees)",
          life: count(life.subtree_suppressed),
          recent: count(recent.subtree_suppressed),
        },
      },
    ];
    return html`<section class="panel-block">
      <${SectionHeading} title="What it did to the grouping"
        hint=${"a fusion is one arriving alarm joining two situations that were separate; a " +
               "rising rate is the over-merge signature"} />
      <${DataTable} columns=${columns} rows=${rows}
        caption="counted as the engine assigned each alarm to a situation"
        empty="nothing yet" />
    </section>`;
  }
}
