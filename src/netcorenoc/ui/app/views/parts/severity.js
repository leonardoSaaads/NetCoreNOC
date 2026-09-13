/* Band 1: how bad is it, right now (v0.16.7, DECISIONS #312).
 *
 * ## The question this release exists to answer
 *
 * *"How many critical alarms are active right now, and is that number growing?"* The first half is
 * here. The second half **cannot be drawn** and the reason is recorded rather than approximated:
 * `alarm.first_seen` spans **1.14 seconds** across the whole three-scenario estate and
 * `alarm.cleared_at` is non-null on **none** of it, so a time axis would be a claim about a corpus
 * rather than a statement about the data (#313).
 *
 * ## Why there are six rows and not four
 *
 * `engine/correlate/severity.py` names a severity only when a vocabulary match **and** an
 * ordinality check against observed alarm lifetimes agree, and refuses otherwise: *a fabricated
 * severity is worse than none.* Measured across all ten shipped scenarios — 2 119 alarms, **2 119
 * with `severity IS NULL`** — that refusal covers every alarm the corpus produces, because
 * `confirm_ordinality` needs fifty closed alarms and the corpus closes **one** (#314).
 *
 * So the four bands are not the answer. **The answer is four bands, `indeterminate`, and
 * `unplaced`**, and on day one the last row holds everything. A panel that drew four zeros beside
 * it would be describing a quiet network, which is the one lie this screen must not tell — and it
 * is the lie that would have looked like a feature.
 *
 * ## Four encodings, none of them colour alone
 *
 * Each row carries a **glyph** (a distinct shape per band), its **position** in the scale, its
 * **bar width**, and its **printed count**. `format.js::band` is the only place a rank becomes a
 * name, so this panel and an alarm's own pill cannot disagree about what rank 3 is called.
 */

import { html, cx } from "../../dom.js";
import { Bars } from "../../charts.js";
import { band, count, plural } from "../../format.js";

/**
 * The ranks the bundled vocabulary issues, **derived from `format.js::band`** rather than listed.
 *
 * `known_oids.SEVERITY_VOCAB` ranks `critical 0 … indeterminate/cleared 4`, and `store/read_models`
 * pins `VOCAB_MAX_RANK` against that dictionary. Walking 0 upward and stopping where `band()` stops
 * knowing the rank means a band added to the vocabulary appears here without this file being
 * edited — F92's finding, which is the fourth trap in this project's own list: *a guard that lists
 * what it checks*. The same argument applies to a panel that lists what it can show.
 */
const SCALE = (() => {
  const out = [];
  for (let rank = 0; band(rank).key !== "unknown"; rank += 1) out.push(rank);
  return out;
})();

/**
 * The rank the vocabulary uses for *"I do not know how serious this is"*.
 *
 * It is one past the last band `format.js` draws, which is exactly what `VOCAB_MAX_RANK` means on
 * the other side of the wire. It is **not** the same as `unplaced`: `indeterminate` is a severity
 * the appliance was *given* and whose own meaning is uncertainty; `unplaced` is a severity it was
 * never able to read at all. Folding them would lose the difference between an NE that answered
 * "don't know" and one that never answered.
 */
const INDETERMINATE = SCALE.length;

/**
 * **How bad is it** — active alarms by band, most severe first.
 *
 * `census` is `/api/stats.severity`: `{active, placed: {rank: n}, unplaced, vendor_scaled,
 * declared}`. Every one of those is a count of alarms the principal can see, filtered in SQL
 * (F35/F38), so a scoped viewer's rows never include an element they cannot open.
 */
