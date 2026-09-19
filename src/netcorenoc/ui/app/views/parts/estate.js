/* The Graph screen's **second projection and its two derived tables** — everything on that screen
 * that a test actually executes (v0.16.6, DECISIONS #307, #310).
 *
 * ## The seam is the one `views/graph.js` has described since v0.13.0
 *
 * That file's header says the force drawing is *"the one screen in this console that no test
 * executes"*, and that the two tables beside it carry what it encodes as text. This release adds a
 * **second drawing that IS executed**, so the honest boundary is no longer drawing-versus-text — it
 * is d3-versus-hand-written. Everything in this module is hand-written, produces real DOM, and is
 * asserted. Everything left in `graph.js` is the d3 scene and is asserted by nothing.
 *
 * Split out because `graph.js` reached the module graph's 17 579-byte ceiling, which is the eighth
 * time that guard has chosen a seam and the first time it chose one this useful.
 *
 * ## Why a second projection at all
 *
 * The force drawing answers *which elements are related*, and it is the right drawing for that. It
 * cannot answer *is this one element or the whole estate*, for two measured reasons:
 *
 *   * **the radius saturates.** `min(24, 7 + 2.5 * sqrt(active_alarms))` hits its ceiling at 47
 *     alarms — F77 put that ceiling there deliberately — so on this project's own corpus
 *     `127.0.0.1` at **1 359** active alarms and `127.0.0.4` at **501** both draw at exactly
 *     **24.0 px**. The one screen built to say which host is worst cannot distinguish them.
 *   * **the layout is not deterministic.** Positions come from a simulation with drag and a
 *     re-centring force, so two glances at an unchanged estate do not agree, and an operator cannot
 *     compare this morning with now.
 */

import { html } from "../../dom.js";
import { DataTable, SectionHeading } from "../../widgets.js";
import { Map as EstateMap } from "../../compare.js";
import { count, plural, score } from "../../format.js";

/** How many rows each derived table shows. Enough to answer the question, short enough to read. */
const TOP_N = 10;

/** The name an operator gave an element, falling back to what the network calls it. */
function displayName(node) { return node.label || node.ip; }

/**
 * The load at which a mark is urgent, and it is the **same constant the Overview's map uses**.
 *
 * 47 is where the force drawing's radius saturates, so it is exactly the load above which that
 * projection stops telling two elements apart — which is why the other projection starts shouting
 * there. Two drawings agreeing about which elements are urgent is worth a shared constant.
 */
export const URGENT_AT = 47;

/**
 * **The second projection: the estate as a deterministic grid** (DECISIONS #310).
 *
 * Same primitive as the Overview's network map, called with this screen's caption — one component
 * in two places, which is what decision 2's three-type ceiling is for. **And this one is executed
 * by tests**, so the gap `graph.js` has declared since v0.13.0 is now closed for one of the two
 * drawings on the screen. It is not closed for the other, and that sentence stays where it is.
 */
