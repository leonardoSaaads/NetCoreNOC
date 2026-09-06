/* The member table of a situation card: the marks, the actions, and the zombie clear.
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
 *
 * ## v0.16.4: eleven columns became eight, and one of them is an actions cell (DECISIONS #293)
 *
 * **Measured, before**: an editor's row was **eleven** columns — `mark, device, nameNe, class,
 * nameClass, instance, severity, declareSev, count, state, clear` — rendering **942 px** wide in a
 * **340 px** box at 390 px, so **602 px** of it were behind a horizontal scroll, and it was still
 * **172 px** over at 820. A viewer's six-column row overflowed by 270 px at 390 and fitted at 820,
 * so the whole of the difference was the five editor-only columns. *(v0.16.3's own note says nine.
 * Nine was true before that release added the third declaration column.)*
 *
 * The three declarations and the clear now share **one trailing actions cell**. What did not
 * change is that all four are still there, at every width, above the touch floor — directive 2,
 * and it does not bend. A tidier table that hides a gesture has traded the product for the layout.
 *
 * **The mark column keeps its own place**, first and narrow, because it is not an action taken on
 * a row — it is a *selection* that three gestures elsewhere on the card read, and folding it in
 * beside three buttons would make it look like a fourth. Its header now carries the select-all,
 * which is the whole of "clear every row at once" that costs no route: measured, one corpus
 * situation holds **1 051** members, and marking or unmarking them one at a time is not a gesture
 * anybody completes. A bulk *hand-clear* is a different thing and is not here — it is a write path
 * with an audit row and a lifecycle consequence per alarm, and it is a ROADMAP line with that same
 * measurement beside it.
 */

import { html } from "../../dom.js";
import { SeverityCell, DataTable, cell } from "../../widgets.js";
import { alarmName, classVendor, deviceName, plural } from "../../format.js";
import { DeclareNe, DeclareClass, DeclareSeverity } from "./declare.js";

export function Members({ alarms, editable, marked, onMark, onMarkAll, onClear, onDeclared }) {
  const markable = alarms.map((a) => a.id);
  const allMarked = markable.length > 0 && markable.every((id) => marked.has(id));
  const columns = [
    ...(editable
      ? [{
          key: "mark",
          // The header is the select-all, so the column's label IS its control. `label` takes a
          // node because `DataTable` renders whatever it is given, and an empty header over the
          // one column an operator interacts with was a wasted 28 px.
          label: html`<input type="checkbox" checked=${allMarked}
            aria-label=${allMarked
              ? `Unmark all ${alarms.length} members`
              : `Mark all ${alarms.length} members as not belonging`}
            onChange=${(e) => onMarkAll(e.target.checked)} />`,
          title: "tick the members that do NOT belong; this header ticks or clears every row",
        }]
      : []),
    { key: "device", label: "device" },
    { key: "class", label: "class" },
    { key: "instance", label: "instance" },
    { key: "severity", label: "severity" },
    { key: "count", label: "count", numeric: true },
    { key: "state", label: "state" },
    ...(editable
      ? [{ key: "actions", label: "actions",
           title: "name this element, name this kind of trap, declare its severity, or hand-clear "
                + "an alarm that never cleared" }]
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
      // The vendor sits BESIDE the name, never in it (DECISIONS #282): 46 of 48 classes on a real
      // corpus have a vendor the appliance resolved and no name at all, so this row read as a bare
      // `1.3.6.1.4.1.2011.5.104.1` for 96 % of the classes an operator meets.
      class: html`<span>${classVendor(a)
        ? html`<span class="muted">${classVendor(a)} · </span>` : null}${alarmName(a)}${a.is_flapping
        ? html`<span class="flap" title="This alarm is flapping"> ~flapping</span>` : null}${
        a.class_label ? html` <span class="muted">(declared)</span>` : null}</span>`,
      instance: a.instance || "—",
      severity: cell(html`<${SeverityCell} alarm=${a} />`),
      count: a.count,
      state: a.status,
      // **One cell, four controls, and each still says which kind of thing it asserts.** The three
      // declarations state what a thing IS; the clear states that an ALARM was stale and asserts
      // nothing whatever about the grouping — which is why it has no confidence control and why it
      // is separated here by a rule rather than sitting flush against the other three.
      actions: html`<td class="row-actions">
        <${DeclareNe} alarm=${a} onDone=${onDeclared} />
        <${DeclareClass} alarm=${a} onDone=${onDeclared} />
        <${DeclareSeverity} alarm=${a} onDone=${onDeclared} />
        ${a.status === "active" ? html`<button type="button" class="tap row-clear"
          title="This alarm never cleared. Clearing it by hand says nothing about the grouping."
          onClick=${() => onClear(a.id)}>clear</button>` : null}
      </td>`,
    },
  }));
  // The mark and actions cells are `<td>`s already; hand them through verbatim.
  for (const row of rows) {
    if (editable) row.cells.mark = cell(row.cells.mark);
    if (editable) row.cells.actions = cell(row.cells.actions);
  }
  // `kind="members"` is what lets the stylesheet freeze the DEVICE column beside the mark below
  // 720 px. #237 chose horizontal scroll with the first column frozen so *"the row keeps its
  // identity beside whatever the operator scrolled to"*, and for an editor the first column is a
  // checkbox: measured at 390 px, scrolled fully right, a viewer's frozen cell read
  // `127.0.0.0/24` and an editor's read nothing at all (F109).
  return html`<${DataTable} kind=${editable ? "members" : null} columns=${columns} rows=${rows}
    caption=${`${plural(alarms.length, "member alarm")} in this situation`} />`;
}
