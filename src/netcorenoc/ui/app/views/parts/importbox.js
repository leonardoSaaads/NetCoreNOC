/* Import a trap list — **class, trap OID, vendor, severity** — from a CSV or text file
 * (v0.22.0, item 17, #385).
 *
 * Two steps, because the server's rule is all-or-nothing: **Check** sends the file as a dry run and
 * shows every problem by line, field and rule; **Import** is offered only when nothing is wrong.
 * An imported row names and grades a trap; it is not evidence, and a colleague's declaration still
 * wins over it — the check lists the rows that will be shadowed.
 */

import { html, Component } from "../../dom.js";
import { post, del } from "../../api.js";
import { count, plural } from "../../format.js";
import { InfoTip } from "../../info.js";

/** The server's cap, mirrored so a too-large file is refused before it is sent. */
const MAX_BYTES = 256 * 1024;

export class ImportBox extends Component {
  constructor(props) {
    super(props);
    this.state = { file: null, text: null, report: null, busy: false, error: null, done: null,
                   withdrawing: false };
  }

  pick(event) {
    const file = event.target.files && event.target.files[0];
    this.setState({ file: null, text: null, report: null, error: null, done: null });
    if (!file) return;
    if (file.size > MAX_BYTES) {
      this.setState({ error: { message: `${file.name} is larger than 256 KiB.` } });
      return;
    }
    const reader = new globalThis.FileReader();
    reader.onload = () => this.check(file.name, String(reader.result));
    reader.onerror = () => this.setState({ error: { message: `Could not read ${file.name}.` } });
    reader.readAsText(file);
  }

  async check(filename, text) {
    this.setState({ busy: true, file: filename, text });
    try {
      const report = await post("/api/catalogue/import?dry_run=true", { filename, text });
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
      this.setState({ busy: false, report: null, done: report, file: null, text: null });
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

  render({ imported }, { report, busy, error, done, withdrawing }) {
    return html`<section class="panel-block importbox">
      <div class="section-heading">
        <h3>Import a trap list</h3>
        <${InfoTip} label="The file format">
          A CSV or text file whose first line names the columns: class, trap_oid, vendor,
          severity — separated by commas, semicolons or tabs. Each row needs a trap OID and a class
          or a severity (critical, major, minor, warning, indeterminate). Up to 5 000 rows and
          256 KiB. A file with any problem imports nothing; an operator's own declaration still
          wins over an imported row.
        <//>
      </div>
      <label class="import-pick">
        <input type="file" accept=".csv,.txt,text/csv,text/plain" onChange=${(e) => this.pick(e)} />
      </label>
      ${busy ? html`<p class="muted">Checking…</p>` : null}
      ${error ? html`<p class="err" role="alert">${error.message}</p>` : null}
      ${report ? html`<${Report} report=${report} busy=${busy} onImport=${() => this.commit()} />`
        : null}
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
      <p><b>${report.filename}: nothing will be imported.</b>${" "}${report.refused
        ?? `${plural(report.problems, "problem")} in ${plural(report.rows + report.problems,
          "row")}; fix them and check again.`}</p>
      ${report.listed.length ? html`<div class="table-scroll"><table class="data">
        <caption class="visually-hidden">Problems by line</caption>
        <thead><tr><th scope="col" class="num">line</th><th scope="col">field</th>
          <th scope="col">problem</th></tr></thead>
        <tbody>${report.listed.map((p, i) => html`<tr key=${i}>
          <td class="num">${p.line}</td><td>${p.field}</td><td>${p.rule}</td></tr>`)}</tbody>
      </table></div>` : null}
    </div>`;
  }
  return html`<div class="import-report">
    <p><b>${report.filename}</b>: ${plural(report.rows, "row")} ready —
      ${" "}${count(report.new)} new, ${count(report.updated)} replacing an earlier import.</p>
    ${report.shadowed_by_declared.length
      ? html`<p class="muted">Lines ${report.shadowed_by_declared.join(", ")} name an OID an
          operator has declared; the declaration keeps winning.</p>` : null}
    <button type="button" class="primary" disabled=${busy} onClick=${onImport}
      >Import ${plural(report.rows, "row")}</button>
  </div>`;
}