export function Projection({ nodes }) {
  return html`<section class="panel-block">
    <${EstateMap} title="The same estate, ordered by load"
      hint=${"The drawing above answers which elements are RELATED. This answers whether the " +
             "load is one element or the whole estate — which the drawing cannot, because a " +
             "node's radius stops growing at 24 px and its layout moves between glances."}
      cells=${nodes.map((node) => ({
        key: String(node.id),
        label: node.label || node.ip,
        value: node.active_alarms,
      }))}
      urgentAt=${URGENT_AT}
      source="/api/graph, live · deterministic: sorted by load, then by id"
      note=${`pulses above ${URGENT_AT} active alarms — where the drawing's radius saturates`} />
  </section>`;
}

/**
 * Which elements are alarming most. **Derived from the payload this screen already had.**
 *
 * `/api/graph` has served `active_alarms` on every node since v0.13.0 and the drawing has encoded
 * it in a radius ever since — a radius that is *capped* at 24 px (F77), so the one screen that
 * answers "which host is worst" answered it in a quantity that saturates. The number was on the
 * wire and thrown away, which is exactly what v0.15.2 found on `/api/stats`: eleven keys served,
 * five rendered. **No new route was needed and none was added** (Part VII rule 2).
 *
 * **v0.16.3 removed the rename from this screen**, and that is a repair rather than a loss. It was
 * here because there was nowhere else — a `globalThis.prompt` on double-click, then a button in
 * this table — and it wrote `label(kind='device', target_id=node.id)` while the Entities screen
 * read the `ne` table, so the name an operator gave a host was invisible on the screen built to
 * describe that host. An element is now named where the operator already is: the row in a
 * situation's member table, the same place its class and its severity are declared. This screen
 * **reads** what was declared there, which is the propagation the release exists to build.
 *
 * **No new gesture is invented here** (Part VII rule 5): the graph shows learned affinities between
 * elements, and an assertion that two elements are unrelated is not in
 * `PREREGISTRATION-0.16.0.md` §2's registered map. The grouping an operator can correct is a
 * *situation*, so each row links to the search that finds this element's situations, where the
 * five registered gestures — and now the three declarations — live.
 */
export function Busiest({ nodes }) {
  const rows = [...nodes]
    .filter((node) => node.active_alarms > 0)
    .sort((a, b) => b.active_alarms - a.active_alarms || String(a.id).localeCompare(String(b.id)))
    .slice(0, TOP_N);
  if (!rows.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="Elements alarming most"
      hint=${"The exact counts the drawing can only approximate: a node's radius stops growing " +
             "at 24 px, so a storm and a busy hour look alike there and do not here."} />
    <${DataTable} columns=${[
      { key: "device", label: "element" },
      // v0.16.4 (F105, DECISIONS #292): the **vendor** column is gone. Nothing has ever written
      // `ne.vendor` — 25 rows, 0 vendors, after 2 252 alarms — and its tooltip, *"inferred from
      // the enterprise arc of the OID"*, described `alarm_class`, a different table. An operator
      // read `unknown` as "the appliance could not identify this device"; the truth was that
      // nothing ever tried.
      { key: "alarms", label: "active alarms", numeric: true },
      { key: "act", label: "" },
    ]} rows=${rows.map((node) => ({
      key: node.id,
      tone: "alarm",
      cells: {
        device: displayName(node),
        alarms: count(node.active_alarms),
        act: html`<a class="tap" href=${`#/situations?q=${encodeURIComponent(displayName(node))}`}
                     title="Find this element's situations, where it is named and its grouping corrected"
                  >situations</a>`,
      },
    }))} />
  </section>`;
}

/**
 * Which relationships are strongest — the maintainer's second question, in the words they used:
 * *"if a host's alarms always affect another, show that relationship."*
 *
 * `weight` is the learned affinity and `n` is the co-occurrence mass behind it, and **both were
 * already on every edge** the drawing renders as opacity and thickness. Two encodings of one
 * number, and no way to read the number.
 *
 * `n` is shown beside the weight rather than folded into it, and that is not decoration: a pair
 * seen six times can reach an affinity of 0.83 (F61, measured), so a strong-looking edge with a
 * small `n` is a claim from very little evidence. A table that printed the affinity alone would
 * present those two as the same fact.
 */
export function Strongest({ graph }) {
  const named = new Map(graph.nodes.map((node) => [node.id, displayName(node)]));
  const rows = [...graph.edges]
    .filter((edge) => named.has(edge.a_id) && named.has(edge.b_id))
    .sort((a, b) => b.weight - a.weight || b.n - a.n)
    .slice(0, TOP_N);
  if (!rows.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="Strongest learned relationships"
      hint=${"How often two elements' alarms have appeared together, as a number rather than as " +
             "an opacity. Evidence is the second column and it is not optional: a pair seen a " +
             "handful of times can already score highly, and that is a weaker claim than the " +
             "same score over hundreds of observations."} />
    <${DataTable} columns=${[
      { key: "pair", label: "pair" },
      { key: "weight", label: "affinity", numeric: true },
      { key: "n", label: "evidence (n)", numeric: true,
        title: "co-occurrence mass; an edge is not drawn at all below the learned minimum" },
    ]} rows=${rows.map((edge) => ({
      key: `${edge.a_id}-${edge.b_id}`,
      cells: {
        pair: `${named.get(edge.a_id)} ↔ ${named.get(edge.b_id)}`,
        weight: score(edge.weight),
        n: score(edge.n),
      },
    }))} />
  </section>`;
}
