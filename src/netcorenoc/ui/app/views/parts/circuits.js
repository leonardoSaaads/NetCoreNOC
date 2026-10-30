/* A customer's circuits, and one circuit's path from end to end (ADR #473).
 *
 * The list searches every field, attribute, hop and note — a port (`1/9/1`), an ODF position, a
 * provider's reference — as an agent's `noc_find_circuits` does. One circuit (`#/customers/{id}/
 * circuits/{circuit}`) draws its path as the signal runs it: from the A end, site by site, each
 * site's hops (client port, line port, ODF, cross-connect) with their coordinates, to the Z end.
 * Its notes are what the NOC learned about it (`note_circuits`, agents first); the circuit itself
 * is the customer's, changed by people (`edit_circuits`), one at a time or a whole sheet at once.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { can } from "../../session.js";
import { plural, relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";
import { CircuitForm, CircuitImport } from "./circuitedit.js";

const PAGE = 100;
const FACTS = [["bandwidth", "Bandwidth"], ["service", "Service"], ["owner", "Owner"],
  ["provider", "Front-ending party"], ["rfs_date", "Ready for service"], ["cease_date", "Ceased"]];

function Status({ status }) {
  if (!status) return html`<span class="muted">—</span>`;
  const tone = /active|in service|up/i.test(status) ? "ok" : /test|plan|pend/i.test(status) ? "warn" : "off";
  return html`<span class=${cx("ckt-status", `ckt-${tone}`)}>${status}</span>`;
}

/** The hops grouped by site, in the order the signal meets them. */
export function bySite(path) {
  const out = [];
  for (const hop of path || []) {
    const last = out[out.length - 1];
    if (last && last.site === hop.site) last.hops.push(hop);
    else out.push({ site: hop.site, hops: [hop] });
  }
  return out;
}

function Path({ circuit }) {
  const sites = bySite(circuit.path);
  if (!sites.length) return html`<p class="book-empty">No path recorded for this circuit.</p>`;
  return html`<ol class="ckt-path" aria-label="The circuit's path, from the A end to the Z end">
    <li class="ckt-end"><span class="ckt-end-mark">A</span><b>${circuit.a_end || "A end"}</b></li>
    ${sites.map((s, i) => html`<li class="ckt-site" key=${`${s.site}-${i}`}>
      <h4><${Icon} name="building" /> ${s.site || "Site"}</h4>
      <ul>${s.hops.map((h, j) => html`<li key=${j} class="ckt-hop">
        <span class="ckt-seg">${h.segment || "—"}</span>
        <span class="ckt-coord">${Object.entries(h.fields).map(([k, v]) => html`<span key=${k}><i>${k}</i> <b>${v}</b></span>`)}</span>
      </li>`)}</ul>
    </li>`)}
    <li class="ckt-end"><span class="ckt-end-mark">Z</span><b>${circuit.z_end || "Z end"}</b></li>
  </ol>`;
}

class CircuitDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { k: null, error: null, line: "", editing: false, removing: false, busy: false };
  }

  componentDidMount() { this.reload(); }

  async reload() {
    const { c, id } = this.props;
    try {
      this.setState({ k: await get(`/api/noc/customers/${c.id}/circuits/${id}`), error: null });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  may(action) { return can("cases.work") && (this.props.c.actions || []).includes(action); }

  async note(event) {
    event.preventDefault();
    const { c, id } = this.props;
    this.setState({ busy: true, error: null });
    try {
      const k = await post(`/api/noc/customers/${c.id}/circuits/${id}/notes`, { notes: this.state.line, mode: "append" });
      this.setState({ k, line: "", busy: false });
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  async remove() {
    const { c, id, onBack, onChanged } = this.props;
    this.setState({ busy: true });
    try {
      await post(`/api/noc/customers/${c.id}/circuits/${id}/delete`, {});
      onChanged();
      onBack();
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ c, onBack, onChanged }, { k, error, line, editing, removing, busy }) {
    const back = html`<button type="button" class="linkish" onClick=${onBack}>← ${c.name}'s circuits</button>`;
    if (!k) return html`<div>${back}${error ? html`<p class="err" role="alert">${error}</p>` : html`<p class="muted">Opening the circuit…</p>`}</div>`;
    if (editing) {
      return html`<div>${back}<${CircuitForm} c=${c} circuit=${k}
        onDone=${(saved) => { this.setState({ k: saved, editing: false }); onChanged(); }}
        onCancel=${() => this.setState({ editing: false })} /></div>`;
    }
    const attrs = Object.entries(k.attributes || {});
    return html`<article class="ckt-detail" aria-labelledby="ckt-heading">
      ${back}
      <header class="ckt-head">
        <span class="record-icon"><${Icon} name="circuit" /></span>
        <div class="record-title">
          <p class="record-kind">Circuit · ${k.source === "import" ? "from the customer's sheet" : "entered here"}</p>
          <h2 id="ckt-heading" class="mono">${k.designation}</h2>
          <p class="ckt-ends">${k.a_end || "?"} <span aria-hidden="true">⇄</span><span class="visually-hidden"> to </span> ${k.z_end || "?"}</p>
        </div>
        <${Status} status=${k.status} />
        ${this.may("edit_circuits") ? html`<span class="record-actions">
          <button type="button" onClick=${() => this.setState({ editing: true })}>Change</button>
          ${removing ? html`<span class="wb-confirm">Remove this circuit?${" "}
              <button type="button" class="danger" disabled=${busy} onClick=${() => this.remove()}>Remove</button>
              <button type="button" onClick=${() => this.setState({ removing: false })}>Keep</button></span>`
            : html`<button type="button" class="danger" onClick=${() => this.setState({ removing: true })}>Remove</button>`}
        </span>` : null}
      </header>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      <dl class="highlights">${FACTS.map(([name, label]) => html`<div key=${name}><dt>${label}</dt>
        <dd>${k[name] || html`<span class="muted">—</span>`}</dd></div>`)}</dl>
      <section class="panel-block" aria-label="Path">
        <h3 class="book-sub">Path <span class="muted">— ${plural(k.path.length, "hop")}</span></h3>
        <${Path} circuit=${k} />
      </section>
      <div class="ckt-lower">
        <section class="panel-block" aria-label="Notes">
          <h3 class="book-sub"><${Icon} name="sparkle" /> NOC notes</h3>
          ${k.notes ? html`<div class="book-text book-lines">${k.notes}</div>` : html`<p class="book-empty">No notes yet.</p>`}
          ${this.may("note_circuits") ? html`<form class="book-add" onSubmit=${(e) => this.note(e)}>
            <label for="ckt-note" class="visually-hidden">Add one line to the notes</label>
            <input id="ckt-note" maxlength="1000" value=${line} placeholder="What you found on this circuit"
              onInput=${(e) => this.setState({ line: e.currentTarget.value })} />
            <button type="submit" disabled=${busy || !line.trim()}>Add a note</button></form>` : null}
          ${k.notes_at ? html`<p class="book-meta muted">Last noted <span title=${timeTitle(k.notes_at)}>${relative(k.notes_at)}</span>${k.notes_by ? ` by ${k.notes_by}` : ""}.</p>` : null}
        </section>
        <section class="panel-block" aria-label="Everything else the sheet said">
          <h3 class="book-sub">Everything else the sheet said</h3>
          ${attrs.length ? html`<dl class="ckt-attrs">${attrs.map(([name, value]) => html`<div key=${name}>
            <dt>${name}</dt><dd>${value}</dd></div>`)}</dl>` : html`<p class="book-empty">Nothing more.</p>`}
        </section>
      </div>
    </article>`;
  }
}

export class Circuits extends Component {
  constructor(props) {
    super(props);
    this.state = { list: null, q: "", status: "", offset: 0, error: null, mode: null };
  }

  componentDidMount() { if (!this.props.circuitId) this.search(); }

  componentDidUpdate(previous) {
    if (previous.circuitId && !this.props.circuitId && !this.state.list) this.search();
  }

  may() { return can("cases.work") && (this.props.c.actions || []).includes("edit_circuits"); }

  async search(offset = 0) {
    const { q, status } = this.state;
    const query = new URLSearchParams({ q, status, limit: String(PAGE), offset: String(offset) });
    try {
      this.setState({ list: await get(`/api/noc/customers/${this.props.c.id}/circuits?${query}`), offset, error: null });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  open(id) { this.props.navigate(`#/customers/${this.props.c.id}/circuits/${id}`); }

  changed() { this.setState({ mode: null, list: null }); this.search(this.state.offset); this.props.onChanged(); }

  /** Remove one circuit from the list itself, after a confirmation on its own row. */
  async remove(k) {
    this.setState({ busy: true, error: null });
    try {
      await post(`/api/noc/customers/${this.props.c.id}/circuits/${k.id}/delete`, {});
      this.setState({ busy: false, removing: null });
      this.changed();
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ c, circuitId, navigate, onChanged }, { list, q, status, offset, error, mode, removing, busy }) {
    if (circuitId) {
      return html`<${CircuitDetail} key=${circuitId} c=${c} id=${circuitId} onChanged=${onChanged}
        onBack=${() => navigate(`#/customers/${c.id}/circuits`)} />`;
    }
    return html`<div class="circuits">
      <div class="part-bar">
        <form class="ckt-search" role="search" onSubmit=${(e) => { e.preventDefault(); this.search(); }}>
          <label for="ckt-q" class="visually-hidden">Find a circuit</label>
          <input id="ckt-q" type="search" maxlength="200" value=${q} placeholder="A circuit, a port (1/9/1), an ODF, a site, a reference…"
            onInput=${(e) => this.setState({ q: e.currentTarget.value })} />
          <label for="ckt-status" class="visually-hidden">Status</label>
          <select id="ckt-status" value=${status} onChange=${(e) => this.setState({ status: e.currentTarget.value }, () => this.search())}>
            <option value="">Any status</option>
            ${c.circuit_statuses.map((s) => html`<option key=${s} value=${s}>${s}</option>`)}</select>
          <button type="submit"><${Icon} name="search" /> Find</button>
        </form>
        ${this.may() && !mode ? html`<span class="record-actions">
          <button type="button" class="primary" onClick=${() => this.setState({ mode: "import" })}><${Icon} name="sheet" /> Import a sheet</button>
          <button type="button" onClick=${() => this.setState({ mode: "add" })}>Add a circuit</button></span>` : null}
      </div>
      ${mode === "import" ? html`<${CircuitImport} c=${c} onDone=${() => this.changed()} onCancel=${() => this.setState({ mode: null })} />` : null}
      ${mode === "add" ? html`<${CircuitForm} c=${c} onDone=${(k) => { this.changed(); this.open(k.id); }}
        onCancel=${() => this.setState({ mode: null })} />` : null}
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      ${!list ? html`<p class="muted">Reading the circuits…</p>` : list.total ? html`<div class="table-scroll">
        <table class="ckt-table">
          <caption class="visually-hidden">${c.name}'s circuits</caption>
          <thead><tr><th scope="col">Circuit</th><th scope="col">A end → Z end</th><th scope="col">Bandwidth</th>
            <th scope="col">Service</th><th scope="col">Status</th><th scope="col">Owner · party</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr></thead>
          <tbody>${list.circuits.map((k) => html`<tr key=${k.id}>
            <td><a class="mono" href=${`#/customers/${c.id}/circuits/${k.id}`}>${k.designation}</a>${k.has_notes
              ? html` <span class="ckt-noted" title="The NOC has notes on it"><${Icon} name="sparkle" /></span>` : null}</td>
            <td>${k.a_end} → ${k.z_end}</td><td>${k.bandwidth}</td><td>${k.service}</td>
            <td><${Status} status=${k.status} /></td>
            <td>${[k.owner, k.provider].filter(Boolean).join(" · ") || html`<span class="muted">—</span>`}</td>
            <td class="ckt-acts">${removing === k.id ? html`<span class="wb-confirm">Remove ${k.designation}?${" "}
                <button type="button" class="danger" disabled=${busy} onClick=${() => this.remove(k)}>Remove</button>
                <button type="button" onClick=${() => this.setState({ removing: null })}>Keep</button></span>`
              : html`<button type="button" class="linkish" onClick=${() => this.open(k.id)}>Open</button>${this.may()
                ? html`<button type="button" class="linkish danger-link" aria-label=${`Remove ${k.designation}`}
                    onClick=${() => this.setState({ removing: k.id })}>Remove</button>` : null}`}</td>
          </tr>`)}</tbody>
        </table></div>
        <p class="hint">${plural(list.total, "circuit")}${list.total > PAGE ? `, ${offset + 1}–${Math.min(offset + PAGE, list.total)} shown` : ""}.
          ${offset > 0 ? html`<button type="button" class="linkish" onClick=${() => this.search(offset - PAGE)}>Previous</button>` : null}
          ${offset + PAGE < list.total ? html`<button type="button" class="linkish" onClick=${() => this.search(offset + PAGE)}>Next</button>` : null}</p>`
        : html`<p class="book-empty">${q || status ? "No circuit matches." : "No circuits yet."}${this.may() && !q ? " Import the customer's sheet, or add one." : ""}</p>`}
    </div>`;
  }
}
