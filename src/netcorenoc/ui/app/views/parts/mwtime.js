/* The two time controls a maintenance window needs, and nothing else (v0.21.0, D2 / IV.1).
 *
 * ## The timeline bar: the composed filter drawn in 40 pixels instead of written in a paragraph
 *
 * Leading band, window, trailing band, *now* marked. **Hand-written SVG**, because the DOM harness
 * cannot see d3 and cannot see whitespace (Appendix B), and a band an operator relies on must be
 * something a test can assert the existence and the width of. Every rect below carries a
 * `data-band`, so `tests/test_maintenance_dom.py` reads the geometry rather than a screenshot.
 *
 * It is not a chart. There is no axis, no gridline and no tick: it answers one question — *"how
 * much of this is patch band, and where is now?"* — and a chart would answer several.
 *
 * ## The zone picker: a combobox over cities, storing zones
 *
 * Three of the cities an operator names have **no IANA zone of their own**, so this stores
 * `America/Sao_Paulo` when they type *"Brasília"*. It is a `<datalist>`-free hand-written listbox
 * for one reason: `<datalist>` cannot carry a value distinct from its label, which is the whole
 * requirement.
 *
 * ## The one line D2 is mandatory for
 *
 * `SiteAndYourTime` renders *"10:00 Riyadh · 04:00 your time (Brasília)"*. An engineer in Brasília
 * scheduling a window at a site in Riyadh reads both clocks in one line, and that is the whole
 * reason the zone is stored rather than an offset.
 */

import { html, Component } from "../../dom.js";
import { get } from "../../api.js";
import { TIMEZONE } from "../../format.js";

/* Where a maintenance window's bands sit on a 0..1 axis, and where `now` falls on it.
 *
 * Pure, and exported so the DOM harness can drive it directly: the geometry is the thing that can
 * be wrong, and asserting it through rendered attributes alone would make every failure a
 * rendering failure. `span` is padded so a window with a zero patch band still draws.
 */
export function bands({ startsAt, endsAt, patchS, now }) {
  const lead = Math.max(0, patchS || 0);
  const from = startsAt - lead;
  const to = endsAt + lead;
  const span = Math.max(1, to - from);
  const at = (t) => Math.min(1, Math.max(0, (t - from) / span));
  return {
    lead: { x: 0, w: at(startsAt) },
    window: { x: at(startsAt), w: at(endsAt) - at(startsAt) },
    trail: { x: at(endsAt), w: 1 - at(endsAt) },
    now: at(now),
    nowInside: now >= from && now <= to,
  };
}

const WIDTH = 320;
const HEIGHT = 34;

/* The timeline bar. 40 pixels of SVG that say what a paragraph would. */
export function TimelineBar({ startsAt, endsAt, patchS, now }) {
  const b = bands({ startsAt, endsAt, patchS, now });
  const px = (v) => Math.round(v * WIDTH * 10) / 10;
  return html`<svg
    class="mw-timeline"
    viewBox="0 0 ${WIDTH} ${HEIGHT}"
    width="100%"
    height=${HEIGHT}
    role="img"
    aria-label=${`The window and its patch bands. ${
      b.nowInside ? "Now is inside the drawn range." : "Now is outside the drawn range."
    }`}
  >
    <rect data-band="lead" x="0" y="10" width=${px(b.lead.w)} height="14" rx="2" />
    <rect data-band="window" x=${px(b.window.x)} y="6" width=${px(b.window.w)} height="22" rx="2" />
    <rect data-band="trail" x=${px(b.trail.x)} y="10" width=${px(b.trail.w)} height="14" rx="2" />
    ${b.nowInside
      ? html`<line data-band="now" x1=${px(b.now)} y1="2" x2=${px(b.now)} y2=${HEIGHT - 2} />`
      : null}
  </svg>`;
}

/* `HH:MM` in the **browser's own zone** (v0.21.0, D2).
 *
 * The one deliberate exception to *"every rendered time carries its offset"*, and it earns the
 * exception by never appearing alone: it is the second half of the line below —
 * *"10:00 Riyadh · 04:00 your time (Brasília)"* — where the zone is named in the prose beside it
 * and the site's own clock is right there for comparison. Appending `-03:00` to a number already
 * labelled *your time* would make the line longer and no clearer, on a control an operator reads
 * at 390 px with one thumb.
 *
 * **It lives here rather than in `format.js`, for two reasons.** With it, `format.js` was 1 023
 * bytes over the module-graph ceiling — F127's shape exactly, and the same remedy: the addition
 * moves to its only consumer. And `chartdata.js` already exports a `clock(ts, spanS)`: a different
 * function, a different arity, the same word. A second `clock` in the module every view imports is
 * two things under one name, which is how a caller comes to get the other one.
 *
 * Built from the local getters rather than from `Intl`, so the output does not move with the
 * runner's locale — which is what lets the DOM harness assert on it. It is not a `toLocale*` call,
 * so `test_no_screen_renders_a_bare_locale_timestamp` is unaffected: that guard is about a time
 * whose zone nothing states, and this one's zone is named in the sentence around it.
 */
