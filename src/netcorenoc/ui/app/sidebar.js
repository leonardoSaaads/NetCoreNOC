/* The sidebar: grouped navigation, keyboard-operable, rendered from the same table the router
 * authorises from.
 *
 * ## Groups are data (draft §2.1)
 *
 * `GROUPS` below is a list. Adding v0.14.0's *Diagnose* section is adding an entry to it and
 * tagging the views that belong; it is not editing a layout. That is the whole of what §2 asks
 * v0.13.0 to owe the future, and it costs one array.
 *
 * **No item for something unbuilt appears here.** Not disabled, not greyed, not "coming soon".
 * Absent. A greyed-out *Troubleshooting* promising a feature nobody has built teaches an operator
 * that parts of the product do not work, which is the same error as v0.9.1's unused `close`
 * channel with worse ergonomics (draft §2). Phase 2's NOC arrives the way that rule said it would:
 * one group, one screen that works, and nothing announced beyond it (ADR #454). Its agents are
 * listed one by one, as they are hired, never as places waiting for one (ADR #468).
 *
 * ## One plane at a time
 *
 * `plane` (from the top bar's switch, derived from the route) picks which half of the product the
 * sidebar offers. Each group names its plane, and a plane with nothing reachable has no switch.
 *
 * ## The wordmark is a link (v0.15.3)
 *
 * It was a `<div>`, so clicking the product's own name did nothing — the one place in a console
 * every operator tries first. It goes to the Overview, and the destination comes from
 * `registry.DEFAULT_VIEW` rather than a second `"#/overview"` written here.
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
import { Icon } from "./icons.js";
import { DEFAULT_VIEW } from "./registry.js";

/** Group id -> heading. Order here is order on screen. `null` group renders above the first.
 *
 * ADR #468: the NOC plane reads like Paperclip's company sidebar — the work (cases, their clocks,
 * the playbooks), the agents one by one, and the NOC itself (its organization, its vault). */
export const GROUPS = [
  { id: "operations", label: "Operations", plane: "broker" },
  { id: "evidence", label: "Evidence", plane: "broker" },
  { id: "administer", label: "Administer", plane: "broker" },
  { id: "work", label: "Work", plane: "noc" },
  { id: "agents", label: "Agents", plane: "noc" },
  { id: "noc", label: "NOC", plane: "noc" },
];

/* An entry is one link of the roving set: a view, or an **extra** a group carries after its views
 * (ADR #468: each of the NOC's agents, under *Agents*). Extras are data the shell hands in —
 * `{ key, href, label, icon, state, live, badge, noun }` — so the sidebar stays a renderer of
 * links and the one tab stop covers them as it covers the views. */
function entriesOf({ capabilities, plane = "broker", extras = {} }) {
  // ADR #470: `extras.hide` names views a NOC grant opens that the caller does not hold.
  const views = reachableViews(capabilities, plane).filter((v) => !(extras.hide || []).includes(v.id));
  const out = [];
  const push = (group, list) => {
    for (const view of list) {
      out.push({ key: view.id, href: `#/${view.id}`, label: view.navLabel || view.label,
        icon: view.icon, view, group });
    }
    for (const extra of (group && extras[group.id]) || []) out.push({ ...extra, group, sub: true });
  };
  push(null, views.filter((v) => !v.group));
  for (const group of GROUPS.filter((g) => g.plane === plane)) {
    const inGroup = views.filter((v) => v.group === group.id);
    // A group with nothing in it is not rendered at all, and its extras with it: a viewer sees no
    // `Administer` heading standing over an empty space — absence is absence.
    if (inGroup.length) push(group, inGroup);
  }
  return out;
}

/** Which entry is the page on screen: an extra naming the address exactly, else the view. */
function activeIndex(entries, activeId, activeHref) {
  const exact = entries.findIndex((e) => e.sub && e.href === activeHref);
  return exact !== -1 ? exact : entries.findIndex((e) => e.view && e.view.id === activeId);
}

export class Sidebar extends Component {
  constructor(props) {
    super(props);
    // Seeded from the route so a deep link puts the tab stop on the item the operator is looking
    // at, rather than at the top of a list they did not ask for.
    const initial = activeIndex(entriesOf(props), props.activeId, props.activeHref);
    this.state = { focusIndex: initial === -1 ? 0 : initial };
    this.items = [];
    this.nodes = [];
    this.onKeyDown = this.onKeyDown.bind(this);
  }

  activate(entry) {
    this.props.onNavigate(entry.href);
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
    const { activeId, activeHref, plane } = this.props;
    if (previous.activeId === activeId && previous.plane === plane
      && previous.activeHref === activeHref) return;
    const index = activeIndex(entriesOf(this.props), activeId, activeHref);
    if (index !== -1 && index !== this.state.focusIndex) this.setState({ focusIndex: index });
  }

