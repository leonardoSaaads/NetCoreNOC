/* "Why these were grouped" — the screen that carries principle 2, redesigned (V.6, #245).
 *
 * ## What was wrong with it
 *
 * It rendered `links.slice(0, 30)`: thirty rows of three-term decomposition, with a paragraph
 * above them, and a line saying how many were not shown. In a four-alarm situation that is the
 * right screen. In a 400-trap storm it is thirty rows out of thousands, chosen by insertion
 * order — and an operator who needs to know **whether the grouping is sound** has to read a
 * sample nobody selected and draw a conclusion from it.
 *
 * The question a storm asks is not "what is link seventeen's temporal term". It is *"can I trust
 * this grouping?"*, and after that, sometimes, *"show me the pair that made you say so"*.
 *
 * ## What replaces it
 *
 * A **summary** that answers the first question from every link rather than from thirty, and the
 * per-link decomposition **one interaction away and complete when opened** — not truncated, which
 * is the half that makes principle 2 still true. Three facts, and each is on screen because it
 * changes what an operator does:
 *
 *   * **the weakest link and its margin over the threshold.** A grouping whose weakest pair
 *     cleared by 0.01 is one scorer nudge from falling apart; one whose weakest cleared by 0.3 is
 *     not. This is the single most useful number on the screen and it was not on it.
 *   * **which term is carrying the grouping**, as the mean contribution of each across every link.
 *     If temporal carries it alone, these alarms are close in time and nothing else — a storm
 *     coincidence. If entity affinity carries it, the appliance is grouping on structure it has
 *     learned. Those two situations look identical in a list of scores.
 *   * **how many links there are**, so the operator knows what "open the detail" will cost.
 *
 * ## What this deliberately does NOT do
 *
 * It does not compute a verdict. Every number here is an aggregate of numbers the server already
 * sent, by arithmetic a reader can check in the source, and none of it is a judgement the
 * appliance has not made. A console that scored its own groupings would be a second scorer
 * (F28's shape at a different altitude), and the one that decides is `engine/correlate`.
 *
 * It is also not situation-lifecycle work: nothing here proposes a state, a merge, a split or a
 * rename. That is v0.16.0 and `docs/plans/v0.16.0-situation-lifecycle.md` carries what was noticed
 * while working in this file.
 */

import { html, Component, cx } from "../../dom.js";
import { Icon } from "../../icons.js";
import { score, alarmName } from "../../format.js";

const TERM_KEY = { temporal: "t", class_affinity: "a", entity_affinity: "e" };
const TERM_LABEL = {
  temporal: "T — how close in time",
  class_affinity: "A — how often these alarm classes co-occur",
  entity_affinity: "E — how often these devices co-occur",
};
/** What it means when a term dominates. One clause each, because the fact needs the reading. */
const TERM_MEANING = {
  temporal: "these alarms are close in time",
  class_affinity: "these alarm classes have co-occurred before",
  entity_affinity: "these devices have co-occurred before",
};

/** The three named terms, from the scorer's own list, falling back to the legacy columns. */
export function termsOf(link) {
  if (Array.isArray(link.terms) && link.terms.length) return link.terms;
  return [
    { name: "temporal", contribution: link.term_t },
    { name: "class_affinity", contribution: link.term_a },
    { name: "entity_affinity", contribution: link.term_e },
  ];
}

/**
 * Aggregate every link — not a sample. Arithmetic only: min, max, and a mean per term.
 *
 * Returns null for an empty list rather than a zeroed object, so a caller cannot render
 * "weakest 0.00" about a situation that has no links at all.
 */
export function summarise(links) {
  if (!links || !links.length) return null;
  const scores = links.map((l) => l.score);
  const totals = new Map();
  for (const link of links) {
    for (const term of termsOf(link)) {
      totals.set(term.name, (totals.get(term.name) ?? 0) + (term.contribution ?? 0));
    }
  }
  const means = [...totals.entries()]
    .map(([name, total]) => ({ name, mean: total / links.length }))
    .sort((a, b) => b.mean - a.mean);
  return {
    count: links.length,
    weakest: Math.min(...scores),
    strongest: Math.max(...scores),
    means,
    carrying: means[0],
  };
}

export function WhyGrouped({ links, byId, threshold }) {
  const all = links || [];
  const summary = summarise(all);
  if (!summary) {
    return html`<section class="why">
      <h3>Why these were grouped</h3>
      <p class="hint">This situation has one member, so there is no link to explain.</p>
    </section>`;
  }
  return html`<section class="why">
    <h3>Why these were grouped</h3>
    <${Soundness} summary=${summary} threshold=${threshold} />
    <${LinkDetail} links=${all} byId=${byId} count=${summary.count} />
  </section>`;
}

/* The icon per band, as a table rather than a ternary inside the element.
 *
 * `tests/test_icons.py` reads the quoted strings inside an `<${Icon} …/>` element, so
 * `name=${band === "thin" ? "warn" : "info"}` made it ask for an icon called "thin" — the
 * ternary's CONDITION. The guard could be taught to parse the expression; a lookup keyed on the
 * band is the better answer, because the alternative is a guard with its own JavaScript parser. */
const BAND_ICON = { thin: "warn", fair: "info", wide: "info", unknown: "info" };

