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

/* An alarm the appliance only knows the **existence** of: raised inside a window, never cleared.
 *
 * It carries no severity and no varbinds, because the ledger holds none — storing them would have
 * been collecting the trap the operator said not to collect. The title says so, because an
 * operator looking at an unplaced alarm is entitled to know whether the appliance failed to place
 * it or never saw it.
 */
export function SurfacedMark({ alarm }) {
  if (!alarm || !alarm.surfaced_from_window_id) return null;
  return html`<span
    class="mw-surfaced"
    data-role="surfaced-mark"
    data-window=${alarm.surfaced_from_window_id}
    title=${"This was raised while a maintenance window was suppressing this element, and never " +
    "cleared before the window ended. The appliance recorded that it happened and nothing else — " +
    "no severity, because the trap itself was not collected."}
  >
    <${Badge} tone="bad">Raised during maintenance, still active<//>
  </span>`;
}
