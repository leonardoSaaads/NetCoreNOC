/* Writing a customer's circuits: a whole sheet at once, or one circuit by hand (ADR #473).
 *
 * **Import** takes the customer's own sheet — pasted from Excel as it is, or an Excel, CSV or TSV
 * document already in its Additional docs — with its header rows of sites and segments over the
 * Shelf/Slot/Port columns. Two steps, as every import in this console: **Preview** says what the
 * sheet holds and changes nothing; **Store** keeps it, replacing a circuit of the same name and
 * keeping the notes the NOC had on it.
 *
 * **One circuit** is its fields and its path, a hop per line: where, what, and its coordinates
 * as `name: value` pairs (`Shelf: 1; Slot: 9; Port: 1; Mode: 10G`).
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";
import { plural } from "../../format.js";
import { Icon } from "../../icons.js";

const SHEETS = new Set(["xlsx", "csv", "tsv", "txt"]);

export class CircuitImport extends Component {
  constructor(props) {
    super(props);
    this.state = { text: "", doc: "", sheet: 0, report: null, busy: false, error: null };
  }

  body(apply) {
    const { text, doc, sheet } = this.state;
    return doc ? { doc_id: Number(doc), sheet: Number(sheet), apply } : { text, apply };
  }

  async run(apply) {
    this.setState({ busy: true, error: null });
    try {
      const report = await post(`/api/noc/customers/${this.props.c.id}/circuits/import`, this.body(apply));
      this.setState({ busy: false, report });
      if (apply) this.props.onDone(report);
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ c, onCancel }, { text, doc, sheet, report, busy, error }) {
    const docs = c.docs.filter((d) => SHEETS.has(d.type));
    const ready = doc || text.trim();
    return html`<section class="panel-block ckt-import" aria-labelledby="ckt-import-h">
      <h3 id="ckt-import-h"><${Icon} name="sheet" /> Import the customer's sheet</h3>
      <p class="hint">Paste it straight from Excel — its header rows of sites and segments and all — or read one of its documents. Nothing is stored until you say so.</p>
      ${docs.length ? html`<div class="pe-fields">
        <div class="pe-field"><label for="ckt-doc">From a document</label>
          <select id="ckt-doc" value=${doc} onChange=${(e) => this.setState({ doc: e.currentTarget.value, report: null })}>
            <option value="">— paste it below instead —</option>
            ${docs.map((d) => html`<option key=${d.id} value=${String(d.id)}>${d.title} (${d.type})</option>`)}</select></div>
        ${doc && docs.find((d) => String(d.id) === doc).type === "xlsx" ? html`<div class="pe-field">
          <label for="ckt-sheet">Sheet</label>
          <input id="ckt-sheet" type="number" min="1" max="200" value=${Number(sheet) + 1}
            onInput=${(e) => this.setState({ sheet: Math.max(0, Number(e.currentTarget.value) - 1), report: null })} /></div>` : null}
      </div>` : null}
      ${doc ? null : html`<label for="ckt-paste" class="visually-hidden">The sheet, pasted</label>
        <textarea id="ckt-paste" class="ckt-paste" rows="8" maxlength="900000" value=${text}
          placeholder="Paste the rows here, header rows included: Sr. · Circuit Designation · … · Shelf · Slot · Port · Mode"
          onInput=${(e) => this.setState({ text: e.currentTarget.value, report: null })}></textarea>`}
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      ${report ? html`<div class="ckt-report" role="status">
        <p><b>${plural(report.found, "circuit")}</b> in the sheet — ${report.new} new, ${report.replaced} replacing one of the same name.${report.applied ? " Stored." : ""}</p>
        <div class="table-scroll"><table class="ckt-table">
          <caption class="visually-hidden">The first circuits the sheet holds</caption>
          <thead><tr><th scope="col">Circuit</th><th scope="col">A → Z</th><th scope="col">Bandwidth</th>
            <th scope="col">Status</th><th scope="col">First hops</th></tr></thead>
          <tbody>${report.preview.map((k) => html`<tr key=${k.designation}><td class="mono">${k.designation}</td>
            <td>${k.a_end} → ${k.z_end}</td><td>${k.bandwidth}</td><td>${k.status}</td>
            <td class="ckt-first">${k.path.slice(0, 2).join(" › ")}${k.path.length > 2 ? ` › … ${k.path.length - 2} more` : ""}</td></tr>`)}</tbody>
        </table></div>
      </div>` : null}
      <div class="stepup-acts">
        <button type="button" disabled=${busy || !ready} onClick=${() => this.run(false)}>${busy && !report ? "Reading…" : "Preview"}</button>
        ${report && !report.applied ? html`<button type="button" class="primary" disabled=${busy}
          onClick=${() => this.run(true)}>${busy ? "Storing…" : `Store ${plural(report.found, "circuit")}`}</button>` : null}
        <button type="button" onClick=${onCancel}>Close</button>
      </div>
    </section>`;
  }
}

const FIELDS = [["designation", "Circuit", 200], ["a_end", "A end", 120], ["z_end", "Z end", 120],
  ["bandwidth", "Bandwidth", 40], ["service", "Service", 80], ["status", "Status", 40],
  ["owner", "Owner", 120], ["provider", "Front-ending party", 120], ["rfs_date", "Ready for service", 40],
  ["cease_date", "Ceased", 40]];

/** `SILA PoP | Client Port | Shelf: 1; Slot: 9` <-> a hop: a name before its colon, pairs apart by
 * semicolons — so an ODF's `1 Tx & 2Rx` survives the round trip. */
