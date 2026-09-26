/* The shared vocabulary every screen renders with: the four states, the dense table, the badges.
 *
 * ## A state for every state, and the empty one is the important one (§IV.1)
 *
 * A zero-config product's first screen is **always** empty. An operator who plugs this appliance
 * in and opens the console sees nothing, and what that nothing says is the product's first
 * sentence. So `Empty` takes two required things — what will fill this screen, and what the
 * operator can do meanwhile — and a screen that supplies neither will not compile into anything
 * useful, which is deliberate.
 *
 * `Failed` never renders a bare status code. It renders the server's own `detail`, because that
 * string is written to be actionable ("weights must sum to at least 0.6"), and it offers a retry
 * because a monitoring console that needs a page reload during an incident is a broken tool.
 *
 * `Partial` exists because `api.readAll` reports per-route outcomes. A dashboard that reads five
 * things and gets four should show four and say which one is missing. Rendering nothing because
 * one route failed is the behaviour v0.12.0 had (`try { … } catch { return; }`) and it is
 * indistinguishable, on screen, from a quiet network.
 */

import { html, Component, cx } from "./dom.js";
import { InfoTip } from "./info.js";
import { severity as severityOf, count, timeTitle, absolute, relative } from "./format.js";

/* ---------- the four states ---------- */

export function Loading({ label = "Loading" }) {
  return html`<div class="state state-loading" role="status" aria-live="polite">
    <span class="spinner" aria-hidden="true"></span>
    <span>${label}…</span>
  </div>`;
}

/**
 * The empty state. `will` is what will fill this screen; `meanwhile` is what to do until then.
 * Both are required by the call sites rather than by a runtime check — a missing one is visible
 * in review, which is where it should be caught.
 */
export function Empty({ title, will, meanwhile, children }) {
  return html`<div class="state state-empty">
    <h3>${title}</h3>
    ${will ? html`<p class="what-fills">${will}</p>` : null}
    ${meanwhile ? html`<p class="hint">${meanwhile}</p>` : null}
    ${children}
  </div>`;
}

export function Failed({ error, retry, what }) {
  const status = error && error.status;
  return html`<div class="state state-error" role="alert">
    <h3>${what ? `Could not load ${what}` : "Something did not load"}</h3>
    <p class="detail">${(error && (error.detail || error.message)) || "No reason was given."}</p>
    ${status ? html`<p class="hint">The server answered ${status}.</p>` : null}
    ${retry ? html`<button type="button" onClick=${retry}>Try again</button>` : null}
  </div>`;
}

/** One line per route that failed, above whatever did load. */
export function Partial({ results }) {
  const broken = Object.entries(results).filter(([, r]) => !r.ok);
  if (!broken.length) return null;
  return html`<div class="state state-partial" role="alert">
    <strong>Partly loaded.</strong>
    <ul>${broken.map(([key, r]) => html`<li key=${key}>
      <code>${key}</code> — ${(r.error && (r.error.detail || r.error.message)) || "no reason given"}
    </li>`)}</ul>
  </div>`;
}

/**
 * The refusal a view renders when the principal lacks its capability.
 *
 * Deliberately not an error. The operator did nothing wrong: they followed a link, or typed a
 * fragment, or kept a bookmark from when they held more. It names the capability so an admin
 * reading over their shoulder knows what to grant, and it says the request was never sent —
 * which is the property invariant 5 is about, stated where the person affected can read it.
 */
export function Refused({ view, missing }) {
  return html`<div class="state state-refused" role="alert">
    <h3>${view ? view.label : "This view"} is not available to you</h3>
    <p>Your account does not hold ${missing.length === 1 ? "the capability" : "the capabilities"}
      ${missing.map((c, i) => html`${i ? ", " : " "}<code>${c}</code>`)}.</p>
    <p class="hint">Nothing was requested from the server. An administrator can grant this under
      Governance; the appliance itself would refuse the request too.</p>
  </div>`;
}

export function Unknown({ viewId }) {
  return html`<div class="state state-empty">
    <h3>No such screen</h3>
    <p>The address <code>#/${viewId}</code> does not name a screen in this console.</p>
    <p class="hint">It may be from a newer version, or a link that was cut short.</p>
  </div>`;
}

/**
 * **Did this route's parameters change?** — one answer, for every screen that needs to ask.
 *
 * `Loader` has asked it since v0.13.0 as a private method, and `views/situations.js` did not ask it
 * at all: a hash change within one view does not remount the component, so a permalink followed
 * from inside Situations changed the address bar and opened nothing (F108, v0.16.4). Extracted
 * rather than copied, because a second implementation of *"the route moved"* is how two screens
 * come to disagree about what a route change is — and the one that disagrees is the one nobody
 * drives.
 */
