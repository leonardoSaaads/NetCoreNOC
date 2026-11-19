/* Import a trap list — **trap OID, class, vendor, severity** — from a CSV or text file
 * (v0.22.0, item 17, #385; redrawn in ADR #493).
 *
 * The owner's complaint: nobody knew what file to give it (a MIB? a CSV?) or what it should hold.
 * So the box shows the answer before anything is asked of it, the way an upload in Paperclip or
 * Linear does:
 *
 *   * **the columns as a table** — each with whether it is needed and an example row, and the
 *     template to download, already filled with that example;
 *   * **a drop zone** — drag the file in or browse; CSV or TXT, never a MIB (a MIB is a compiler's
 *     input; export its notifications to a list first);
 *   * **a preview** — the file's first rows as the appliance will read them, its columns marked as
 *     understood or ignored, before the server is asked anything.
 *
 * Then the two steps the server's all-or-nothing rule needs: **Check** sends the file as a dry run
 * and lists every problem by line, field and rule; **Import** is offered only when nothing is wrong.
 * An imported row names and grades a trap; it is not evidence, and a colleague's declaration still
 * wins over it — the check lists the rows that will be shadowed.
 */

import { html, Component, cx } from "../../dom.js";
import { post, del } from "../../api.js";
import { count, plural } from "../../format.js";
import { Icon } from "../../icons.js";

/** The server's caps, mirrored so a file that cannot pass is refused before it is sent. */
const MAX_BYTES = 256 * 1024;
const MAX_ROWS = 5000;
/** The column names the server reads (`api/catalogue_import.COLUMNS`), case and spacing aside. */
const KNOWN = { class: "class", name: "class", "class name": "class", trap_oid: "trap_oid",
  "trap oid": "trap_oid", oid: "trap_oid", vendor: "vendor", severity: "severity" };
const FORMAT = [
  ["trap_oid", "Required", "1.3.6.1.4.1.6141.2.60.5.0.3"],
  ["class", "A name for it", "linkDown"],
  ["vendor", "Optional", "ciena"],
  ["severity", "critical · major · minor · warning", "major"],
];
const TEMPLATE = "trap_oid,class,vendor,severity\n"
  + "1.3.6.1.4.1.6141.2.60.5.0.3,linkDown,ciena,major\n"
  + "1.3.6.1.4.1.6141.2.60.5.0.4,linkUp,ciena,warning\n"
  + "1.3.6.1.4.1.2011.2.15.2.4.3.3.1,hwOpticalPowerAbnormal,huawei,critical\n";
const PREVIEW_ROWS = 5;

/** The file as the server will split it: the first non-blank line is the header, and the
 * delimiter is the one it holds most of. Quotes are honoured; nothing is interpreted. */
export function preview(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (!lines.length) return null;
  const delimiter = [",", ";", "\t"].reduce((a, b) => (lines[0].split(b).length > lines[0].split(a).length ? b : a));
  const cells = (line) => {
    const out = []; let cur = ""; let quoted = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (ch === '"' && quoted && line[i + 1] === '"') { cur += '"'; i += 1; }
      else if (ch === '"') quoted = !quoted;
      else if (ch === delimiter && !quoted) { out.push(cur); cur = ""; }
      else cur += ch;
    }
    out.push(cur);
    return out.map((c) => c.trim());
  };
  const head = cells(lines[0]);
  const columns = head.map((h) => KNOWN[h.toLowerCase().split(/\s+/).join(" ")] || null);
  return { head, columns, rows: lines.slice(1, 1 + PREVIEW_ROWS).map(cells), total: lines.length - 1,
    delimiter: { ",": "commas", ";": "semicolons", "\t": "tabs" }[delimiter],
    hasOid: columns.includes("trap_oid") };
}

function save(name, text) {
  const url = globalThis.URL.createObjectURL(new globalThis.Blob([text], { type: "text/csv" }));
  const anchor = globalThis.document.createElement("a");
  anchor.setAttribute("href", url);
  anchor.setAttribute("download", name);
  anchor.click();
  globalThis.URL.revokeObjectURL(url);
}

export class ImportBox extends Component {
  constructor(props) {
    super(props);
    this.state = { file: null, text: null, peek: null, report: null, busy: false, error: null,
      done: null, withdrawing: false, over: false };
  }

