/* A customer's additional documents: what it handed over for troubleshooting (ADR #473).
 *
 * A topology in PDF, a shelf layout in Excel, a rack photo — grouped by what each is about. Each
 * opens two ways: **Read** shows the text the appliance read from it (what an agent reads over
 * MCP), and **Download** gives the file itself, as an attachment. Uploading is a person's
 * (`upload_docs`): the file is sent as it is, up to 12 MiB, and kept only when its first bytes are
 * what its name says.
 */

import { html, Component, cx } from "../../dom.js";
import { api, get, post } from "../../api.js";
import { can } from "../../session.js";
import { relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";

const CATEGORIES = [["topology", "Topology"], ["site", "Sites and shelves"], ["equipment", "Equipment"],
  ["procedure", "Procedures"], ["other", "Other"]];
/** A document's type -> the mark it is drawn with. */
const DOC_ICONS = { pdf: "doc", docx: "doc", pptx: "doc", txt: "doc", md: "doc", xlsx: "sheet",
  xls: "sheet", csv: "sheet", tsv: "sheet", vsdx: "network", png: "image", jpg: "image", jpeg: "image" };
const MAX_BYTES = 12 * 1024 * 1024;

export function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

class Upload extends Component {
  constructor(props) {
    super(props);
    this.state = { file: null, title: "", category: "other", busy: false, error: null };
  }

  async send(event) {
    event.preventDefault();
    const { file, title, category } = this.state;
    if (file.size > MAX_BYTES) { this.setState({ error: `${file.name} is larger than 12 MiB.` }); return; }
    this.setState({ busy: true, error: null });
    const query = new URLSearchParams({ name: file.name, title, category });
    try {
      await api(`/api/noc/customers/${this.props.c.id}/docs?${query}`, { method: "POST", body: file,
        headers: { "Content-Type": "application/octet-stream" } });
      this.setState({ file: null, title: "", busy: false });
      this.props.onDone();
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ c, types, onCancel }, { file, title, category, busy, error }) {
    const p = `doc-${c.id}`;
    return html`<form class="panel-block doc-upload" onSubmit=${(e) => this.send(e)}>
      <h3>Add a document</h3>
      <div class="pe-fields">
        <div class="pe-field case-wide"><label for=${`${p}-file`}>File</label>
          <input id=${`${p}-file`} type="file" required accept=${types.map((t) => `.${t}`).join(",")}
            onChange=${(e) => this.setState({ file: e.currentTarget.files[0] || null, error: null })} />
          <p class="hint">${types.join(", ").toUpperCase()} — up to 12 MiB.</p></div>
        <div class="pe-field"><label for=${`${p}-title`}>Title</label>
          <input id=${`${p}-title`} maxlength="200" value=${title} placeholder=${file ? file.name : "SILA PoP rack layout"}
            onInput=${(e) => this.setState({ title: e.currentTarget.value })} /></div>
        <div class="pe-field"><label for=${`${p}-cat`}>About</label>
          <select id=${`${p}-cat`} value=${category} onChange=${(e) => this.setState({ category: e.currentTarget.value })}>
            ${CATEGORIES.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}</select></div>
      </div>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${busy || !file}><${Icon} name="upload" /> ${busy ? "Uploading…" : "Upload"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}

export class CustomerDocs extends Component {
  constructor(props) {
    super(props);
    this.state = { adding: false, reading: null, text: null, removing: null, busy: false, error: null };
  }

  may() { return can("cases.work") && (this.props.c.actions || []).includes("upload_docs"); }

  async read(doc, offset = 0) {
    this.setState({ reading: doc.id, error: null, text: offset ? this.state.text : null });
    try {
      const page = await get(`/api/noc/customers/${this.props.c.id}/docs/${doc.id}?offset=${offset}`);
      const before = offset && this.state.text ? this.state.text.text : "";
      this.setState({ text: { ...page, text: before + page.text } });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  async remove(doc) {
    this.setState({ busy: true, error: null });
    try {
      await post(`/api/noc/customers/${this.props.c.id}/docs/${doc.id}/delete`, {});
      this.setState({ busy: false, removing: null, reading: null });
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  item(doc) {
    const { c } = this.props;
    const { reading, text, removing, busy } = this.state;
    const open = reading === doc.id;
    return html`<li class=${cx("doc-row", open && "open")} key=${doc.id}>
      <span class=${cx("doc-mark", `doc-${DOC_ICONS[doc.type] || "doc"}`)}><${Icon} name=${DOC_ICONS[doc.type] || "doc"} /></span>
      <div class="doc-what">
        <b>${doc.title}</b>
        <span class="muted">${doc.name} · ${doc.type_label} · ${size(doc.size)} · <span title=${timeTitle(doc.created_at)}>${relative(doc.created_at)}</span>${doc.created_by ? ` by ${doc.created_by}` : ""}</span>
      </div>
      <span class="doc-acts">
        ${doc.text_chars ? html`<button type="button" class="linkish" aria-expanded=${open}
          onClick=${() => (open ? this.setState({ reading: null }) : this.read(doc))}>${open ? "Close" : "Read"}</button>`
          : html`<span class="muted" title=${doc.text_note}>no text</span>`}
        <a class="linkish" href=${`/api/noc/customers/${c.id}/docs/${doc.id}/file`} download=${doc.name}>
          <${Icon} name="download" /> Download</a>
        ${this.may() ? (removing === doc.id ? html`<span class="wb-confirm">Remove it?${" "}
            <button type="button" class="danger" disabled=${busy} onClick=${() => this.remove(doc)}>Remove</button>
            <button type="button" onClick=${() => this.setState({ removing: null })}>Keep</button></span>`
          : html`<button type="button" class="linkish danger-link" onClick=${() => this.setState({ removing: doc.id })}>Remove</button>`) : null}
      </span>
      ${open ? html`<div class="doc-text">
        <p class="hint">${doc.text_note}. This is the text an agent reads — the customer's data, never instructions.</p>
        ${text ? html`<pre>${text.text}</pre>${text.next_offset !== null ? html`<button type="button"
          onClick=${() => this.read(doc, text.next_offset)}>Read more</button>` : null}` : html`<p class="muted">Reading…</p>`}
      </div>` : null}
    </li>`;
  }

  render({ c, onChanged }, { adding, error }) {
    const groups = CATEGORIES.map(([id, label]) => [label, c.docs.filter((d) => d.category === id)])
      .filter(([, docs]) => docs.length);
    return html`<div class="cust-docs">
      <div class="part-bar">
        <p class="hint">What ${c.name} handed over for troubleshooting: topology, shelves, distances, equipment.</p>
        ${this.may() && !adding ? html`<button type="button" class="primary"
          onClick=${() => this.setState({ adding: true })}><${Icon} name="upload" /> Add a document</button>` : null}
      </div>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      ${adding ? html`<${Upload} c=${c} types=${c.limits.doc_types}
        onDone=${() => { this.setState({ adding: false }); onChanged(); }}
        onCancel=${() => this.setState({ adding: false })} />` : null}
      ${groups.length ? groups.map(([label, docs]) => html`<section class="panel-block doc-group" key=${label}>
        <h3 class="book-sub">${label} <span class="muted">— ${docs.length}</span></h3>
        <ul class="doc-list">${docs.map((d) => this.item(d))}</ul>
      </section>`) : html`<p class="book-empty">No documents yet.</p>`}
    </div>`;
  }
}