export function routeKey(params) { return JSON.stringify(params ?? []); }

/* ---------- the loader: fetch on mount, all four states, one implementation ---------- */

/**
 * Base class for a view that reads before it renders.
 *
 * **`load()` runs in `componentDidMount`, and that is the whole of F53's repair at this layer.**
 * A component only mounts when `router.resolve` returned a `view` decision, so the request is
 * downstream of the capability decision by construction rather than by a check inside the
 * loader. A refused principal gets `Refused` — a different component — and this code never runs.
 */
export class Loader extends Component {
  constructor(props) {
    super(props);
    this.state = { status: "loading", data: null, error: null };
    this.reload = this.reload.bind(this);
  }

  componentDidMount() { this.reload(); }

  /** Re-read when the route's parameters change; without this a deep link would render stale. */
  componentDidUpdate(previous) {
    if (this.routeKey(previous) !== this.routeKey(this.props)) this.reload();
  }

  routeKey(props) { return routeKey(props.params); }

  async reload() {
    this.setState({ status: "loading", error: null });
    try {
      const data = await this.load();
      this.setState({ status: "ready", data });
    } catch (error) {
      this.setState({ status: "error", error });
    }
  }

  /** Subclasses implement. Must return the data the view renders from. */
  async load() { throw new Error(`${this.constructor.name} does not implement load()`); }

  /** Subclasses implement. Receives whatever `load()` resolved to. */
  view() { throw new Error(`${this.constructor.name} does not implement view()`); }

  render() {
    const { status, data, error } = this.state;
    if (status === "loading") return html`<${Loading} label=${this.loadingLabel ?? "Loading"} />`;
    if (status === "error") {
      return html`<${Failed} error=${error} retry=${this.reload} what=${this.what ?? null} />`;
    }
    return this.view(data);
  }
}

/* ---------- presentation ---------- */

export function Badge({ tone, title, children }) {
  return html`<span class=${cx("badge", tone && `badge-${tone}`)} title=${title}>${children}</span>`;
}

/**
 * **The severity level: four bars, filled by how serious the band is** (v0.23.0, #391).
 *
 * `level` is 4 for critical down to 1 for warning, 0 for indeterminate (all bars empty) and -1 for
 * unplaced (all bars dashed — nothing was read). Drawn on a 12-unit grid, bottom-aligned and rising,
 * so the count of filled bars is the order and survives greyscale with the word covered.
 */
const BARS = [[0.6, 7.6], [3.5, 5.4], [6.4, 3.2], [9.3, 1]];

/** The class a chart or bar row uses for a band's colour — the one other place a band is drawn. */
export function severityTone(key) { return `sev-${key}`; }

/** A severity mix as one bar, each band's share by its count (v0.23.0, #393). `bands` is
 *  `[{key, label}]` in scale order and `counts` maps a key to its number; zero bands are left out. */
export function SeverityMix({ bands, counts }) {
  const shown = bands.filter((b) => Number(counts[b.key]) > 0);
  const text = shown.map((b) => `${b.label} ${count(counts[b.key])}`).join(", ");
  return html`<span class="topmix" role="img" aria-label=${text || "no active alarms"}
      title=${shown.map((b) => `${b.label}: ${count(counts[b.key])}`).join("\n")}>
    ${shown.map((b) => html`<span key=${b.key} class=${`topmix-part ${severityTone(b.key)}`}
      style=${`flex-grow:${counts[b.key]}`}></span>`)}
  </span>`;
}

/** The level alone — for a legend, or anywhere a word already sits beside it. */
export function SeverityLevel({ level }) {
  const n = Number(level);
  return html`<svg class="sev-glyph" data-level=${n} viewBox="0 0 12 12"
       aria-hidden="true" focusable="false">
    ${BARS.map(([x, y], i) => html`<rect key=${i} x=${x} y=${y} width="2.1" height=${11.4 - y}
      rx="0.5" class=${n < 0 ? "sev-off sev-dashed" : i < n ? "sev-on" : "sev-off"} />`)}
  </svg>`;
}

/**
 * **The severity chip: a level AND a colour AND a word, on every screen** (v0.22.0; #391).
 *
 * `band` is `format.band(rank)` or `format.UNPLACED`. `count` prints beside the word when given.
 * Any one of the three encodings carries the band alone, which is the rule this repository keeps:
 * no meaning by colour alone.
 */