function hopLine(h) {
  return [h.site, h.segment, Object.entries(h.fields).map(([k, v]) => `${k}: ${v}`).join("; ")].join(" | ");
}
function lineHop(line) {
  const [site = "", segment = "", coords = ""] = line.split("|").map((s) => s.trim());
  const fields = {};
  for (const pair of coords.split(";").map((s) => s.trim()).filter(Boolean)) {
    const at = pair.indexOf(":");
    if (at > 0) fields[pair.slice(0, at).trim()] = pair.slice(at + 1).trim();
    else fields[pair] = pair;
  }
  return { site, segment, fields };
}

export class CircuitForm extends Component {
  constructor(props) {
    super(props);
    const k = props.circuit || {};
    const f = {};
    for (const [name] of FIELDS) f[name] = k[name] || "";
    this.state = { f, path: (k.path || []).map(hopLine).join("\n"), busy: false, error: null };
  }

  async save(event) {
    event.preventDefault();
    const { c, circuit } = this.props;
    const path = this.state.path.split("\n").map((l) => l.trim()).filter(Boolean).map(lineHop);
    const body = { ...this.state.f, path, attributes: (circuit && circuit.attributes) || {},
      notes: (circuit && circuit.notes) || "" };
    this.setState({ busy: true, error: null });
    try {
      const saved = circuit
        ? await post(`/api/noc/customers/${c.id}/circuits/${circuit.id}`, { ...body, version: circuit.version })
        : await post(`/api/noc/customers/${c.id}/circuits`, body);
      this.props.onDone(saved);
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ circuit, onCancel }, { f, path, busy, error }) {
    return html`<form class="panel-block ckt-form" onSubmit=${(e) => this.save(e)}>
      <h3>${circuit ? `Change ${circuit.designation}` : "Add a circuit"}</h3>
      <div class="pe-fields">${FIELDS.map(([name, label, max]) => html`<div class="pe-field" key=${name}>
        <label for=${`ckt-f-${name}`}>${label}</label>
        <input id=${`ckt-f-${name}`} maxlength=${max} required=${name === "designation"} value=${f[name]}
          onInput=${(e) => this.setState({ f: { ...f, [name]: e.currentTarget.value } })} /></div>`)}
        <div class="pe-field case-wide"><label for="ckt-f-path">Path — one hop per line: site | segment | coordinates</label>
          <textarea id="ckt-f-path" rows="8" value=${path} placeholder="SILA PoP | Client Port | Shelf: 1; Slot: 9; Port: 1; Mode: 10G"
            onInput=${(e) => this.setState({ path: e.currentTarget.value })}></textarea></div>
      </div>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${busy}>${busy ? "Saving…" : circuit ? "Save the circuit" : "Add the circuit"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}
