/* The context panel: one panel, addressable by any section (Part III, closing draft §12.3).
 *
 * One rather than per-section because §2 requires only addressability, and one panel is the
 * smaller thing. A section publishes into it with `setContext({ title, render })` and clears it
 * with `clearContext()`; nothing else may write to it, so two sections cannot fight over it.
 *
 * It collapses on a narrow viewport (the stylesheet decides at what width) and can be collapsed
 * deliberately, because during a storm the work area is what the operator wants and a detail
 * panel taking a third of the screen is in the way.
 *
 * Phase 2's troubleshooting transcript attaches here — that is the obligation draft §2.3 records
 * — and nothing in this file assumes the situation list owns it.
 */

import { html, Component } from "./dom.js";

let entry = null;
const listeners = new Set();

/** Publish content into the panel. `render()` is called on every shell render. */
export function setContext(next) {
  entry = next;
  for (const fn of [...listeners]) fn(entry);
}

export function clearContext() { setContext(null); }

export function context() { return entry; }

export function subscribeContext(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export class ContextPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { entry: context(), collapsed: false };
  }

  componentDidMount() {
    this.unsubscribe = subscribeContext((next) => this.setState({ entry: next }));
  }

  componentWillUnmount() { if (this.unsubscribe) this.unsubscribe(); }

  render(_props, { entry: current, collapsed }) {
    if (!current) {
      return html`<aside id="context" class="context context-idle" aria-label="Details">
        <p class="hint">Select something to see its detail here.</p>
      </aside>`;
    }
    return html`<aside id="context" class=${collapsed ? "context collapsed" : "context"}
                       aria-label="Details">
      <div class="context-head">
        <h2>${current.title}</h2>
        <button type="button" class="icon"
                aria-expanded=${collapsed ? "false" : "true"}
                aria-label=${collapsed ? "Expand the detail panel" : "Collapse the detail panel"}
                onClick=${() => this.setState({ collapsed: !collapsed })}>${collapsed ? "«" : "»"}</button>
      </div>
      ${collapsed ? null : html`<div class="context-body">${current.render()}</div>`}
    </aside>`;
  }
}