  pick(file) {
    this.setState({ file: null, text: null, peek: null, report: null, error: null, done: null });
    if (!file) return;
    const mib = `${file.name} is a MIB. Export its notifications as a CSV with the columns shown here.`;
    if (/\.(mib|my)$/i.test(file.name)) { this.setState({ error: { message: mib } }); return; }
    if (file.size > MAX_BYTES) {
      this.setState({ error: { message: `${file.name} is larger than 256 KiB.` } });
      return;
    }
    const reader = new globalThis.FileReader();
    reader.onload = () => {
      const text = String(reader.result);
      if (/DEFINITIONS\s*::=\s*BEGIN/.test(text.slice(0, 8000))) { this.setState({ error: { message: mib } }); return; }
      const peek = preview(text);
      if (!peek) { this.setState({ error: { message: `${file.name} is empty.` } }); return; }
      if (peek.total > MAX_ROWS) {
        this.setState({ error: { message: `${file.name} has ${count(peek.total)} rows; at most ${count(MAX_ROWS)}.` } });
        return;
      }
      this.setState({ file: file.name, text, peek });
    };
    reader.onerror = () => this.setState({ error: { message: `Could not read ${file.name}.` } });
    reader.readAsText(file);
  }

  drop(event) {
    event.preventDefault();
    this.setState({ over: false });
    const files = event.dataTransfer && event.dataTransfer.files;
    this.pick(files && files[0]);
  }

