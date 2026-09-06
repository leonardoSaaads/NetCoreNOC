/* The member table of a situation card: the marks, and the zombie clear.
 *
 * Split out of `situations.js` in v0.16.0. The seam is a real one rather than a size accident:
 * this is the one place an operator says something about an **individual alarm** — tick it as not
 * belonging, or hand-clear it — while the file it left owns the **situation**: its state, its name,
 * its history and the four gestures that restructure it.
 *
 * The controls here are deliberately different in kind, and the card should read that way:
 *
 *   * the **mark** is an assertion about the *grouping* — it feeds a split, a move or an
 *     operator-split, and everything it produces is evidence about correlation;
 *   * the **clear** is an assertion about the *alarm* — it says the device never sent the clear,
 *     and it carries no confidence control because it teaches the correlator nothing
 *     (`PREREGISTRATION-0.16.0.md` §1);
 *   * the three **declarations** (v0.16.3) are assertions about *what a thing is* — this element's
 *     name, this trap kind's name, this trap kind's severity. They also carry no confidence
 *     control, and for the same reason: nothing learns from them, and
 *     `PREREGISTRATION-0.16.0.md` §2's map is unamended (DECISIONS #286).
 *
 * A card that offered them identically would be inviting the exact confusion the plan turns into a
 * prohibition. They are `declare.js`, which is where the three rules that govern all three live.
 *
 * **Why they are on this row at all.** Three complaints arrived from three screens — the host
 * rename that changed nothing, the Alarm Classes screen that changed nothing, the missing
 * severity — and they are one gap: nowhere for an operator to write down what they already know.
 * The row where the trap appears is where the operator already is, and everything they declare
 * propagates because Entities, the graph and Alarm Classes all read the same `label` table.
 */

import { html } from "../../dom.js";
import { SeverityCell, DataTable, cell } from "../../widgets.js";
import { alarmName, classVendor, deviceName, plural } from "../../format.js";
import { DeclareNe, DeclareClass, DeclareSeverity } from "./declare.js";

export function Members({ alarms, editable, marked, onMark, onClear, onDeclared }) {
  const columns = [
    ...(editable ? [{ key: "mark", label: "", title: "tick the members that do NOT belong" }] : []),
    { key: "device", label: "device" },
    ...(editable ? [{ key: "nameNe", label: "",
                     title: "name this element — it appears everywhere the element does" }] : []),
    { key: "class", label: "class" },
    ...(editable ? [{ key: "nameClass", label: "",
                     title: "name this kind of trap — it appears on every alarm of this class" }]
                 : []),
    { key: "instance", label: "instance" },
    { key: "severity", label: "severity" },
    ...(editable ? [{ key: "declareSev", label: "",
                     title: "declare how serious this kind of trap is" }] : []),
    { key: "count", label: "count", numeric: true },
    { key: "state", label: "state" },
    ...(editable ? [{ key: "clear", label: "", title: "hand-clear an alarm that never cleared" }]
                 : []),
  ];
  const rows = alarms.map((a, index) => ({
    key: a.id,
    cells: {
      // The accessible name is POSITIONAL, not the device and class names.
      //
      // Embedding them read better until the escaping invariant measured it: an operator label is
      // attacker-influenced text, and putting it in an attribute means a screen reader announces
      // whatever arrived in a trap. `setAttribute` makes it inert as markup, so this is not an
      // XSS — it is the same string reaching a second sink that the F1 discipline never covered,
      // and "inert" is not the same as "appropriate to read aloud". Position is also simply
      // clearer than a 200-character name.
      mark: html`<td><input type="checkbox" checked=${marked.has(a.id)}
        aria-label=${`Mark member ${index + 1} of ${alarms.length} as not belonging`}
        onChange=${(e) => onMark(a.id, e.target.checked)} /></td>`,
      device: html`<span>${deviceName(a)}${a.device_label
        ? html` <span class="muted">(declared)</span>` : null}</span>`,
      nameNe: html`<${DeclareNe} alarm=${a} onDone=${onDeclared} />`,
      // The vendor sits BESIDE the name, never in it (DECISIONS #282): 46 of 48 classes on a real
      // corpus have a vendor the appliance resolved and no name at all, so this row read as a bare
      // `1.3.6.1.4.1.2011.5.104.1` for 96 % of the classes an operator meets.
      class: html`<span>${classVendor(a)
        ? html`<span class="muted">${classVendor(a)} · </span>` : null}${alarmName(a)}${a.is_flapping
        ? html`<span class="flap" title="This alarm is flapping"> ~flapping</span>` : null}${
        a.class_label ? html` <span class="muted">(declared)</span>` : null}</span>`,
      nameClass: html`<${DeclareClass} alarm=${a} onDone=${onDeclared} />`,
      instance: a.instance || "—",
      severity: cell(html`<${SeverityCell} alarm=${a} />`),
      declareSev: html`<${DeclareSeverity} alarm=${a} onDone=${onDeclared} />`,
      count: a.count,
      state: a.status,
      // The zombie clear. **No confidence control**, and that is the release's central distinction
      // on screen: this says the ALARM was stale and asserts nothing whatever about the grouping,
      // so there is no number for an operator to give and nothing for the correlator to learn.
      clear: html`<td>${a.status === "active" ? html`<button type="button" class="linklike"
        title="This alarm never cleared. Clearing it by hand says nothing about the grouping."
        onClick=${() => onClear(a.id)}>clear</button>` : null}</td>`,
    },
  }));
  // The mark and clear columns are <td>s already; hand them through verbatim. The three
  // declaration cells are ordinary content and `DataTable` wraps them like any other.
  for (const row of rows) {
    if (editable) row.cells.mark = cell(row.cells.mark);
    if (editable) row.cells.clear = cell(row.cells.clear);
  }
  return html`<${DataTable} columns=${columns} rows=${rows}
    caption=${`${plural(alarms.length, "member alarm")} in this situation`} />`;
}
