/* The trap catalogue — every trap OID the appliance has seen, what it is called, how serious it
 * is, and on whose word (v0.22.0, items 16-18, #384, #385).
 *
 * It was "Alarm classes": one unbounded list of OIDs under two paragraphs. It is renamed to say
 * what it holds, the paragraphs went (one fact survived, behind the info icon: a name or severity
 * set here does not change correlation), and it gained the three things the maintainer asked for:
 *
 *   * **a branch browser** (`parts/oidtree.js`) — name a whole vendor branch in a few clicks, and
 *     every trap beneath inherits it, by arc;
 *   * **an import** (`parts/importbox.js`) — a customer's own list of class, OID, vendor, severity,
 *     checked row by row, all or nothing;
 *   * **a bounded, searchable table** that says, for every class, **which rule is winning**.
 *
 * Every filter is a query parameter answered by the server before the page is cut.
 */

import { html, Component } from "../dom.js";
import { get, post } from "../api.js";
import { DataTable, Empty, Failed, Loading, SeverityChip, cell } from "../widgets.js";
import { band, count } from "../format.js";
import { can } from "../session.js";
import { InfoTip } from "../info.js";
import { BranchBrowser, ROOT } from "./parts/oidtree.js";
import { ImportBox } from "./parts/importbox.js";

const PAGE = 25;
const SEARCH_DEBOUNCE_MS = 250;

/** Where a name or a severity came from, as the tag beside it. */
function Source({ source, rule }) {
  if (!source) return null;
  const words = {
    declared: rule ? (rule.subtree ? "branch rule" : "rule") : "declared",
    imported: "imported",
    standard: "standard",
    builtin: "built-in",
  };
  const where = rule && rule.source === "builtin"
    ? ` — the vendor's MIB (${rule.origin}); the severity is a default you can change`
    : rule ? ` on ${rule.oid}${rule.subtree ? " and beneath" : ""}` : "";
  return html`<span class="cat-source" title=${`${words[source] ?? source}${where}`}>
    ${words[source] ?? source}</span>`;
}

export class Classes extends Component {
  constructor(props) {
    super(props);
    this.state = { page: null, error: null, q: "", offset: 0, node: ROOT, imported: 0 };
    this.reload = this.reload.bind(this);
  }

  componentDidMount() { this.reload(); }

  componentWillUnmount() { if (this.timer) clearTimeout(this.timer); }

  async reload() {
    const { q, offset, node } = this.state;
    const under = node === ROOT ? "" : `&under=${encodeURIComponent(node)}`;
    try {
      const [page, rules] = await Promise.all([
        get(`/api/catalogue?limit=${PAGE}&offset=${offset}${under}` +
            `${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`),
        get("/api/catalogue/rules?source=imported&limit=1"),
      ]);
      this.setState({ page, imported: rules.total, error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  search(text) {
    this.setState({ q: text, offset: 0 });
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(this.reload, SEARCH_DEBOUNCE_MS);
  }

  render(_props, { page, error, q, offset, node, imported }) {
    if (error && !page) return html`<${Failed} error=${error} retry=${this.reload} what="the catalogue" />`;
    if (!page) return html`<${Loading} label="Reading the trap catalogue" />`;
    const namer = can("label.write");
    const rows = page.classes.map((c) => ({
      key: c.id,
      cells: {
        trap: html`<span class="cat-name">${c.name || html`<span class="muted">unnamed</span>`}</span>
          ${" "}<${Source} source=${c.name_source} rule=${c.name_rule} />
          <code class="cat-oid">${c.oid}</code>`,
        vendor: c.vendor || "—",
        severity: cell(html`<td class="sev">
          ${c.severity
            ? html`<span class="cat-sev"><${SeverityChip} band=${band(c.severity_rank)} />
                ${" "}<${Source} source=${c.severity_source} rule=${c.severity_rule} /></span>`
            : html`<span class="muted" title="Placed per alarm: the trap's own word, or learned"
                >per alarm</span>`}
        </td>`),
        active: count(c.active),
        act: namer ? html`<${ClassName} klass=${c} onDone=${this.reload} />` : null,
      },
    }));
    const columns = [
      { key: "trap", label: "trap" },
      { key: "vendor", label: "vendor", wideOnly: true },
      { key: "severity", label: "severity" },
      { key: "active", label: "active", numeric: true },
    ];
    if (namer) columns.push({ key: "act", label: "" });
    return html`<div class="catalogue">
      <div class="filters cat-bar" role="search">
        <label class="visually-hidden" for="catQ">Search traps</label>
        <input id="catQ" type="search" value=${q} placeholder="search name, OID or vendor"
               onInput=${(e) => this.search(e.target.value)} />
        <span class="filter-count">${count(page.total)} classes</span>
        <${InfoTip} label="What a name or severity here changes">
          A name or a severity set here — for one trap, for a branch, or by import — renames and
          grades it on every screen. It changes nothing about how alarms are grouped, nor what a
          maintenance window collects.
        <//>
      </div>
      <${BranchBrowser} node=${node} editable=${can("catalogue.write")}
        onNode=${(to) => this.setState({ node: to, offset: 0 }, this.reload)}
        onChanged=${this.reload} />
      ${page.total === 0
        ? html`<${Empty} title=${q || node !== ROOT ? "No trap matches." : "No trap has arrived yet."}
            will="A trap appears here the first time one of its kind arrives." />`
        : html`<${DataTable} columns=${columns} rows=${rows}
            caption=${node === ROOT ? "Traps, busiest first" : `Traps beneath ${node}, busiest first`} />
          <div class="pager">
            <span>${count(offset + 1)}–${count(offset + page.classes.length)} of ${count(page.total)}</span>
            <button type="button" disabled=${offset === 0}
              onClick=${() => this.setState({ offset: Math.max(0, offset - PAGE) }, this.reload)}
              >Previous</button>
            <button type="button" disabled=${offset + PAGE >= page.total}
              onClick=${() => this.setState({ offset: offset + PAGE }, this.reload)}>Next</button>
          </div>`}
      ${can("catalogue.import")
        ? html`<${ImportBox} imported=${imported} onDone=${this.reload} />` : null}
    </div>`;
  }
}

/** Name one class — the per-class declaration, which wins over every rule (#284). */
class ClassName extends Component {
  constructor(props) {
    super(props);
    this.state = { editing: false, value: props.klass.label || "", busy: false, error: null };
  }

  async save(event) {
    event.preventDefault();
    const label = this.state.value.trim();
    if (!label || this.state.busy) return;
    this.setState({ busy: true, error: null });
    try {
      await post("/api/labels", { kind: "class", id: this.props.klass.id, label });
      this.setState({ busy: false, editing: false });
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render({ klass }, { editing, value, busy, error }) {
    if (!editing) {
      return html`<button type="button" class="tap"
        onClick=${() => this.setState({ editing: true })}
      >${klass.label ? "Rename" : "Name"}</button>`;
    }
    return html`<form class="inline-form" onSubmit=${(e) => this.save(e)}>
      <label class="visually-hidden" for=${`cls-${klass.id}`}>Name for class ${klass.id}</label>
      <input id=${`cls-${klass.id}`} value=${value} maxlength="80" autocomplete="off"
             onInput=${(e) => this.setState({ value: e.target.value })} />
      <button type="submit" class="primary" disabled=${busy || !value.trim()}>Save</button>
      <button type="button" onClick=${() => this.setState({ editing: false, error: null })}
      >Cancel</button>
      ${error ? html`<span class="err" role="alert">${error.detail || error.message}</span>` : null}
    </form>`;
  }
}

