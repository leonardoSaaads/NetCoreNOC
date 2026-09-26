/* Band 1: how bad is it, right now (v0.16.7, #312; chips in v0.22.0, item 3).
 *
 * Active alarms by band — the four X.733 placements, `indeterminate`, and `unplaced`. **`unplaced`
 * is a first-class row and never a zero**: the appliance names a severity only when the trap
 * carried one, an operator declared or imported one, or it learned one, and refuses otherwise.
 * When nothing at all is placed, the band counts render `—` rather than `0`, because a zero would
 * claim "none are critical" about alarms nobody could grade.
 *
 * Every row is a chip — shape, colour and word — plus a bar and its count: no meaning by colour.
 */

import { html, cx } from "../../dom.js";
import { Bars } from "../../compare.js";
import { SeverityChip, severityTone } from "../../widgets.js";
import { SCALE, UNPLACED, count, plural } from "../../format.js";
import { InfoTip } from "../../info.js";

/** Where a severity came from, in an operator's words. An unknown key is shown raw, never dropped. */
const SOURCES = {
  declared: "declared by an operator",
  imported: "from an imported file",
  standard: "the trap's own word",
  learned: "learned from the stream",
  vendor: "a bundled vendor row",
  builtin: "the built-in trap pack",
};

/** `1,467 placed — 1,467 the trap's own word`: the arms present, largest first. */
function provenanceNote(census) {
  const arms = Object.entries(census.provenance || {})
    .map(([key, value]) => [key, Number(value) || 0])
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1]);
  return arms.map(([key, value]) => `${count(value)} ${SOURCES[key] ?? key}`).join(", ");
}

/**
 * **How bad is it.** `census` is `/api/stats.severity`: `{active, placed: {rank: n}, unplaced,
 * vendor_scaled, provenance}`, every count filtered to the principal's scope in SQL (F35/F38).
 */
export function Severity({ census }) {
  const known = census != null;
  const placed = (known && census.placed) || {};
  const raw = (rank) => Number(placed[String(rank)] || 0);
  const active = known ? Number(census.active || 0) : null;
  const unplaced = known ? Number(census.unplaced || 0) : null;
  const vendorScaled = known ? Number(census.vendor_scaled || 0) : 0;
  const placedTotal = SCALE.reduce((sum, b) => sum + raw(b.rank), 0) + vendorScaled;
  // A zero is a claim that every alarm was looked at and none is in the band; it is only true when
  // something was placed (or nothing is active).
  const graded = known && (placedTotal > 0 || active === 0);
  const at = (rank) => (graded ? raw(rank) : null);
  const row = (b, value) => ({
    key: b.key,
    label: b.label,
    face: html`<${SeverityChip} band=${b} />`,
    value,
    tone: severityTone(b.key),
  });
  const rows = [
    ...SCALE.map((b) => row(b, at(b.rank))),
    ...(vendorScaled
      ? [{ key: "vendor", label: "vendor scale", value: vendorScaled, tone: "quiet",
           title: "A vendor's own numbering, placed on each alarm against that element's scale." }]
      : []),
    row(UNPLACED, unplaced),
  ];
  return html`<div class="severity-band">
    <h2 class=${cx("severity-head", !graded && "severity-ungraded")}>
      <span class="severity-now">${known ? count(raw(0)) : "—"}</span>${" "}
      <span class="severity-word">critical</span>${" "}
      <span class="severity-of">of ${known ? plural(active, "active alarm") : "an unread count"}</span>
      <${InfoTip} label="Where a severity comes from">
        An operator's declaration wins, then an imported file, then the word the trap carried,
        then what the appliance learned. Alarms none of those can grade are counted as unplaced,
        never guessed.
      <//>
    </h2>
    <${Bars} title="Active alarms by severity" unit="alarms" rows=${rows}
      source=${known ? `${count(placedTotal)} placed` : "no severity census was reported"}
      note=${known ? provenanceNote(census) : null} />
  </div>`;
}
