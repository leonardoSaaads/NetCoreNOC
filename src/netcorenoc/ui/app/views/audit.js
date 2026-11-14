/* The audit log: who did what, when, from where — found by period, person, section and outcome
 * (ADR #486).
 *
 * The filters are the screen: a period (the last hour to the last month, or two dates), a person
 * (everyone who appears in the log, with how often), a section of the product (sign-in, people,
 * situations, models, settings, the NOC, the audit log itself, each with its count), an outcome
 * and a text. Rows open in place to show where the request came from and its details. "Load
 * older" pages back with a cursor; "Download view" saves exactly the rows on screen as CSV, and the
 * whole chain still exports as NDJSON for an auditor.
 *
 * Every read here is itself an audit row, filters included (the server writes it).
 *
 * The chain is hash-linked and keyed: an edited or deleted row breaks it at a detectable point.
 * Verification runs offline (`python -m netcorenoc audit verify`) and there is no repair — a chain
 * whose break could be repaired from a console would not be evidence of anything.
 */

import { html, Component, cx } from "../dom.js";
import { get, post } from "../api.js";
import { absolute, count, relative, timeTitle } from "../format.js";
import { Icon } from "../icons.js";
import { InfoTip } from "../info.js";
import { can } from "../session.js";
import { Destructive } from "../destructive.js";

const PAGE = 100;
const PERIODS = [["1h", "1 h", 3600], ["24h", "24 h", 86400], ["7d", "7 days", 7 * 86400],
  ["30d", "30 days", 30 * 86400], ["all", "All", null], ["custom", "Dates", null]];
const OUTCOMES = [["", "All"], ["ok", "OK"], ["denied", "Denied"], ["error", "Error"]];
const SECTION_ICON = { "sign-in": "lock", people: "users", situations: "situations", models: "promotion",
  settings: "settings", noc: "headset", audit: "audit" };
/** The section an action is filed under, from the words the server listed for each. */
function sectionOf(action, sections) {
  const head = String(action).split(".")[0];
  const found = (sections || []).find((s) => (s.words || []).includes(head));
  return found ? found.id : null;
}

function dayStart(text) { return text ? Date.parse(`${text}T00:00:00Z`) / 1000 : null; }

function csv(rows) {
  const cells = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
  const head = ["id", "time_utc", "actor", "role", "source_ip", "action", "object_type", "object_id", "outcome", "details"];
  const lines = rows.map((r) => [r.id, new Date(r.ts * 1000).toISOString(), r.actor, r.role, r.source_ip,
    r.action, r.object_type, r.object_id, r.outcome, r.details].map(cells).join(","));
  return `${head.join(",")}\n${lines.join("\n")}\n`;
}

function save(name, text, type) {
  const url = globalThis.URL.createObjectURL(new globalThis.Blob([text], { type }));
  const anchor = globalThis.document.createElement("a");
  anchor.setAttribute("href", url);
  anchor.setAttribute("download", name);
  anchor.click();
  globalThis.URL.revokeObjectURL(url);
}

function pretty(details) {
  try { return JSON.stringify(JSON.parse(details || "{}"), null, 2); } catch { return String(details || ""); }
}

export class Audit extends Component {
  constructor(props) {
    super(props);
    this.state = { facets: null, rows: null, error: null, busy: false, more: false, open: null,
      period: "7d", from: "", to: "", actor: "", section: "", outcome: "", q: "" };
    this.typing = null;
  }

  componentDidMount() {
    get("/api/audit/facets").then((facets) => this.setState({ facets })).catch(() => {});
    this.load(true);
  }

  componentWillUnmount() { globalThis.clearTimeout(this.typing); }

  query(before) {
    const { period, from, to, actor, section, outcome, q } = this.state;
    const p = new globalThis.URLSearchParams({ limit: String(PAGE) });
    const span = PERIODS.find(([key]) => key === period)?.[2];
    if (span) p.set("since", String(Math.floor(Date.now() / 1000 - span)));
    if (period === "custom") {
      if (from) p.set("since", String(dayStart(from)));
      if (to) p.set("until", String(dayStart(to) + 86399));
    }
    if (actor) p.set("actor", actor);
    if (section) p.set("section", section);
    if (outcome) p.set("outcome", outcome);
    if (q.trim()) p.set("q", q.trim());
    if (before) p.set("before", String(before));
    return `/api/audit?${p.toString()}`;
  }

