/* Learned alarm classes — a screen for a route that had none (draft §4).
 *
 * `GET /api/classes` is `viewer+` and was unreachable from any screen in v0.12.0. It is the
 * clearest demonstration the product has of its own central claim: this table was empty when the
 * appliance was installed, nobody loaded a MIB, and every row in it arrived by inference from
 * the trap stream.
 */

import { html } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty, DataTable } from "../widgets.js";
import { count, plural } from "../format.js";

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
    const columns = [
      { key: "id", label: "#", numeric: true },
      { key: "name", label: "class", title: "the operator label if one was set, else what was inferred" },
      { key: "oid", label: "trap OID" },
      { key: "vendor", label: "vendor", title: "inferred from the enterprise arc of the OID" },
    ];
    const rows = classes.map((c) => ({
      key: c.id,
      cells: {
        id: count(c.id),
        name: c.label
          ? html`<span>${c.label} <span class="muted">(labelled)</span></span>`
          : (c.name || "—"),
        oid: html`<code class="mono">${c.oid}</code>`,
        vendor: c.vendor || "unknown",
      },
    }));
    return html`<div>
      <p class="hint">${plural(classes.length, "class", "classes")}, every one of them inferred.
        A label you set here is cosmetic: it renames the class on screen and changes nothing about
        how it correlates.</p>
      <${DataTable} columns=${columns} rows=${rows} />
    </div>`;
  }
}
