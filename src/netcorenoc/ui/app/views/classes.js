/* Learned alarm classes — a screen for a route that had none (draft §4).
 *
 * `GET /api/classes` is `viewer+` and was unreachable from any screen in v0.12.0. It is the
 * clearest demonstration the product has of its own central claim: this table was empty when the
 * appliance was installed, nobody loaded a MIB, and every row in it arrived by inference from
 * the trap stream.
 *
 * ## v0.16.1 — what an operator does here, asked and answered
 *
 * Decision 4 of this release put the screen's existence in question: *"a list with no
 * statistics"*. It survives, and the reason is not that it exists. Two things an operator does
 * here and nowhere else:
 *
 * 1. **Read the OID → class mapping** while triaging a trap kind they have not seen before. The
 *    situation card names a class; only this screen says which OID it came from and which vendor
 *    arc that OID belongs to.
 * 2. **Name a class** — and until this release they could not. The screen's own text has said
 *    since v0.13.0 that *"a label you set here is cosmetic"*, and there was **no control to set
 *    one**: `POST /api/labels {kind: "class"}` was reachable from no screen in the console.
 *    A screen that describes an action it does not offer is worse than a screen that offers
 *    nothing, and this is the whole of what it needed.
 *
 * The name is not a new gesture and needs no new capability: it is `label.write`, the same
 * capability that renames a device on the graph and names a situation (DECISIONS #260). It changes
 * nothing about correlation, which is what the caption has always said and what
 * `PREREGISTRATION-0.16.0.md` §2's map requires — a class label is not an assertion about a
 * grouping and produces no training row.
 *
 * **No statistics were added.** Part VII rule 4: a metric on a screen nobody acts on is the same
 * waste as a paragraph nobody reads. Nothing here serves a per-class alarm count today, and the
 * honest options were to invent a route for a number nobody named a question for, or to leave it.
 */

import { html, Component } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, Empty, DataTable, SeverityBadge } from "../widgets.js";
import { count, plural } from "../format.js";
import { can } from "../session.js";

export class Classes extends Loader {
  constructor(props) {
    super(props);
    this.what = "the learned alarm classes";
    this.loadingLabel = "Reading learned alarm classes";
  }

  async load() { return get("/api/classes"); }

  view(classes) {
    if (!classes.length) {
      return html`<${Empty}
        title="No alarm classes learned yet."
        will=${"A class appears the first time a trap of that kind arrives. The appliance groups " +
               "traps into classes by their OID and varbind shape — there is no MIB to load and " +
               "no vendor list to maintain."}
        meanwhile=${"Send one trap from any device. It becomes a class immediately, before any " +
                    "correlation happens."} />`;
    }
    const editable = can("label.write");
    const columns = [
      { key: "id", label: "#", numeric: true },
      { key: "name", label: "class", title: "the operator label if one was set, else what was inferred" },
      { key: "oid", label: "trap OID" },
      { key: "vendor", label: "vendor", title: "inferred from the enterprise arc of the OID" },
      { key: "severity", label: "severity",
        title: "the severity an operator declared for this kind of trap" },
    ];
    if (editable) columns.push({ key: "act", label: "" });
    const rows = classes.map((c) => ({
      key: c.id,
      cells: {
        id: count(c.id),
        // **Which value is in use, on the row** (DECISIONS #284). A declared name wins and the
        // derived one is still there; saying so is what makes the declaration revertible in the
        // operator's head as well as in the database.
        name: c.label
          ? html`<span>${c.label} <span class="muted">(declared)</span></span>`
          : (c.name || "—"),
        oid: html`<code class="mono">${c.oid}</code>`,
        vendor: c.vendor || "unknown",
        severity: c.severity
          ? html`<${SeverityBadge} alarm=${{ severity: null, severity_rank: null,
                                             declared_severity: c.severity,
                                             declared_severity_rank: c.severity_rank }} />`
          : html`<span class="muted">not declared</span>`,
        act: editable
          ? html`<${ClassName} klass=${c} onDone=${() => this.reload()} />`
          : null,
      },
    }));
    return html`<div>
      <p class="hint">${plural(classes.length, "class", "classes")}, every one of them inferred.
        ${editable
          ? " A name you set here is cosmetic: it renames the class on every screen and changes " +
            "nothing about how it correlates."
          : " A class can be given a name by an operator who holds label.write; yours does not."}
      </p>
      <${DataTable} columns=${columns} rows=${rows} />
    </div>`;
  }
}

/**
 * Name one class. **The control the caption has promised since v0.13.0.**
 *
 * Deliberately not a `Destructive`: naming is reversible by naming again, and wrapping every write
 * in a confirmation would make the confirmation mean nothing where it matters. It reports the
 * server's own `detail` on refusal, like every other write in this console — a 403 here means the
 * capability was revoked between the render and the click, and saying "failed" would hide that.
 */
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
      ${error ? html`<span class="err" role="alert"
        >${error.detail || error.message}</span>` : null}
    </form>`;
  }
}