  async load(reset) {
    const before = reset || !this.state.rows || !this.state.rows.length ? null
      : this.state.rows[this.state.rows.length - 1].id;
    this.setState({ busy: true, error: null });
    try {
      const page = await get(this.query(before));
      this.setState({ busy: false, more: page.length === PAGE,
        rows: reset ? page : [...(this.state.rows || []), ...page] });
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  set(patch) { this.setState(patch, () => this.load(true)); }

  search(q) {
    this.setState({ q });
    globalThis.clearTimeout(this.typing);
    this.typing = globalThis.setTimeout(() => this.load(true), 350);
  }

  async exportAll() {
    try { save("audit.ndjson", await get("/api/audit/export"), "application/x-ndjson"); }
    catch (error) { this.setState({ error }); }
  }

  render(_props, { facets, rows, error, busy, more, open, period, from, to, actor, section, outcome, q }) {
    const sections = (facets && facets.sections) || [];
    const denied = (rows || []).filter((r) => r.outcome !== "ok").length;
    return html`<div class="auditv2">
      <section class="audit-chain">
        <span class="audit-chain-mark"><${Icon} name="shield" /></span>
        <div><b>Tamper-evident.</b> Every row is hash-chained and keyed; verify offline with${" "}
          <code class="mono">python -m netcorenoc audit verify</code>.${" "}
          ${facets && facets.first ? html`<span class="muted">${count(facets.total)} rows since${" "}
            ${absolute(facets.first)}.</span>` : null}</div>
        <div class="audit-chain-acts">
          <button type="button" class="btn-ghost" disabled=${!rows || !rows.length}
            onClick=${() => save("audit-view.csv", csv(rows), "text/csv")}><${Icon} name="download" /> Download view</button>
          ${can("audit.export") ? html`<button type="button" class="btn-ghost" onClick=${() => this.exportAll()}>
            <${Icon} name="download" /> Export all (NDJSON)</button>` : null}
        </div>
      </section>

      <div class="audit-filters" role="search" aria-label="Filter the audit log">
        <div class="seg" role="radiogroup" aria-label="Period">
          ${PERIODS.map(([key, label]) => html`<button type="button" key=${key} role="radio"
            aria-checked=${period === key} class=${cx("seg-btn", period === key && "on")}
            onClick=${() => this.set({ period: key })}>${label}</button>`)}
        </div>
        ${period === "custom" ? html`<span class="audit-dates">
          <input type="date" aria-label="From" value=${from} onChange=${(e) => this.set({ from: e.currentTarget.value })} />
          <span class="muted">to</span>
          <input type="date" aria-label="To" value=${to} onChange=${(e) => this.set({ to: e.currentTarget.value })} /></span>` : null}
        <select aria-label="Person" value=${actor} onChange=${(e) => this.set({ actor: e.currentTarget.value })}>
          <option value="">Everyone</option>
          ${((facets && facets.actors) || []).map((a) => html`<option key=${a.actor} value=${a.actor}>${a.actor} (${a.count})</option>`)}
        </select>
        <div class="seg" role="radiogroup" aria-label="Outcome">
          ${OUTCOMES.map(([key, label]) => html`<button type="button" key=${key || "all"} role="radio"
            aria-checked=${outcome === key} class=${cx("seg-btn", outcome === key && "on", key && `seg-${key}`)}
            onClick=${() => this.set({ outcome: key })}>${label}</button>`)}
        </div>
        <label class="audit-search"><${Icon} name="search" /><input type="search" placeholder="Action, person or object"
          aria-label="Search" value=${q} onInput=${(e) => this.search(e.currentTarget.value)} /></label>
      </div>

      <div class="audit-sections" role="radiogroup" aria-label="Section">
        <button type="button" role="radio" aria-checked=${!section} class=${cx("audit-chip", !section && "on")}
          onClick=${() => this.set({ section: "" })}>All</button>
        ${sections.map((s) => html`<button type="button" key=${s.id} role="radio" aria-checked=${section === s.id}
          class=${cx("audit-chip", section === s.id && "on")} onClick=${() => this.set({ section: s.id })}>
          <${Icon} name=${SECTION_ICON[s.id] || "audit"} /> ${s.label} <span class="audit-chip-n">${count(s.count)}</span></button>`)}
      </div>

      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
      ${rows === null ? html`<p class="muted">Reading the audit log…</p>` : rows.length === 0
        ? html`<div class="audit-empty"><${Icon} name="search" /> Nothing matches these filters.</div>`
        : html`<p class="audit-summary muted">${count(rows.length)} ${rows.length === 1 ? "entry" : "entries"}
            ${more ? " so far" : ""}${denied ? html` · <span class="warn-text">${denied} not OK</span>` : ""}</p>
          <ol class="audit-rows">
            ${rows.map((r) => {
              const sec = sectionOf(r.action, sections);
              const isOpen = open === r.id;
              return html`<li key=${r.id} class=${cx("audit-row", r.outcome !== "ok" && "is-bad", isOpen && "is-open")}>
                <button type="button" class="audit-line" aria-expanded=${isOpen}
                  onClick=${() => this.setState({ open: isOpen ? null : r.id })}>
                  <span class="audit-when" title=${timeTitle(r.ts)}>${relative(r.ts)}</span>
                  <span class="audit-who"><span class="audit-initial" aria-hidden="true">${String(r.actor || "?").slice(0, 2).toUpperCase()}</span>
                    <span>${r.actor}${r.role ? html` <small class="muted">${r.role}</small>` : null}</span></span>
                  <span class="audit-what"><${Icon} name=${SECTION_ICON[sec] || "audit"} /><code class="mono">${r.action}</code></span>
                  <span class="audit-obj muted">${r.object_type ? `${r.object_type}${r.object_id ? `/${r.object_id}` : ""}` : "—"}</span>
                  <span class=${`outcome outcome-${r.outcome}`}>${r.outcome}</span>
                  <span class="audit-caret" aria-hidden="true"><${Icon} name="chevron" /></span>
                </button>
                ${isOpen ? html`<div class="audit-detail">
                  <dl><div><dt>When</dt><dd>${absolute(r.ts)}</dd></div>
                    <div><dt>From</dt><dd class="mono">${r.source_ip || "—"}</dd></div>
                    <div><dt>Row</dt><dd class="mono">#${r.id}</dd></div></dl>
                  <pre class="mono">${pretty(r.details)}</pre>
                </div>` : null}
              </li>`;
            })}
          </ol>
          ${more ? html`<button type="button" class="audit-more" disabled=${busy} onClick=${() => this.load(false)}>
            ${busy ? "Reading…" : "Load older"}</button>` : null}`}

      ${can("audit.prune") ? html`<details class="audit-retention">
        <summary>Retention <${InfoTip} label="About pruning">Deletes rows older than the audit retention
          (Settings → System). The rows that remain still verify against each other; the deleted ones
          cannot be recovered.<//></summary>
        <${Prune} onDone=${() => this.load(true)} />
      </details>` : null}
    </div>`;
  }
}

/** `POST /api/audit/prune`. The route has no preview mode, and the preview says so. */
function Prune({ onDone }) {
  return html`<${Destructive}
    title="Prune the audit log"
    hint="Deletes rows older than the retention bound. A prune is visible in the chain but not reversible."
    confirmLabel="Prune now"
    previewLabel="Preview what would be pruned"
    consequence=${"Audit rows are deleted permanently. The rows that remain still verify " +
                  "against each other; the deleted ones cannot be recovered from this appliance."}
    preview=${async () => ({
      measured: false,
      message: "This route has no preview mode: the appliance does not count what a prune would " +
               "remove without performing it. Pruning removes rows older than the configured " +
               "audit retention, which is on the Settings screen.",
    })}
    apply=${async () => post("/api/audit/prune", {})}
    onDone=${onDone} />`;
}
