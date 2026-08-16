/* The sidebar: grouped navigation, keyboard-operable, rendered from the same table the router
 * authorises from.
 *
 * ## Groups are data (draft §2.1)
 *
 * `GROUPS` below is a list. Adding v0.14.0's *Diagnose* section is adding an entry to it and
 * tagging the views that belong; it is not editing a layout. That is the whole of what §2 asks
 * v0.13.0 to owe the future, and it costs one array.
 *
 * **No Phase 2 or Phase 3 item appears here.** Not disabled, not greyed, not "coming soon".
 * Absent. A greyed-out *Troubleshooting* promising a feature nobody has built teaches an operator
 * that parts of the product do not work, which is the same error as v0.9.1's unused `close`
 * channel with worse ergonomics (draft §2).
 *
 * ## Ordering is stable and never keyed on state
 *
 * A NOC operator builds muscle memory. Items are in registry order, always, whatever is alarming
 * — a list that re-sorts by severity moves the target under a hand that already knows where it
 * is. Counts appear as badges on a fixed position instead.
 *
 * ## Keyboard (§12.6's floor)
 *
 * The nav is one tab stop. Inside it, Up/Down move between items, Home/End jump to the ends, and
 * Enter or Space activates — the standard listbox-ish pattern operators already know from every
 * other tool. Roving `tabindex` rather than fourteen tab stops, because tabbing through fourteen
 * links to reach the content is exactly the accessibility failure that makes people stop using
 * the keyboard.
 */

import { html, Component, cx } from "./dom.js";
import { reachableViews } from "./router.js";

/** Group id -> heading. Order here is order on screen. `null` group renders above the first. */
export const GROUPS = [
  { id: "operations", label: "Operations" },
  { id: "evidence", label: "Evidence" },
  { id: "administer", label: "Administer" },
];

export class Sidebar extends Component {
  constructor(props) {
    super(props);
    // Seeded from the route so a deep link puts the tab stop on the item the operator is looking
    // at, rather than at the top of a list they did not ask for.
    const initial = reachableViews(props.capabilities).findIndex((v) => v.id === props.activeId);
    this.state = { focusIndex: initial === -1 ? 0 : initial };
    this.items = [];
    this.nodes = [];
    this.onKeyDown = this.onKeyDown.bind(this);
  }

  activate(view) {
    this.props.onNavigate(`#/${view.id}`);
  }

  onKeyDown(event) {
    const last = this.items.length - 1;
    if (last < 0) return;
    const current = this.state.focusIndex;
    let next = null;
    if (event.key === "ArrowDown") next = Math.min(last, current + 1);
    else if (event.key === "ArrowUp") next = Math.max(0, current - 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      this.activate(this.items[current]);
      return;
    }
    if (next === null) return;
    event.preventDefault();
    this.setState({ focusIndex: next });
    const node = this.nodes[next];
    if (node && node.focus) node.focus();
  }

  /* Re-seat the roving tab stop on the active item when the ROUTE changes, and never otherwise.
   *
   * The first version computed `focusIndex` from `activeId` on every render, which read correctly
   * and made the arrow keys inert: `ArrowDown` set `state.focusIndex`, the next render discarded
   * it, and the tab stop stayed where the route put it. Driving the sidebar in the harness is what
   * found it — the keyboard trace reported `at: 0` after every keypress — and it is exactly the
   * class of defect Part I.1 exists to catch, because the markup was correct throughout and no
   * assertion about rendered structure would have noticed.
   */
  componentDidUpdate(previous) {
    if (previous.activeId === this.props.activeId) return;
    const index = reachableViews(this.props.capabilities)
      .findIndex((view) => view.id === this.props.activeId);
    if (index !== -1 && index !== this.state.focusIndex) this.setState({ focusIndex: index });
  }

  render({ capabilities, activeId, counts = {} }) {
    const views = reachableViews(capabilities);
    this.items = views;
    const focusIndex = Math.min(Math.max(0, this.state.focusIndex), Math.max(0, views.length - 1));
    this.nodes = [];

    const ungrouped = views.filter((v) => !v.group);
    const rendered = [];
    if (ungrouped.length) rendered.push(this.renderGroup(null, ungrouped, activeId, focusIndex, counts));
    for (const group of GROUPS) {
      const inGroup = views.filter((v) => v.group === group.id);
      // A group with nothing in it is not rendered at all. A viewer sees no `Administer`
      // heading standing over an empty space — absence is absence.
      if (inGroup.length) rendered.push(this.renderGroup(group, inGroup, activeId, focusIndex, counts));
    }

    return html`<nav id="nav" class="sidebar" aria-label="Sections" onKeyDown=${this.onKeyDown}>
      <div class="brand"><span class="brand-mark" aria-hidden="true">◈</span>
        <span class="brand-name">Net<b>CoreNOC</b></span></div>
      ${rendered}
    </nav>`;
  }

  renderGroup(group, views, activeId, focusIndex, counts) {
    const headingId = group ? `nav-group-${group.id}` : "nav-group-top";
    return html`<div class="nav-group" key=${headingId}>
      ${group ? html`<h2 class="nav-heading" id=${headingId}>${group.label}</h2>` : null}
      <ul class="nav-list" aria-labelledby=${group ? headingId : null}>
        ${views.map((view) => {
          const index = this.items.indexOf(view);
          const active = view.id === activeId;
          const badge = counts[view.id];
          return html`<li key=${view.id}>
            <a
              class=${cx("nav-item", active && "active")}
              href=${`#/${view.id}`}
              aria-current=${active ? "page" : null}
              tabindex=${index === focusIndex ? "0" : "-1"}
              ref=${(node) => { this.nodes[index] = node; }}
              onClick=${() => this.setState({ focusIndex: index })}
            >
              <span class="nav-glyph" aria-hidden="true">${view.glyph}</span>
              <span class="nav-label">${view.label}</span>
              ${badge != null && badge > 0
                ? html`<span class="nav-count" title=${`${badge} ${view.countNoun ?? "items"}`}>${badge}</span>`
                : null}
            </a>
          </li>`;
        })}
      </ul>
    </div>`;
  }
}
