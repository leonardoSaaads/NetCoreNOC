/* The two disclosures in the top bar: what is wrong, and whether the appliance is keeping up.
 *
 * ## Why they exist, and what left to make room for them (DECISIONS #288, #289)
 *
 * The top bar carried four counters — devices, classes, active alarms, open situations — and at
 * 390 px they wrapped it onto **four rows, 126 px tall**, above 187 px of banners, so **360 px of
 * an 844 px phone** were spent before the work area began. Every one of those numbers is on the
 * Overview and two are on the Situations screen; none of them is something an operator acts on
 * from a chrome strip. They are gone.
 *
 * What replaces them is two controls that hold what an operator *does* act on:
 *
 *   * **the bell** — the operator warnings, which already existed and already interrupted, in a
 *     banner strip that an operator could only read and never return to. Each warning that names a
 *     parameter this console knows about links to Settings; each that does not renders as text
 *     with no affordance, because a control that navigates somewhere unhelpful teaches an operator
 *     that the bell's links are noise;
 *   * **the health control** — CPU, memory and storage, each with a two-hour sparkline. It lives
 *     in `health.js` from v0.16.5, because this file reached the module graph's 17 579-byte
 *     ceiling; the disclosure mechanism it uses is still here and is exported to it.
 *
 * ## Both panels are anchored to the bar, and F111 is why that sentence is repeated
 *
 * v0.16.4 wrote `.topbar { position: relative }` to anchor them and left `.disclosure` positioned
 * above it, so the panels resolved `100%` against a 28 px button and rendered **26 px wide at every
 * width**. Both are in the right-hand group now, beside the account controls, and both open from
 * the bar's right edge.
 *
 * ## The ingest gap stays a banner
 *
 * *"Traps are being dropped right now"* is the single most urgent thing this appliance can say and
 * it is not something an operator should have to open a panel to learn. The bell **also** holds it,
 * so the panel is a complete list rather than a partial one, and the banner above the work area is
 * unchanged in meaning from v0.12.0.
 *
 * ## Both are disclosures, and both close the three ways a disclosure must
 *
 * Escape, a second press of the opener, and a click outside. `aria-expanded` on the opener and
 * `role="dialog"` with a label on the panel, because a collapsed icon-only control is its
 * accessible name and nothing else.
 */

import { html, Component, cx } from "./dom.js";
import { Icon } from "./icons.js";
import { count, plural, score } from "./format.js";
import { warningTarget } from "./parameters.js";

/**
 * One word for the whole appliance, **derived from the numbers beside it and never from a fourth**.
 *
 * Three states and each is a different action: `dropping` means traps are being lost now and the
 * queue is the reason; `behind` means correlation has not caught up but nothing is lost yet;
 * `keeping up` means neither. A fourth state for "the host is loaded" would need a measurement
 * this appliance does not take, which is exactly the number decision 2 refuses to invent.
 */
export function healthState(stats) {
  const gaps = (stats?.open_ingest_gaps ?? []).length;
  const dropped = stats?.receiver?.dropped ?? 0;
  if (gaps > 0 || dropped > 0) return { key: "dropping", word: "dropping traps", tone: "alarm" };
  if ((stats?.queue_depth ?? 0) > 0) return { key: "behind", word: "behind", tone: "warn" };
  return { key: "ok", word: "keeping up", tone: "quiet" };
}

/** Everything the bell holds, oldest concern first: the gaps, then the warnings. */
export function notices(stats) {
  const gaps = stats?.open_ingest_gaps ?? [];
  const out = [];
  if (gaps.length) {
    const lost = gaps.reduce((n, g) => n + (g.dropped || 0), 0);
    out.push({
      urgent: true,
      text:
        `Ingest gap — dropping traps now. ${plural(lost, "event")} lost ` +
        `(${[...new Set(gaps.map((g) => g.reason))].join(", ")}).`,
      // No link. A gap is a condition, not a misconfiguration, and there is no setting that ends
      // one — the operator's next step is the health control beside this, not a form.
      href: null,
    });
  }
  for (const text of stats?.warnings ?? []) {
    out.push({ urgent: false, text, href: warningTarget(text) });
  }
  return out;
}

/** A disclosure that closes on Escape, on a second press, and on a click outside it.
 *
 * Exported because `health.js` is the same mechanism with different contents — the split in
 * v0.16.5 was forced by the module-graph ceiling, and duplicating the class to avoid an import
 * would have been the guard making the code worse rather than better. */
export class Disclosure extends Component {
  constructor(props) {
    super(props);
    this.state = { open: false };
    this.root = null;
    this.onDocument = this.onDocument.bind(this);
    this.onKey = this.onKey.bind(this);
  }

  componentDidMount() {
    globalThis.document.addEventListener("click", this.onDocument, true);
    globalThis.document.addEventListener("keydown", this.onKey);
  }

  componentWillUnmount() {
    globalThis.document.removeEventListener("click", this.onDocument, true);
    globalThis.document.removeEventListener("keydown", this.onKey);
    this.cancelHover();
  }

  cancelHover() {
    if (this.hoverTimer) globalThis.clearTimeout(this.hoverTimer);
    this.hoverTimer = null;
  }