export function SeverityChip({ band, text, count: n, title, declared }) {
  return html`<span class=${cx("sev-pill", `sev-${band.key}`)} title=${title}>
    <${SeverityLevel} level=${band.level} />
    <span class="sev-text">${text ?? band.label}</span>
    ${n != null ? html`${" "}<b class="sev-count">${count(n)}</b>` : null}
    ${declared ? html`<span class="sev-mark" aria-label="declared by an operator">*</span>` : null}
  </span>`;
}

/** The chip for one alarm, with the declared-versus-learned precedence in its title (#284). */
export function SeverityBadge({ alarm }) {
  const s = severityOf(alarm);
  const title = !s.known
    ? "No severity has been read for this alarm. Nothing has been assumed."
    : s.declared
      ? `severity ${s.text}, declared by an operator` +
        (s.learned == null ? "." : `; the appliance placed ${s.learned}.`)
      : s.ruled
        ? `severity ${s.text}, from a catalogue rule` +
          (s.learned == null ? "." : `; the appliance placed ${s.learned}.`)
        : `severity ${s.text}, placed by the appliance`;
  return html`<${SeverityChip} band=${s} text=${s.text} title=${title} declared=${s.declared} />`;
}

/** The pill in a table cell. The wrapper exists so `DataTable` can insert it verbatim. */
export function SeverityCell({ alarm }) {
  return html`<td class="sev"><${SeverityBadge} alarm=${alarm} /></td>`;
}

/**
 * A dense table. `columns` is `[{ key, label, numeric?, title? }]`; `rows` is a list of objects
 * with a `key` and a `cells` map. Numeric columns are right-aligned and tabular-figured so a
 * column of counts can be scanned vertically, which is the whole reason these products are dense.
 *
 * `kind` names a table whose *layout* differs from the default, and there is exactly one: the
 * member table, whose first column is a checkbox rather than the row's identity (F109). It is a
 * class on the element, not a set of options here — a table that took layout parameters would
 * become the place every screen's layout is written, which is the opposite of one vocabulary.
 */
export function DataTable({ columns, rows, caption, empty, kind }) {
  if (!rows.length && empty) return empty;
  return html`<div class="table-scroll">
    <table class=${cx("data", kind)}>
      ${caption ? html`<caption>${caption}</caption>` : null}
      <thead><tr>${columns.map((c) => html`
        <th key=${c.key} scope="col" class=${cx(c.numeric && "num", c.wideOnly && "wide-only")}
            title=${c.title}>${c.label}</th>`)}
      </tr></thead>
      <tbody>${rows.map((row) => html`<tr key=${row.key} class=${row.tone && `row-${row.tone}`}>
        ${columns.map((c) => (
          row.cells[c.key] && row.cells[c.key].__cell
            ? row.cells[c.key].node
            : html`<td key=${c.key} class=${cx(c.numeric && "num", c.wideOnly && "wide-only")}
                >${row.cells[c.key]}</td>`))}
      </tr>`)}</tbody>
    </table>
  </div>`;
}

/* A column may be `wideOnly`: dropped below 560px, where a phone needs the number more than a
   context column another part of the screen already shows (v0.22.0, item 20). */

/** Wrap a pre-built `<td>` so `DataTable` inserts it verbatim (e.g. `SeverityCell`). */
export function cell(node) { return { __cell: true, node }; }

/** A timestamp rendered the way §IV.1 requires: absolute, relative, and the zone named. */
export function TimeCell({ ts }) {
  return html`<td title=${timeTitle(ts)}>
    <span class="ts-abs">${absolute(ts)}</span>
    <span class="ts-rel">${relative(ts)}</span>
  </td>`;
}

/** A labelled number for a dashboard tile. */
export function Stat({ label, value, note, tone, title }) {
  return html`<div class=${cx("stat", tone && `stat-${tone}`)} title=${title}>
    <div class="stat-value">${typeof value === "number" ? count(value) : value}</div>
    <div class="stat-label">${label}</div>
    ${note ? html`<div class="stat-note">${note}</div>` : null}
  </div>`;
}

/** A section heading with an optional explanation. Used by every screen, so they read alike. */
export function SectionHeading({ title, hint, id, children }) {
  /* v0.22.0: a section's explanation is one `i` beside its title, never a paragraph under it —
     the console-wide prose budget, applied at the one component every section heading uses. */
  return html`<div class="section-heading">
    <h3 id=${id}>${title}${hint
      ? html`<${InfoTip} label=${`About ${title}`}>${hint}<//>` : null}</h3>
    ${children}
  </div>`;
}