function localClock(epochSeconds) {
  if (epochSeconds == null || Number.isNaN(epochSeconds)) return "—";
  const d = new Date(epochSeconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* **The line D2 exists for.** The site's clock and the operator's own, side by side.
 *
 * Both are computed from the SERVER's rendering of the site zone (`site_time`, `site_offset`) and
 * the browser's own zone for the second half — never from an offset the client stored, because a
 * window across a DST transition has two.
 */
export function SiteAndYourTime({ instant, siteZone, siteTime, siteOffset }) {
  const site = (siteTime || "").slice(11, 16);
  const mine = localClock(instant);
  const sameZone = siteZone === TIMEZONE;
  return html`<p class="mw-clocks" data-role="clocks">
    <strong>${site || "—"}</strong> ${cityOf(siteZone)}
    ${sameZone
      ? html`<span class="muted"> · this is your own time zone</span>`
      : html`<span class="muted"> · <strong>${mine}</strong> your time (${cityOf(TIMEZONE)})</span>`}
    <span class="muted mono"> ${siteOffset || ""}</span>
  </p>`;
}

/* `America/Sao_Paulo` -> `Sao Paulo`. For display only; the stored value is always the zone. */
export function cityOf(zone) {
  if (!zone) return "";
  const tail = zone.split("/").pop() || zone;
  return tail.replace(/_/g, " ");
}

/* A searchable list of cities that stores canonical zones.
 *
 * Every keystroke asks the server, because the server is the only thing that knows what THIS host
 * can resolve — a list compiled into the console would be the build-machine trap one layer up.
 * The request is debounced to one in flight; an operator types faster than a round trip and a
 * queue of stale answers would make the list flicker backwards.
 */
export class ZonePicker extends Component {
  constructor(props) {
    super(props);
    this.state = { q: "", zones: [], open: false, busy: false };
    this._seq = 0;
  }

  componentDidMount() { this.search(""); }

  async search(q) {
    const seq = ++this._seq;
    this.setState({ q, busy: true });
    try {
      const body = await get(`/api/timezones?q=${encodeURIComponent(q)}`);
      if (seq !== this._seq) return;  // a later keystroke already won
      this.setState({ zones: body.zones || [], busy: false });
    } catch {
      if (seq === this._seq) this.setState({ zones: [], busy: false });
    }
  }

  choose(zone) {
    this.setState({ open: false, q: "" });
    this.props.onPick(zone.zone);
  }

  view() {
    const { zones, open, q } = this.state;
    return html`<div class="mw-zone">
      <label for="mw-zone-input">Site city</label>
      <input
        id="mw-zone-input"
        type="text"
        role="combobox"
        aria-expanded=${open ? "true" : "false"}
        aria-controls="mw-zone-list"
        autocomplete="off"
        placeholder=${cityOf(this.props.value) || "Search a city"}
        value=${q}
        onFocus=${() => this.setState({ open: true })}
        onInput=${(e) => { this.setState({ open: true }); this.search(e.target.value); }}
      />
      ${open
        ? html`<ul id="mw-zone-list" class="mw-zone-list" role="listbox">
            ${zones.map(
              (zone) => html`<li role="option" key=${zone.zone + zone.label}>
                <button type="button" onClick=${() => this.choose(zone)}>
                  <span class="mw-zone-city">${zone.label}</span>
                  <span class="muted mono">${zone.zone}</span>
                  <span class="muted">${zone.offset}</span>
                </button>
              </li>`,
            )}
            ${zones.length === 0
              ? html`<li class="muted">No zone here matches that.</li>`
              : null}
          </ul>`
        : null}
    </div>`;
  }

  render() { return this.view(); }
}

/* `2026-05-14T10:00:00-03:00` for a local datetime-input value read in the SITE's zone.
 *
 * **The one piece of arithmetic this file does**, and it is the one the API's naive-datetime
 * refusal exists to force: a `<input type="datetime-local">` yields `2026-05-14T10:00` with no
 * zone at all, and sending that would have the appliance guess. The offset is taken from the
 * server's own rendering of that zone at approximately that instant, so a window across a DST
 * transition takes the offset in force at its start rather than the browser's.
 */
export function withOffset(localValue, offsetLabel) {
  if (!localValue) return "";
  const offset = (offsetLabel || "UTC+00:00").replace("UTC", "") || "+00:00";
  const seconds = localValue.length === 16 ? `${localValue}:00` : localValue;
  return `${seconds}${offset}`;
}
