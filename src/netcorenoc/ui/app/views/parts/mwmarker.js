/* **The marker every role sees** (IV.3, prime directive 4).
 *
 * A host that goes quiet with no marker reads as healthy, which is how maintenance windows hide
 * outages. So the marker's *existence* ignores `visibility` entirely: a viewer looking at a device
 * under an `editors`-only window sees that planned work is in force and when it ends, and does not
 * see its name, its owner or its rules.
 *
 * That split is decided by the server — `store/mw_reads.py::window_markers` is a different query
 * selecting different columns — and this module only draws what it is handed. A component that
 * decided what to hide would be F35 again: v0.7.0 made a display string an authorization key.
 *
 * ## Two different sentences, and they must not be confused
 *
 * *"Under maintenance"* is planned work in progress. *"Raised during maintenance, still active"*
 * is a fault that **outlived** a window — the thing II.2's ledger exists to surface, and the most
 * important alarm on the screen when it appears. `SurfacedMark` is the second one.
 */

import { html } from "../../dom.js";
import { Badge } from "../../widgets.js";
import { humanise } from "./mwdraft.js";

/* A device or a situation is under a window right now.
 *
 * `marker` is what the server gave: a window id, a status, and when it ends. Never a name.
 */
export function MaintenanceMark({ marker, now }) {
  if (!marker) return null;
  const left = (marker.ends_at_effective ?? marker.ends_at) - (now ?? Date.now() / 1000);
  return html`<span
    class="mw-mark"
    data-role="maintenance-mark"
    data-window=${marker.window_id}
    title=${"Planned work is in force on this element. Alarms from it may be suppressed. " +
    "Who scheduled it and what it still collects follow the window's visibility setting."}
  >
    <${Badge} tone="info">Under maintenance<//>
    ${left > 0 ? html`<span class="muted">${humanise(left)} left</span>` : null}
  </span>`;
}

/* **A fault that outlived a maintenance window** — the marker, small (v0.22.0, item 8, #388).
 *
 * The maintainer asked for it removed: *"a MW exists precisely to avoid alerts."* It is the one
 * thing that says a window closed with something still broken — the trap that reported it arrived
 * inside the window, and traps are sent once. Measured, the defect was its SCOPE: it was drawn on
 * every alarm the ledger ever surfaced, including ones that had since cleared and ones the element
 * had re-reported itself. The server now serves `outlived_window_id` only while the marker is true
 * — active, not seen since it was surfaced, not acknowledged — and this draws it as a badge, with
 * the sentence in its accessible name and title rather than in the row.
 *
 * `onAck` is present for a principal holding `alarm.acknowledge`: the operator has seen it.
 */
export function SurfacedMark({ alarm, onAck }) {
  if (!alarm || !alarm.outlived_window_id) return null;
  const said = `Raised during maintenance window ${alarm.outlived_window_id} and still active ` +
    "after it ended. The appliance saw the raise and nothing else — no severity, because the " +
    "trap was not collected.";
  return html`<span class="mw-outlived" data-role="surfaced-mark"
      data-window=${alarm.outlived_window_id} title=${said}>
    <span class="mw-outlived-badge" role="img" aria-label=${said}>after MW</span>
    ${onAck
      ? html`<button type="button" class="mw-outlived-ack" aria-label="Acknowledge: seen"
          title="Seen — stop marking it" onClick=${() => onAck(alarm.id)}>✓</button>`
      : null}
  </span>`;
}