export function Severity({ census }) {
  const known = census != null;
  const placed = (known && census.placed) || {};
  const raw = (rank) => (known ? Number(placed[String(rank)] || 0) : 0);
  const unplaced = known ? Number(census.unplaced || 0) : null;
  const vendorScaled = known ? Number(census.vendor_scaled || 0) : 0;
  const active = known ? Number(census.active || 0) : null;
  // What the appliance has actually placed on the scale. Stated beside the total rather than
  // subtracted from it, because "1 684 active" and "0 placed" are two facts and neither explains
  // the other away.
  const placedTotal = known
    ? SCALE.reduce((sum, rank) => sum + raw(rank), 0) + raw(INDETERMINATE) + vendorScaled
    : null;

  /**
   * **The band counts, and the one case in which a band is `—` rather than `0`.**
   *
   * A `0` here is a claim: *the appliance looked at every active alarm and none of them is
   * critical.* That claim is true only when it has placed something. When it has placed nothing
   * — the state of every corpus this repository ships — the honest reading of "how many are
   * critical" is **not measured**, and prime directive 1 says so in as many words: an unreadable
   * metric renders `—`, never `0`.
   *
   * This distinction is the whole release. `0 critical of 1 684 active alarms` was on screen, in
   * display type, over an estate the appliance cannot grade — found by looking at it, because
   * every assertion about the panel was green.
   */
  const graded = known && (placedTotal > 0 || active === 0);
  const at = (rank) => (graded ? raw(rank) : null);

  const rows = [
    ...SCALE.map((rank) => {
      const one = band(rank);
      return {
        key: one.key,
        label: `${one.glyph} ${one.label}`,
        value: at(rank),
        tone: rank === 0 ? "alarm" : rank === 1 ? "warn" : null,
        title: `Active alarms the appliance has placed at ${one.label}.`,
      };
    }),
    {
      key: "indeterminate",
      label: `${band(INDETERMINATE).glyph} indeterminate`,
      value: at(INDETERMINATE),
      tone: "quiet",
      title:
        "The element named a severity, and the word it used means 'I do not know how serious " +
        "this is'. That is an answer, and it is not the same as never having answered.",
    },
    ...(vendorScaled
      ? [
          {
            key: "vendor",
            label: "# vendor scale",
            value: vendorScaled,
            tone: "quiet",
            title:
              "A severity the appliance learned as a vendor's own numbering. Where it sits on " +
              "the scale depends on that element's other values, so it is counted here and " +
              "placed on the alarm itself.",
          },
        ]
      : []),
    {
      key: "unplaced",
      // **Its own glyph, and not the `?` `indeterminate` carries.** Two rows sharing a shape are
      // one encoding, which is what the accessibility floor forbids and what F-numbered findings
      // in this project keep being about. `∅` is the empty set: nothing was read at all.
      label: "∅ not placed",
      value: unplaced,
      tone: "quiet",
      title:
        "The appliance has not been able to read a severity for these alarms. It learns severity " +
        "from the trap stream and refuses to guess one.",
    },
  ];

  return html`<section class="panel-block severity-band">
    ${/* **The headline is a number, not a heading.** Every other band on this screen opens with
          `SectionHeading`; this one opens with the count an operator is looking for, because a
          heading reading "How bad is it" above a number is the screen describing itself. The
          `<h2>` is the number's own label and the reading order keeps it. */ null}
    <h2 class=${cx("severity-head", !graded && "severity-ungraded")}>
      <span class="severity-now">${graded ? count(at(0)) : "—"}</span>${" "}
      <span class="severity-word">critical</span>${" "}
      <span class="severity-of">
        of ${known ? plural(active, "active alarm") : "an unread count"}</span>
    </h2>
    ${known && !graded && active > 0
      ? html`<p class="warnbox severity-none">Severity is <b>not measured</b> on any of them: the
          appliance learns it from the trap stream and has not been able to yet.</p>`
      : null}
    <${Bars} title="Active alarms by severity" unit="alarms" rows=${rows}
      source="/api/stats.severity — an operator's declaration first, then what the appliance learned"
      note=${known
        ? `${count(placedTotal)} placed, ${count(unplaced)} not placed` +
          (census.declared ? `; ${count(census.declared)} from a declaration` : "")
        : "the appliance did not report a severity census"} />
  </section>`;
}
