/* The two disclosures in the top bar: what is wrong (the bell), and whether the appliance is
 * keeping up (`health.js`, which imports the mechanism from here).
 *
 * They replaced four counters that wrapped the phone's top bar onto four rows (DECISIONS #288,
 * #289). Both panels are anchored to the bar's right edge (F111), both close on Escape, a second
 * press, and a click outside, and the ingest gap stays a banner as well as a bell entry — "traps
 * are being dropped now" is not something to open a panel to learn.
 */

import { html, Component, cx } from "./dom.js";
import { Icon } from "./icons.js";
import { absolute, count, plural } from "./format.js";
import { get, post, del } from "./api.js";
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

/** The snooze intervals, in the words the control uses. `change` is refused for security. */
const SNOOZES = [["24h", "24 h"], ["7d", "7 days"], ["change", "until it changes"]];

/**
 * **The bell** — the warnings, a link to the setting that resolves each one that has one, and a
 * per-user snooze (v0.22.0, item 1, ADR #387).
 *
 * The maintainer asked for an ×. A permanently dismissible security warning is how appliances ship
 * insecure, so the × is a **snooze**: for an interval the operator picks, for them alone, audited,
 * and keyed on the warning's text so a changed warning returns. A security warning cannot be
 * snoozed "until it changes". A snoozed warning is not gone: the bell counts it in a muted badge
 * and lists it under the live ones, where it can be restored. A warning whose condition is fixed
 * stops being emitted on the next poll whether or not anyone snoozed it.
 */
export class Bell extends Component {
  constructor(props) {
    super(props);
    this.state = { snoozes: {}, security: {}, offer: null, showSnoozed: false, error: null };
    this.seen = "";
  }

  componentDidMount() { this.read(); }

  componentDidUpdate() {
    const now = (this.props.stats?.warnings ?? []).join("\n");
    if (now !== this.seen) this.read();
  }

  /** Which of the current warnings this user has snoozed. Read when the set of warnings changes. */
  async read() {
    this.seen = (this.props.stats?.warnings ?? []).join("\n");
    try {
      const body = await get("/api/notices");
      const snoozes = {};
      const security = {};
      const digests = {};
      for (const n of body.notices || []) {
        digests[n.text] = n.digest;
        security[n.text] = n.security;
        if (n.snoozed) snoozes[n.text] = n.snoozed;
      }
      this.digests = digests;
      this.setState({ snoozes, security, error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  async snooze(text, mode) {
    try {
      await post("/api/notices/snooze", { digest: this.digests[text], mode });
      this.setState({ offer: null });
      await this.read();
    } catch (error) {
      this.setState({ error });
    }
  }

  async restore(text) {
    try {
      await del(`/api/notices/snooze/${this.digests[text]}`);
      await this.read();
    } catch (error) {
      this.setState({ error });
    }
  }

  render({ stats }, { snoozes, security, offer, showSnoozed, error }) {
    const items = notices(stats);
    const live = items.filter((item) => item.urgent || !snoozes[item.text]);
    const quiet = items.filter((item) => !item.urgent && snoozes[item.text]);
    const urgent = live.some((item) => item.urgent);
    const label = live.length
      ? `${plural(live.length, "warning")}${quiet.length ? `, ${count(quiet.length)} snoozed` : ""}`
        + " — open the list"
      : quiet.length ? `${plural(quiet.length, "snoozed warning")}. Open the list.`
        : "No warnings. Open the list.";
    const row = (item, snoozed) => html`<li key=${item.text}
        class=${cx(item.urgent && "notice-urgent", snoozed && "notice-snoozed")}>
      <${Icon} name=${item.urgent ? "warn" : "info"} />
      <div class="notice-body">
        <p class="notice-text">
          ${security[item.text] ? html`<span class="notice-kind">security</span>${" "}` : null}
          ${item.text}
        </p>
        <div class="notice-acts">
          ${item.href && !snoozed
            ? html`<a class="tap notice-link" href=${item.href}>Open the setting →</a>` : null}
          ${snoozed
            ? html`<span class="muted">snoozed ${snoozeText(snoozed)}</span>
                <button type="button" class="tap" onClick=${() => this.restore(item.text)}
                >Restore</button>`
            : item.urgent ? null
              : offer === item.text
                ? html`<span class="notice-snooze" role="group" aria-label="Snooze for">
                    ${SNOOZES.filter(([mode]) => !(mode === "change" && security[item.text]))
                      .map(([mode, words]) => html`<button type="button" class="tap" key=${mode}
                        onClick=${() => this.snooze(item.text, mode)}>${words}</button>`)}
                  </span>`
                : html`<button type="button" class="tap"
                    onClick=${() => this.setState({ offer: item.text })}>Snooze</button>`}
        </div>
      </div>
    </li>`;
    return html`<${Disclosure} id="noticePanel" icon="bell" label=${label}
        title="What needs attention"
        tone=${urgent ? "alarm" : live.length ? "warn" : null}
        badge=${live.length || (quiet.length ? html`<span class="notice-muted-count">${quiet.length}</span>`
          : null)}>
      ${error ? html`<p class="hint">${error.message}</p>` : null}
      ${live.length === 0 && !quiet.length
        ? html`<p class="hint">Nothing to attend to.</p>`
        : html`<ul class="notice-list">${live.map((item) => row(item, null))}</ul>`}
      ${quiet.length
        ? html`<button type="button" class="tap notice-quiet"
              aria-expanded=${showSnoozed ? "true" : "false"}
              onClick=${() => this.setState({ showSnoozed: !showSnoozed })}>
            ${plural(quiet.length, "snoozed warning")}${
              quiet.some((item) => security[item.text]) ? " (security)" : ""}
          </button>
          ${showSnoozed
            ? html`<ul class="notice-list">${quiet.map((item) => row(item, snoozes[item.text]))}</ul>`
            : null}`
        : null}
    <//>`;
  }
}

/** `for 7 days (until 02 Oct 14:00)` / `until it changes`. */
function snoozeText(snoozed) {
  if (snoozed.until == null) return "until it changes";
  return `until ${absolute(snoozed.until)}`;
}
