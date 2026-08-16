/* Quarantine: datagrams the parser refused.
 *
 * Reading this list is audited server-side, and the screen says so before the operator reads it
 * rather than after — a control whose existence is a surprise is a control that feels like a
 * trap.
 */

import { html } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty, DataTable, TimeCell, cell } from "../widgets.js";
import { count, plural } from "../format.js";

export class Quarantine extends Loader {
  constructor(props) {
    super(props);
    this.what = "the quarantine list";
    this.loadingLabel = "Reading quarantined datagrams";
  }

  async load() { return get("/api/quarantine?limit=100"); }

  view(rows) {
    if (!rows.length) {
      return html`<${Empty}
        title="Nothing has been quarantined."
        will=${"A datagram lands here when the parser refuses it — a malformed PDU, an " +
               "unsupported version, a source outside the allowlist. It is kept by hash and " +
               "length, never re-parsed, and never becomes an alarm."}
        meanwhile=${"An empty quarantine and a quiet timeline together mean nothing is arriving " +
                    "at all; check the trap destination on your devices and the allowlist under " +
                    "Settings."} />`;
    }
    const columns = [
      { key: "when", label: "received" },
      { key: "source", label: "source" },
      { key: "reason", label: "reason" },
      { key: "sha256", label: "sha-256" },
      { key: "length", label: "bytes", numeric: true },
    ];
    const table = rows.map((q) => ({
      key: q.sha256 + q.received_at,
      cells: {
        when: cell(html`<${TimeCell} ts=${q.received_at} />`),
        source: q.source,
        reason: q.reason,
        sha256: html`<code class="mono">${q.sha256}</code>`,
        length: count(q.length),
      },
    }));
    return html`<div>
      <p class="warnbox">Reading this list is recorded in the audit log, attributed to you. The
        datagrams are stored by hash and length only; their contents are not replayed here.</p>
      <p class="hint">${plural(rows.length, "refused datagram")}, newest first.</p>
      <${DataTable} columns=${columns} rows=${table} />
    </div>`;
  }
}
