/* How a judgement teaches the models, as a UML class diagram with live numbers (ADR #484).
 *
 * Five classes, the relations between them and their multiplicities — the notation every engineer
 * reads at a glance: an `Alarm` aggregates into a `Situation` (2..* to 1); a person's judgement on
 * a situation yields `Label`s (1 to 0..*), each a `PositivePair` (confirm) or a `NegativePair`
 * (split) — generalisation, the hollow triangle; labels are kept in the `Corpus`; and the `Model`
 * depends on the corpus (the judge re-ranks every model on it every five minutes).
 *
 * Each class is a button: choosing one says what it is in a sentence and where to act on it. The
 * attribute compartments carry this appliance's numbers, so the diagram is also the summary.
 */

import { html } from "../../dom.js";
import { count } from "../../format.js";

const W = 188;
const ROW = 17;

/** One UML class: name, attributes, operations. */
function UmlClass({ id, x, y, name, attrs, ops = [], active, onPick, stereotype }) {
  const head = stereotype ? 40 : 28;
  const h = head + attrs.length * ROW + 10 + (ops.length ? ops.length * ROW + 10 : 0);
  const split = y + head + attrs.length * ROW + 10;
  return html`<g class=${active ? "uml-class is-active" : "uml-class"} role="button" tabindex="0"
    aria-pressed=${active} aria-label=${`${name}: ${attrs.join(", ")}`}
    onClick=${() => onPick(id)} onKeyDown=${(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(id); } }}>
    <rect x=${x} y=${y} width=${W} height=${h} rx="4" class="uml-box" />
    ${stereotype ? html`<text x=${x + W / 2} y=${y + 15} class="uml-stereo" text-anchor="middle">«${stereotype}»</text>` : null}
    <text x=${x + W / 2} y=${y + head - 9} class="uml-name" text-anchor="middle">${name}</text>
    <line x1=${x} x2=${x + W} y1=${y + head} y2=${y + head} class="uml-rule" />
    ${attrs.map((a, i) => html`<text key=${a} x=${x + 10} y=${y + head + 15 + i * ROW} class="uml-attr">- ${a}</text>`)}
    ${ops.length ? html`<line x1=${x} x2=${x + W} y1=${split} y2=${split} class="uml-rule" />` : null}
    ${ops.map((o, i) => html`<text key=${o} x=${x + 10} y=${split + 15 + i * ROW} class="uml-attr">+ ${o}</text>`)}
  </g>`;
}

/** The diagram. `n` carries the live numbers; `active` the chosen class. */
export function LearningDiagram({ n, active, onPick }) {
  const fmt = (v) => (v == null ? "—" : count(v));
  /** An operation as UML writes it, `name()`. */
  const op = (name) => `${name}()`;
  return html`<svg class="uml" viewBox="0 0 980 312" role="group" aria-label="How a judgement teaches the models">
    <defs>
      <marker id="uml-tri" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="12" markerHeight="12" orient="auto">
        <path d="M1 1 11 6 1 11z" class="uml-tri" /></marker>
      <marker id="uml-diamond" viewBox="0 0 16 10" refX="1" refY="5" markerWidth="16" markerHeight="10" orient="auto">
        <path d="M1 5 8 1 15 5 8 9z" class="uml-tri" /></marker>
      <marker id="uml-open" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto">
        <path d="M1 1 9 5 1 9" class="uml-open" /></marker>
    </defs>

    <path d="M270 92 H 208" class="uml-line" marker-start="url(#uml-diamond)" />
    <text x="262" y="82" class="uml-mult" text-anchor="end">1</text>
    <text x="214" y="108" class="uml-mult">2..*</text>
    <text x="239" y="82" class="uml-role" text-anchor="middle">groups</text>

    <path d="M458 92 H 520" class="uml-line" marker-end="url(#uml-open)" />
    <text x="464" y="82" class="uml-mult">1</text>
    <text x="512" y="108" class="uml-mult" text-anchor="end">0..*</text>
    <text x="489" y="82" class="uml-role" text-anchor="middle">yields</text>

    <path d="M708 92 H 770" class="uml-line" marker-end="url(#uml-open)" />
    <text x="739" y="84" class="uml-role" text-anchor="middle">kept in</text>

    <path d="M864 216 V 131" class="uml-line uml-dep" marker-end="url(#uml-open)" />
    <text x="872" y="178" class="uml-role">«reads»</text>

    <path d="M394 236 V 204 H 614" class="uml-line" />
    <path d="M614 236 V 204" class="uml-line" />
    <path d="M614 204 V 126" class="uml-line" marker-end="url(#uml-tri)" />

    <${UmlClass} id="alarm" x=${20} y=${40} name="Alarm" active=${active === "alarm"} onPick=${onPick}
      attrs=${[`active now: ${fmt(n.alarms)}`, "device, trap class", "severity"]} />
    <${UmlClass} id="situation" x=${270} y=${40} name="Situation" active=${active === "situation"} onPick=${onPick}
      attrs=${[`to judge: ${fmt(n.judge)}`, `proposals: ${fmt(n.proposals)}`]}
      ops=${[op("confirm"), op("split"), `${op("move")} / ${op("merge")}`]} />
    <${UmlClass} id="label" x=${520} y=${40} name="Label" stereotype="abstract" active=${active === "label"} onPick=${onPick}
      attrs=${["pair: (alarm, alarm)", "author: a person"]} />
    <${UmlClass} id="corpus" x=${770} y=${40} name="Corpus" active=${active === "corpus"} onPick=${onPick}
      attrs=${[`pairs kept: ${fmt(n.pairs)}`, `judged alarms: ${fmt(n.judged)}`, `capture: ${n.capture ?? "—"}`]} />
    <${UmlClass} id="model" x=${770} y=${216} name="Model" active=${active === "model"} onPick=${onPick}
      attrs=${["champion decides links"]} ops=${[`${op("judge")} every 5 min`]} />
    <${UmlClass} id="label" x=${300} y=${236} name="PositivePair" active=${active === "label"} onPick=${onPick}
      attrs=${[`from ${op("confirm")}`]} />
    <${UmlClass} id="label" x=${520} y=${236} name="NegativePair" active=${active === "label"} onPick=${onPick}
      attrs=${[`from ${op("split")}`]} />
  </svg>`;
}
