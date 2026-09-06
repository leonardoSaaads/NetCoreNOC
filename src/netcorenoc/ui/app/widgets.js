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

  routeKey(props) { return JSON.stringify(props.params ?? []); }

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
 * **The severity pill. One component, every severity surface** (DECISIONS #277).
 *
 * A filled badge an operator recognises without reading it, and it still carries all three
 * encodings the rule requires: the **fill** (a luminosity ladder, so the ordering survives when
 * hue does not), a **glyph** whose shape is distinct from every other band's, and the element's
 * own **text**. Any one of the three carries the rank alone.
 *
 * `tests/test_ui_invariants.py::test_every_severity_band_carries_a_glyph_and_text_not_only_colour`
 * drives every band including `unknown` and fails on a badge that carries colour alone.
 */
export function SeverityBadge({ alarm }) {
  const s = severityOf(alarm);
  // **Which value is in use, and what the other one says** (v0.16.3, DECISIONS #284). A declared
  // severity wins and the learned one is never overwritten, so the pill marks the declaration and
  // names the appliance's own judgement beside it — the disagreement is the evidence.
  const title = !s.known
    ? "This element's severity has not been learned yet. It is not a default — nothing has been " +
      "assumed about how serious this alarm is."
    : s.declared
      ? `severity ${s.text} (rank ${s.rank}), declared by an operator. ` +
        (s.learned == null
          ? "The appliance has not learned a severity for this alarm class."
          : `The appliance learned ${s.learned}.`)
      : `severity ${s.text} (rank ${s.rank}), learned by the appliance`;
  return html`<span class=${cx("sev-pill", `sev-${s.key}`)} title=${title}>
    <span class="sev-glyph" aria-hidden="true">${s.glyph}</span><span class="sev-text">${s.text}</span>
    ${s.declared ? html`<span class="sev-mark" aria-label="declared by an operator">*</span>` : null}
  </span>`;
}

/** The pill in a table cell. The wrapper exists so `DataTable` can insert it verbatim. */
export function SeverityCell({ alarm }) {
  return html`<td class="sev"><${SeverityBadge} alarm=${alarm} /></td>`;
}

/**
 * A dense table. `columns` is `[{ key, label, numeric?, title? }]`; `rows` is a list of objects
 * with a `key` and a `cells` map. Numeric columns are right-aligned and tabular-figured so a
 * column of counts can be scanned vertically, which is the whole reason these products are dense.
 */
export function DataTable({ columns, rows, caption, empty }) {
  if (!rows.length && empty) return empty;
  return html`<div class="table-scroll">
    <table class="data">
      ${caption ? html`<caption>${caption}</caption>` : null}
      <thead><tr>${columns.map((c) => html`
        <th key=${c.key} scope="col" class=${cx(c.numeric && "num")} title=${c.title}>${c.label}</th>`)}
      </tr></thead>
      <tbody>${rows.map((row) => html`<tr key=${row.key} class=${row.tone && `row-${row.tone}`}>
        ${columns.map((c) => (
          row.cells[c.key] && row.cells[c.key].__cell
            ? row.cells[c.key].node
            : html`<td key=${c.key} class=${cx(c.numeric && "num")}>${row.cells[c.key]}</td>`))}
      </tr>`)}</tbody>
    </table>
  </div>`;
}

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
  return html`<div class="section-heading">
    <h3 id=${id}>${title}</h3>
    ${children}
    ${hint ? html`<p class="hint">${hint}</p>` : null}
  </div>`;
}