  render(props) {
    const { activeId, activeHref, plane = "broker", counts = {}, collapsed = false } = props;
    const entries = entriesOf(props);
    this.items = entries;
    const focusIndex = Math.min(Math.max(0, this.state.focusIndex), Math.max(0, entries.length - 1));
    const active = activeIndex(entries, activeId, activeHref);
    this.nodes = [];

    const groups = [];
    for (const entry of entries) {
      const last = groups[groups.length - 1];
      if (last && last.group === entry.group) last.entries.push(entry);
      else groups.push({ group: entry.group, entries: [entry] });
    }
    const rendered = groups.map(({ group, entries: list }) =>
      this.renderGroup(group, list, active, focusIndex, counts, collapsed));

    // The brand is OUTSIDE the <nav>, and that placement is load-bearing rather than tidy.
    // Inside it, the wordmark link joined `#nav a` — the roving-tabindex set — and the DOM
    // harness measured the sidebar at two tab stops with the first ArrowDown landing on item 2.
    // The accessibility floor says the nav is one tab stop; a link to the Overview is not one of
    // the sections the arrow keys move between, so it belongs beside the navigation, not in it.
    return html`<div class=${cx("sidebar", collapsed && "sidebar-collapsed")}>
      <a class="brand" href=${`#/${DEFAULT_VIEW}`} title="Go to the Overview"
         aria-label="NetCoreNOC — go to the Overview">
        <span class="brand-mark" aria-hidden="true">◈</span>
        <span class="brand-name">Net<b>CoreNOC</b></span>
      </a>
      <nav id="nav" class=${cx("nav", `nav-plane-${plane}`)} aria-label="Sections"
           onKeyDown=${this.onKeyDown}>
        ${rendered}
      </nav>
    </div>`;
  }

  /* A collapsed item is **icon-only, and the accessible name is the whole of its usability.**
   *
   * The v0.13.0 floor says every icon in this console is decoration beside a text label, and a
   * collapsed sidebar is the one place that cannot hold: the label is not on screen. So the label
   * stays in the DOM as `.nav-label` — hidden visually, never `display: none`, so a screen reader
   * still reads it — and `aria-label` carries it plus the badge's meaning for the collapsed case,
   * because `sr-only` text inside a link is announced but the count beside it is a bare numeral
   * that would be read as part of the name.
   *
   * The `title` is the sighted half of the same fact, and it is the reason a collapsed sidebar is
   * usable with a mouse at all.
   */
  renderGroup(group, entries, active, focusIndex, counts, collapsed) {
    const headingId = group ? `nav-group-${group.id}` : "nav-group-top";
    return html`<div class="nav-group" key=${headingId}>
      ${group ? html`<h2 class="nav-heading" id=${headingId}>${group.label}</h2>` : null}
      <ul class="nav-list" aria-labelledby=${group ? headingId : null}>
        ${entries.map((entry) => {
          const index = this.items.indexOf(entry);
          const on = index === active;
          const badge = entry.view ? counts[entry.view.id] : entry.badge;
          const noun = (entry.view && entry.view.countNoun) || entry.noun || "items";
          const named = entry.state ? `${entry.label} — ${entry.state}` : entry.label;
          const said = badge != null && badge > 0 ? `${named} — ${badge} ${noun}` : named;
          return html`<li key=${entry.key}>
            <a
              class=${cx("nav-item", on && "active", entry.sub && "nav-sub")}
              href=${entry.href}
              aria-current=${on ? "page" : null}
              aria-label=${collapsed ? said : null}
              title=${collapsed ? said : entry.state ? named : null}
              tabindex=${index === focusIndex ? "0" : "-1"}
              ref=${(node) => { this.nodes[index] = node; }}
              onClick=${() => this.setState({ focusIndex: index })}
            >
              <span class=${cx("nav-glyph", entry.tone)}><${Icon} name=${entry.icon} /></span>
              ${/* **`.visually-hidden`, the console's own class, not a second copy of its rules.**
                    A collapsed label must be hidden from the eye and kept in the accessible tree,
                    and this console already has exactly one implementation of that. Writing the
                    clipping again in a `.sidebar-collapsed .nav-label` rule would be a second one
                    — and a second one is how `display: none` gets in, which removes the label from
                    the tree and is invisible to every test in this repository that has no layout
                    engine. As a class it is visible in the DOM, so a guard can see it. */ null}
              <span class=${cx("nav-label", collapsed && "visually-hidden")}>${entry.label}${entry.state
                ? html`<span class="visually-hidden">, ${entry.state}</span>` : null}</span>
              ${entry.dot ? html`<span class=${cx("nav-dot", `dot-${entry.dot}`)} aria-hidden="true"></span>` : null}
              ${entry.live && !collapsed ? html`<span class="nav-live" aria-hidden="true">live</span>` : null}
              ${badge != null && badge > 0
                ? html`<span class="nav-count" title=${`${badge} ${noun}`}>${badge}</span>`
                : null}
            </a>
          </li>`;
        })}
      </ul>
    </div>`;
  }
}
