/* A customer's instructions and the NOC's memory of it, side by side (ADR #473).
 *
 * **Instructions** are the customer's words — how it wants the NOC to act, how a case is named,
 * who is called first — kept by people (`edit_instructions`); an agent reads them before it acts
 * and never rewrites them. **The memory** is what the NOC learned working that network, kept by
 * agents as they work: one dated line at a time, or condensed whole when it grows (`edit_memory`).
 * Both are shown as written — plain text, never markup — with who changed them last and when.
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";
import { can } from "../../session.js";
import { relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";

function Meta({ who, at, none }) {
  if (!at) return html`<p class="book-meta muted">${none}</p>`;
  return html`<p class="book-meta muted">Last changed <span title=${timeTitle(at)}>${relative(at)}</span>${who ? ` by ${who}` : ""}.</p>`;
}

export class BookText extends Component {
  constructor(props) {
    super(props);
    this.state = { editing: null, condensing: null, line: "", busy: false, error: null };
  }

  may(action) { return can("cases.work") && (this.props.c.actions || []).includes(action); }

  async send(path, body, reset) {
    this.setState({ busy: true, error: null });
    try {
      await post(path, body);
      this.setState({ busy: false, ...reset });
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  instructions(c, { editing, busy }) {
    const id = `cust-${c.id}-instructions`;
    return html`<section class="panel-block book-panel" aria-labelledby=${`${id}-h`}>
      <div class="book-head">
        <h3 id=${`${id}-h`}><${Icon} name="book" /> Instructions</h3>
        ${this.may("edit_instructions") && editing === null
          ? html`<button type="button" onClick=${() => this.setState({ editing: c.instructions })}>Edit</button>` : null}
      </div>
      <p class="hint">How ${c.name} wants the NOC to act: how to name its cases, who to call first, what never to touch. Kept by people; every agent follows it.</p>
      ${editing !== null ? html`<div class="book-edit">
        <label for=${id} class="visually-hidden">Instructions for ${c.name}</label>
        <textarea id=${id} rows="16" maxlength="65536" value=${editing}
          onInput=${(e) => this.setState({ editing: e.currentTarget.value })}></textarea>
        <div class="stepup-acts">
          <button type="button" class="primary" disabled=${busy}
            onClick=${() => this.send(`/api/noc/customers/${c.id}`, { instructions: editing, version: c.version },
              { editing: null })}>${busy ? "Saving…" : "Save the instructions"}</button>
          <button type="button" onClick=${() => this.setState({ editing: null, error: null })}>Cancel</button>
        </div>
      </div>` : c.instructions
        ? html`<div class="book-text">${c.instructions}</div>`
        : html`<p class="book-empty">No instructions yet.${this.may("edit_instructions") ? " Write how the customer wants to be served." : ""}</p>`}
      <${Meta} who=${c.updated_by} at=${c.instructions ? c.updated_at : null} none="Never written." />
    </section>`;
  }

  memory(c, { condensing, line, busy }) {
    const id = `cust-${c.id}-memory`;
    const path = `/api/noc/customers/${c.id}/memory`;
    return html`<section class="panel-block book-panel book-memory" aria-labelledby=${`${id}-h`}>
      <div class="book-head">
        <h3 id=${`${id}-h`}><${Icon} name="sparkle" /> NOC memory</h3>
        ${this.may("edit_memory") && condensing === null && c.memory
          ? html`<button type="button" onClick=${() => this.setState({ condensing: c.memory })}>Condense</button>` : null}
      </div>
      <p class="hint">What the NOC learned working this network — a recurring fault, a provider's habit, a workaround. Agents keep it current as they work.</p>
      ${condensing !== null ? html`<div class="book-edit">
        <label for=${id} class="visually-hidden">The memory, whole</label>
        <textarea id=${id} rows="12" maxlength="32768" value=${condensing}
          onInput=${(e) => this.setState({ condensing: e.currentTarget.value })}></textarea>
        <div class="stepup-acts">
          <button type="button" class="primary" disabled=${busy}
            onClick=${() => this.send(path, { memory: condensing, mode: "replace", version: c.memory_version },
              { condensing: null })}>${busy ? "Saving…" : "Save the memory"}</button>
          <button type="button" onClick=${() => this.setState({ condensing: null, error: null })}>Cancel</button>
        </div>
      </div>` : c.memory
        ? html`<div class="book-text book-lines">${c.memory}</div>`
        : html`<p class="book-empty">Nothing learned yet. Agents add to it as they work this customer's cases.</p>`}
      ${this.may("edit_memory") && condensing === null ? html`<form class="book-add"
          onSubmit=${(e) => { e.preventDefault(); this.send(path, { memory: line, mode: "append" }, { line: "" }); }}>
        <label for=${`${id}-line`} class="visually-hidden">Add one line to the memory</label>
        <input id=${`${id}-line`} maxlength="1000" value=${line} placeholder="One thing the next engineer should know"
          onInput=${(e) => this.setState({ line: e.currentTarget.value })} />
        <button type="submit" disabled=${busy || !line.trim()}>Add to the memory</button>
      </form>` : null}
      <${Meta} who=${c.memory_by} at=${c.memory_at} none="Never added to." />
    </section>`;
  }

  render({ c }, state) {
    return html`<div class="book">
      ${state.error ? html`<p class="err" role="alert">${state.error}</p>` : null}
      <div class="book-grid">
        ${this.instructions(c, state)}
        ${this.memory(c, state)}
      </div>
      ${c.elements.length ? html`<section class="panel-block" aria-label="Its elements">
        <h3 class="book-sub">Its elements <span class="muted">— ${c.element_count}</span></h3>
        <ul class="chip-list">${c.elements.map((e) => html`<li key=${e.id}>
          <a class="chip-static" href=${`#/entities?ne=${e.id}`}>${e.label || e.ip}${e.label ? html` <span class="muted mono">${e.ip}</span>` : null}</a></li>`)}</ul>
      </section>` : null}
    </div>`;
  }
}