/** The answer to "is this grouping sound?", from every link. */
function Soundness({ summary, threshold }) {
  const margin = threshold != null ? summary.weakest - threshold : null;
  // Three bands, and the words change with the band as well as the colour — a margin read off a
  // colour alone fails the same operator the severity rules are written for.
  const band = margin == null ? "unknown" : margin < 0.05 ? "thin" : margin < 0.15 ? "fair" : "wide";
  return html`<div class=${cx("soundness", `soundness-${band}`)}>
    <div class="stat-row">
      <div class="stat">
        <div class="stat-value">${score(summary.weakest)}</div>
        <div class="stat-label">weakest link</div>
        <div class="stat-note">${margin == null
          ? "the threshold was not reported"
          : `${score(margin)} above the threshold of ${score(threshold)}`}</div>
      </div>
      <div class="stat">
        <div class="stat-value">${score(summary.strongest)}</div>
        <div class="stat-label">strongest link</div>
        <div class="stat-note">${summary.count === 1 ? "the only link" : "of the links below"}</div>
      </div>
      <div class="stat">
        <div class="stat-value">${summary.count}</div>
        <div class="stat-label">${summary.count === 1 ? "link" : "links"}</div>
        <div class="stat-note">every pair scored above the threshold</div>
      </div>
    </div>
    ${margin != null ? html`<p class=${cx("hint", band === "thin" && "err")}>
      <${Icon} name=${BAND_ICON[band]} />${" "}
      ${band === "thin"
        ? "The weakest pair cleared the threshold by a small margin, so this grouping is "
          + "sensitive to a change in the scorer."
        : band === "wide"
          ? "Every pair cleared the threshold comfortably."
          : "Every pair cleared the threshold."}
    </p>` : null}
    <${Carrying} means=${summary.means} />
  </div>`;
}

/**
 * Which term is doing the work, averaged over every link.
 *
 * A bar AND the number AND the term's name, never the bar alone — the same rule the per-link rows
 * follow, and for the same reason: a bar cannot be read off a bad monitor or copied into a ticket.
 */
function Carrying({ means }) {
  const top = means[0];
  const total = means.reduce((n, m) => n + m.mean, 0) || 1;
  return html`<div class="carrying">
    <p class="hint">Averaged over every link, the grouping is carried by${" "}
      <b>${TERM_LABEL[top.name]?.split(" — ")[0] ?? top.name}</b>${" — "}
      ${TERM_MEANING[top.name] ?? "an unnamed term"}.</p>
    <ul class="term-means">
      ${means.map((m) => html`<li key=${m.name}>
        <span class="term-mean-label">${TERM_LABEL[m.name] ?? m.name}</span>
        <span class=${cx("term-bar", `term-${TERM_KEY[m.name] ?? "t"}`)}
              style=${{ width: `${Math.max(2, Math.round(160 * (m.mean / total)))}px` }}
              aria-hidden="true"></span>
        <span class="term-num">${score(m.mean)} mean</span>
      </li>`)}
    </ul>
  </div>`;
}

/**
 * Every link, complete, behind one interaction.
 *
 * **Complete is the point.** The old version rendered `slice(0, 30)` and said how many it had
 * hidden, which means the per-term contributions — the product's central claim — were unreachable
 * for link thirty-one onwards on any device at all. They are all here once opened; what is behind
 * the interaction is the *cost* of drawing them, not the facts.
 */
class LinkDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { open: false };
  }

  render({ links, byId, count }, { open }) {
    return html`<div class="link-detail">
      <button type="button" class="link-detail-toggle" aria-expanded=${open ? "true" : "false"}
              aria-controls="why-links"
              onClick=${() => this.setState({ open: !open })}>
        <span class=${open ? "sit-chevron open" : "sit-chevron"}><${Icon} name="chevron" /></span>
        ${open ? "Hide" : "Show"} the per-term contribution of${" "}
        ${count === 1 ? "the link" : `all ${count} links`}
      </button>
      <ol class="links" id="why-links" hidden=${!open}>
        ${open ? links.map((link, index) => html`<li class="linkrow" key=${index}>
          <span class="linkscore" title="the sum of the three terms below">${score(link.score)}</span>
          <${TermBar} link=${link} />
          <span class="linkpair">
            ${nameOf(byId, link.alarm_a)} <span aria-hidden="true">↔</span>
            ${nameOf(byId, link.alarm_b)}
          </span>
        </li>`) : null}
      </ol>
    </div>`;
  }
}

function nameOf(byId, alarmId) {
  const alarm = byId.get(alarmId);
  return alarm ? alarmName(alarm) : `alarm ${alarmId}`;
}

export function TermBar({ link }) {
  const terms = termsOf(link);
  const title = terms
    .map((t) => `${TERM_LABEL[t.name] ?? t.name}: ${score(t.contribution)}`)
    .join("\n");
  return html`<span class="terms" title=${title}>
    ${terms.map((t) => html`<span class="term" key=${t.name}>
      <span class=${cx("term-bar", `term-${TERM_KEY[t.name] ?? "t"}`)}
            style=${{ width: `${Math.max(2, Math.round(120 * (t.contribution / 0.95)))}px` }}
            aria-hidden="true"></span>
      <span class="term-num">${(TERM_KEY[t.name] ?? "?").toUpperCase()} ${score(t.contribution)}</span>
    </span>`)}
  </span>`;
}
