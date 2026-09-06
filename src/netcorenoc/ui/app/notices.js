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
 *   * **the health control** — the four numbers `/api/stats` already measures and one word derived
 *     from them. **CPU, RAM and disk are not here and are not invented**: there is no `psutil`, no
 *     `resource` and no `/proc` read anywhere in `src/`, so the alternative to showing four true
 *     numbers is not "read what is there", it is "add a source". The ten-minute series the
 *     maintainer described needs storage nothing has, and it is v0.16.5's.
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

/** A disclosure that closes on Escape, on a second press, and on a click outside it. */
class Disclosure extends Component {
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
    if (event.key === "Escape" && this.state.open) this.setState({ open: false });
  }

  render({ id, icon, label, badge, tone, children }, { open }) {
    return html`<div class="disclosure" ref=${(node) => { this.root = node; }}>
      <button type="button" class=${cx("icon", "disclosure-open", tone && `disclosure-${tone}`)}
              aria-expanded=${open ? "true" : "false"} aria-controls=${id}
              aria-label=${label} title=${label}
              onClick=${() => this.setState({ open: !open })}>
        <${Icon} name=${icon} />
        ${badge != null ? html`<span class="disclosure-badge">${badge}</span>` : null}
      </button>
      <div id=${id} class="disclosure-panel" role="dialog" aria-label=${label} hidden=${!open}>
        ${open ? children : null}
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
      tone=${urgent ? "alarm" : items.length ? "warn" : null}
      badge=${items.length || null}>
    <h2 class="disclosure-title">What needs attention</h2>
    ${items.length === 0
      ? html`<p class="hint">Nothing. The appliance has raised no warnings and no ingest gap is
          open.</p>`
      : html`<ul class="notice-list">${items.map((item, index) => html`
          <li key=${index} class=${cx(item.urgent && "notice-urgent")}>
            <${Icon} name=${item.urgent ? "warn" : "info"} />
            <span class="notice-text">${item.text}</span>
            ${item.href
              ? html`<a class="tap" href=${item.href}>Settings →</a>`
              : null}
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
export function Health({ stats, rate }) {
  const state = healthState(stats);
  const receiver = stats?.receiver;
  return html`<${Disclosure} id="healthPanel" icon="pulse" tone=${state.tone}
      label=${`Appliance health: ${state.word}. Open the numbers.`}>
    <h2 class="disclosure-title">Is the appliance keeping up?</h2>
    <p class=${cx("health-state", `health-${state.key}`)}>${state.word}</p>
    <dl class="kv health-kv">
      <dt>queue depth</dt>
      <dd>${count(stats?.queue_depth ?? 0)}${" "}
        <span class="muted">traps parsed and not yet correlated</span></dd>
      <dt>p95 latency</dt>
      <dd>${score(stats?.latency_p95_s ?? 0, 4)} s${" "}
        <span class="muted">arrival to correlation</span></dd>
      <dt>trap rate</dt>
      <dd>${rate
        ? html`${rate.perSecond.toFixed(rate.perSecond < 10 ? 2 : 0)} /s${" "}
            <span class="muted">over the last ${rate.windowS.toFixed(1)} s</span>`
        : html`—${" "}<span class="muted">waiting for a second reading</span>`}</dd>
      ${receiver ? html`<dt>refused</dt>
        <dd>${count(receiver.denied)}${" "}
          <span class="muted">source not in the allowlist</span></dd>
        <dt>dropped</dt>
        <dd>${count(receiver.dropped)}${" "}
          <span class="muted">queue full — an ingest gap</span></dd>` : null}
    </dl>
    ${receiver ? null : html`<p class="hint">No <code>receiver</code> block in
      <code>/api/stats</code>: the API is running without the process runner, so there are no
      socket counters to show.</p>`}
    <p class="hint">CPU, memory and disk are not measured by this appliance. Nothing here is
      estimated.</p>
  <//>`;
}
