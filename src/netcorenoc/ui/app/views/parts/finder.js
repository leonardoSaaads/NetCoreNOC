/* The controls that NARROW the list: the counts, the search box and the four tabs.
 *
 * **Split out of `views/situations.js` in v0.16.4**, on a seam that release created and then had
 * to honour. That module's own header says it owns *"finding a situation — the list, the three
 * tabs and the search"*, and this release added a fourth narrowing control (the count cards, which
 * are filters rather than figures) and gave all of them a bordered block to sit in. At 19 006
 * bytes the file went past the module-graph guard's ceiling — a third of the 52 738-byte file
 * v0.12.0 replaced — and the honest repair to a file over budget is to notice what it had become
 * two of. The screen keeps the **list**; this keeps everything that decides which rows are in it.
 *
 * ## Why the tiles are here rather than on the Overview (item 3)
 *
 * They were five tiles on the Overview — active alarms, open situations, devices, alarm classes,
 * p95 latency — where an operator reads a number and then navigates somewhere else to act on it.
 * Three arrive here instead, and the difference is not where they sit: **each one is a filter**.
 * Pressing *New* selects the New tab, which is the gesture the number was making an operator want.
 * A tile that only displays is a tile that costs a screen row for nothing.
 *
 * ## Why the counts come from the server and not from the list beside them
 *
 * The live list this screen renders is capped at **50 rows**, so counting the statuses in it would
 * report a floor and call it a count — the invented number the health control refuses one file
 * over. `new_situations` and `working_situations` are two more `COUNT(*)` on a route that already
 * ran five, scoped by the same rule the listing uses.
 *
 * ## The block, and what the maintainer reported about it (item 5)
 *
 * *"Too small, crowded, and touching the table."* The third was structural: the tab row sat 12 px
 * above the first card with nothing between them, so it read as part of that situation rather than
 * as a filter over all of them. The field now takes the row, the tabs are one segmented control
 * with a shared border, and the whole thing is a bordered header with space under it.
 */

import { html, cx } from "../../dom.js";
import { count, plural } from "../../format.js";

/* The three states the schema now has, and the fourth entry that is not a state.
 *
 * `new` leads because it is what the correlator creates and what an untriaged appliance is full of
 * (DECISIONS #254). The titles say what each state MEANS rather than repeating its name: "open" and
 * "new" are not self-explanatory to somebody who has just been handed the console. */
export const SEARCH_NOTE =
  "These are search results, not the live list: they were matched by the server when you typed " +
  "and they do not update on their own. The tab above narrows them. Clear the box to go back to " +
  "the live list.";

export const TABS = [
  ["new", "New", "Formed by the correlator, and nobody has looked at it yet"],
  ["open", "Open", "An operator has touched it: judged, moved, merged, split or named it"],
  ["resolved", "Resolved", "It has left — and the card says why"],
  ["", "Any", "Every situation this appliance currently holds"],
];


/**
 * The three counts an operator working situations needs, **on the screen where they act on them**
 * (v0.16.4, item 3).
 *
 * They were five tiles on the Overview — active alarms, open situations, devices, alarm classes,
 * p95 latency — where an operator reads them and then navigates somewhere else to do anything
 * about them. Three arrive here instead, and the difference is not where they sit: **each one is a
 * filter**. Pressing *New* selects the New tab, which is the gesture the number was making an
 * operator want. A tile that only displays is a tile that costs a screen row for nothing.
 *
 * **Every figure is counted server-side over the whole estate and scoped by the same rule the
 * listing uses.** The live list this screen renders from is capped at 50 rows, so counting the
 * statuses in it would report a floor and call it a count — which is the invented number the
 * health control refuses one file over. `new_situations` and `working_situations` are two more
 * `COUNT(*)` on a route that already ran five.
 *
 * `devices`, `classes` and the p95 latency do not follow them here. They are facts about the
 * appliance rather than about the queue, they are still on the Overview, and the health control in
 * the top bar holds the latency now — on every screen rather than on one.
 */
export function Counts({ stats, status, onPick }) {
  if (!stats) return null;
  const tiles = [
    ["active alarms", stats.active_alarms, null, "alarm",
     "Alarms the appliance believes are still on. Not situations — one situation can hold many."],
    ["new", stats.new_situations, "new", "warn",
     "Formed by the correlator and nobody has looked at them yet. This is the queue."],
    ["open", stats.working_situations, "open", null,
     "An operator has touched them: judged, moved, merged, split or named."],
  ];
  return html`<div class="stat-row count-cards">
    ${tiles.map(([label, value, tab, tone, why]) => {
      const selected = tab != null && tab === status;
      // A tile that filters is a BUTTON, and one that does not is a div. The difference is not
      // decoration: a control an operator can press must be reachable by keyboard and must say
      // what pressing it does, and a `<div onClick>` is neither.
      if (tab == null) {
        return html`<div key=${label} class=${cx("stat", tone && `stat-${tone}`)} title=${why}>
          <div class="stat-value">${count(value ?? 0)}</div>
          <div class="stat-label">${label}</div>
        </div>`;
      }
      return html`<button key=${label} type="button"
          class=${cx("stat", "stat-filter", tone && `stat-${tone}`, selected && "stat-on")}
          aria-pressed=${selected ? "true" : "false"}
          title=${`${why} Press to show only these.`}
          onClick=${() => onPick(tab)}>
        <div class="stat-value">${count(value ?? 0)}</div>
        <div class="stat-label">${label}</div>
      </button>`;
    })}
  </div>`;
}