  /**
   * Hover opens it, on a mouse only — `hover` is the opt-in and the two timers are not optional.
   *
   * `matchMedia("(hover: hover) and (pointer: fine)")` because on a touch screen the browser
   * synthesises a hover from the tap that is also the click: without the query the panel opens on
   * hover and the click that caused it immediately closes it again, which reads as a control that
   * does nothing. Touch keeps the click, which is the gesture that exists there.
   *
   * The delays: opening waits 120 ms so a pointer crossing the bar on its way somewhere else does
   * not fling panels open; closing waits 260 ms because the panel is anchored to the bar's right
   * edge and reaching it means leaving the 28 px button, and a panel that vanishes while you move
   * toward it cannot be read. A panel opened by a **click** ignores both — `pinned` — because a
   * deliberate press should not be undone by the pointer wandering off.
   */
  hover(open) {
    const fine = globalThis.matchMedia?.("(hover: hover) and (pointer: fine)")?.matches;
    if (!fine || !this.props.hover) return;
    if (this.pinned && !open) return;
    this.cancelHover();
    this.hoverTimer = globalThis.setTimeout(
      () => this.setState({ open }),
      open ? 120 : 260,
    );
  }

  /* Close on a click outside — and on a **link inside**, which is not the same rule.
   *
   * Found in the live pass: following a warning's link navigated to Settings and left the panel
   * hanging over it, because the click was inside `this.root` and the early return kept it open.
   * A disclosure that survives the navigation it caused is a disclosure an operator has to dismiss
   * twice. The opener itself is exempt — that click is the toggle, and closing here as well would
   * make it a no-op. */
  onDocument(event) {
    if (!this.state.open) return;
    const inside = this.root && this.root.contains(event.target);
    if (inside && !this.isNavigation(event.target)) return;
    this.close();
  }

  /** Every close goes through here, so none of them can leave `pinned` set behind it.
   *
   * A panel dismissed with Escape or the × that stayed pinned would be a panel hover could open
   * and never close again — the bug you get for free by writing `setState({open: false})` in four
   * places and remembering the flag in three. */
  close() {
    this.cancelHover();
    this.pinned = false;
    this.setState({ open: false });
  }

  /** Did this click land on something that takes the operator elsewhere?
   *
   * `toLowerCase()` rather than a comparison against `"A"`: an HTML document reports `tagName`
   * upper-case and an XHTML one reports it as authored, and the DOM harness is the second kind.
   * Written the case-sensitive way this closed correctly in Chromium and not at all under the
   * harness — a difference that would have shipped as *"no test could see it"* rather than as a
   * bug, which is the failure mode this project has met eight times. */
  isNavigation(target) {
    for (let node = target; node && node !== this.root; node = node.parentNode) {
      if (String(node.tagName).toLowerCase() === "a" && node.getAttribute?.("href")) return true;
    }
    return false;
  }

  onKey(event) {
    if (event.key === "Escape" && this.state.open) this.close();
  }

  render({ id, icon, label, title, badge, tone, children }, { open }) {
    return html`<div class="disclosure" ref=${(node) => { this.root = node; }}
         onMouseEnter=${() => this.hover(true)} onMouseLeave=${() => this.hover(false)}>
      <button type="button" class=${cx("icon", "disclosure-open", tone && `disclosure-${tone}`)}
              aria-expanded=${open ? "true" : "false"} aria-controls=${id}
              aria-label=${label} title=${label}
              onClick=${() => {
                this.cancelHover();
                this.pinned = !open;
                this.setState({ open: !open });
              }}>
        <${Icon} name=${icon} />
        ${badge != null ? html`<span class="disclosure-badge">${badge}</span>` : null}
      </button>
      <div id=${id} class="disclosure-panel" role="dialog" aria-label=${label} hidden=${!open}>
        ${open
          ? html`<div class="disclosure-head">
                <h2 class="disclosure-title">${title}</h2>
                ${/* The dismiss the operator asked for. Escape, a second press of the opener and a
                      click outside all close this panel and always did — but none of the three is
                      VISIBLE, and a control an operator cannot see is a control they do not have.
                      `aria-label` rather than a bare glyph: the accessible name of a control whose
                      text content is "×" is the multiplication sign. */
                  null}
                <button type="button" class="disclosure-close" aria-label="Close"
                        title="Close (Esc)"
                        onClick=${() => this.close()}>×</button>
              </div>
              <div class="disclosure-body">${children}</div>`
          : null}
      </div>
    </div>`;
  }
}

/** The warnings, with a link to the setting that resolves each one that has one. */
export function Bell({ stats }) {
  const items = notices(stats);
  const urgent = items.some((item) => item.urgent);
  const label = items.length
    ? `${plural(items.length, "warning")} — open the list`
    : "No warnings. Open the list.";
  return html`<${Disclosure} id="noticePanel" icon="bell" label=${label}
      title="What needs attention"
      tone=${urgent ? "alarm" : items.length ? "warn" : null}
      badge=${items.length || null}>
    ${items.length === 0
      ? html`<p class="hint">Nothing. The appliance has raised no warnings and no ingest gap is
          open.</p>`
      : html`<ul class="notice-list">${items.map((item, index) => html`
          <li key=${index} class=${cx(item.urgent && "notice-urgent")}>
            <${Icon} name=${item.urgent ? "warn" : "info"} />
            <div class="notice-body">
              <p class="notice-text">${item.text}</p>
              ${item.href
                ? html`<a class="tap notice-link" href=${item.href}>Open the setting →</a>`
                : null}
            </div>
          </li>`)}</ul>`}
  <//>`;
}

/**
 * Queue depth, p95 latency, the derived trap rate, and the two receiver counters that mean loss.
 *
 * Every figure here is one `/api/stats` already serves on every poll. The rate is derived in the
 * client between two samples and prints the window it covers, because a rate with no window is a
 * number nobody can act on (DECISIONS #222); until a second sample arrives it says so rather than
 * showing a zero.
 */