  async check() {
    const { file, text } = this.state;
    this.setState({ busy: true, error: null });
    try {
      const report = await post("/api/catalogue/import?dry_run=true", { filename: file, text });
      this.setState({ busy: false, report });
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  async commit() {
    const { file, text } = this.state;
    this.setState({ busy: true });
    try {
      const report = await post("/api/catalogue/import", { filename: file, text });
      this.setState({ busy: false, report: null, done: report, file: null, text: null, peek: null });
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  async withdraw() {
    try {
      const out = await del("/api/catalogue/imported");
      this.setState({ withdrawing: false, done: { outcome: "withdrawn", removed: out.removed } });
      this.props.onDone();
    } catch (error) {
      this.setState({ withdrawing: false, error });
    }
  }

  reset() { this.setState({ file: null, text: null, peek: null, report: null, error: null }); }

  render({ imported }, { file, peek, report, busy, error, done, withdrawing, over }) {
    const stage = report ? (report.outcome === "refused" ? 2 : 3) : peek ? 2 : 1;
    return html`<section class="panel-block importbox" aria-labelledby="ib-title">
      <header class="ib-head">
        <div><h3 id="ib-title">Import a trap list</h3>
          <p class="muted">Name and grade traps before the first one arrives.</p></div>
        <button type="button" class="tap" onClick=${() => save("trap-list-template.csv", TEMPLATE)}>
          <${Icon} name="download" /> Template</button>
      </header>
      <ol class="ib-steps">${["Choose a file", "Check it", "Import"].map((word, i) => html`<li key=${word}
        class=${cx(i + 1 < stage && "is-done", i + 1 === stage && "is-now")}><span>${i + 1 < stage
          ? html`<${Icon} name="check" />` : i + 1}</span>${word}</li>`)}</ol>

      ${peek ? null : html`<div class="ib-format table-scroll" role="group" aria-label="What the file holds">
        <table class="data compact-table"><thead><tr>${FORMAT.map(([col]) => html`<th key=${col} scope="col"><code>${col}</code></th>`)}</tr></thead>
          <tbody><tr class="ib-need">${FORMAT.map(([col, need]) => html`<td key=${col}>${need}</td>`)}</tr>
            <tr>${FORMAT.map(([col, , example]) => html`<td key=${col} class="mono">${example}</td>`)}</tr></tbody></table>
      </div>`}

      ${peek ? html`<div class="ib-file">
          <span class="ib-file-name"><${Icon} name="sheet" /> <b>${file}</b>
            <span class="muted">${plural(peek.total, "row")} · ${peek.delimiter}</span></span>
          <button type="button" class="btn-ghost" onClick=${() => this.reset()}>Choose another</button>
        </div>
        <div class="table-scroll ib-preview"><table class="data compact-table">
          <caption class="visually-hidden">The file's first rows, as they will be read</caption>
          <thead><tr>${peek.head.map((h, i) => html`<th key=${i} scope="col" class=${peek.columns[i] ? "ib-col-ok" : "ib-col-skip"}
            title=${peek.columns[i] ? `Read as ${peek.columns[i]}` : "Not a known column: ignored"}>${h || "—"}
            ${peek.columns[i] ? html` <${Icon} name="check" />` : html` <span class="muted">ignored</span>`}</th>`)}</tr></thead>
          <tbody>${peek.rows.map((r, i) => html`<tr key=${i}>${peek.head.map((_, j) => html`<td key=${j} class="mono">${r[j] || ""}</td>`)}</tr>`)}</tbody>
        </table></div>
        ${peek.hasOid ? null : html`<p class="err" role="alert">No <code>trap_oid</code> column: the first line must name the columns.</p>`}
        ${report ? null : html`<button type="button" class="primary" disabled=${busy || !peek.hasOid}
          onClick=${() => this.check()}>${busy ? "Checking…" : "Check the file"}</button>`}`
      : html`<label class=${cx("ib-drop", over && "is-over")}
          onDragOver=${(e) => { e.preventDefault(); if (!over) this.setState({ over: true }); }}
          onDragLeave=${() => this.setState({ over: false })} onDrop=${(e) => this.drop(e)}>
          <input type="file" class="visually-hidden" accept=".csv,.txt,text/csv,text/plain"
            onChange=${(e) => this.pick(e.currentTarget.files && e.currentTarget.files[0])} />
          <span class="ib-drop-icon"><${Icon} name="upload" /></span>
          <span><b>Drop a CSV here</b> or <u>browse</u></span>
          <small class="muted">CSV or TXT · commas, semicolons or tabs · up to 5,000 rows · not a MIB</small>
        </label>`}

      ${error ? html`<p class="err" role="alert">${error.message}</p>` : null}
      ${report ? html`<${Report} report=${report} busy=${busy} onImport=${() => this.commit()} />` : null}
      ${done ? html`<p class="ok-note" role="status">${done.outcome === "withdrawn"
        ? `${plural(done.removed, "imported row")} withdrawn.`
        : `${plural(done.rows, "row")} imported from ${done.filename}.`}</p>` : null}
      ${imported
        ? html`<p class="import-held">${plural(imported, "imported row")} in the catalogue.
            ${withdrawing
              ? html`<button type="button" class="danger" onClick=${() => this.withdraw()}
                  >Withdraw all ${count(imported)}</button>
                <button type="button" onClick=${() => this.setState({ withdrawing: false })}
                  >Keep them</button>`
              : html`<button type="button" class="tap"
                  onClick=${() => this.setState({ withdrawing: true })}>Withdraw…</button>`}
          </p>`
        : null}
    </section>`;
  }
}

function Report({ report, busy, onImport }) {
  if (report.outcome === "refused") {
    return html`<div class="import-report import-refused" role="alert">
      <p><b>Nothing will be imported.</b>${" "}${report.refused
        ?? `${plural(report.problems, "problem")}; fix them and check again.`}</p>
      ${report.listed.length ? html`<div class="table-scroll"><table class="data compact-table">
        <caption class="visually-hidden">Problems by line</caption>
        <thead><tr><th scope="col" class="num">Line</th><th scope="col">Column</th>
          <th scope="col">Problem</th></tr></thead>
        <tbody>${report.listed.map((p, i) => html`<tr key=${i}>
          <td class="num">${p.line}</td><td><code>${p.field}</code></td><td>${p.rule}</td></tr>`)}</tbody>
      </table></div>` : null}
    </div>`;
  }
  return html`<div class="import-report import-ready">
    <p><${Icon} name="check" /> <b>${plural(report.rows, "row")} ready</b> —
      ${" "}${count(report.new)} new, ${count(report.updated)} replacing an earlier import.</p>
    ${report.shadowed_by_declared.length
      ? html`<p class="muted">Lines ${report.shadowed_by_declared.join(", ")} name a trap an
          operator has named; that name keeps winning.</p>` : null}
    <button type="button" class="primary" disabled=${busy} onClick=${onImport}
      >Import ${plural(report.rows, "row")}</button>
  </div>`;
}
