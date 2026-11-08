/* A list of device credentials (ADRs #457, #481): a light, the kind's mark, a name, where it works,
 * what it grants, whether agents may use it, when it was last checked.
 *
 * One table for the two places credentials are listed now: a customer's **Access** (its equipment,
 * `custaccess.js`) and the vault's **Not assigned** list (credentials stored before they belonged to
 * a customer, `views/secrets.js`). What this never shows is a secret: the rows are the server's
 * metadata, and opening one is `secretdetail.js`, behind a step-up.
 */

import { html, cx } from "../../dom.js";
import { relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";
import { StatusLight } from "./secretdetail.js";
import { ACCESS, KindMark } from "./vaultui.js";

const STATE_ORDER = { down: 0, degraded: 1, unknown: 2, up: 3 };

/** The rows, worst light first, then by name. */
export function sortCredentials(rows) {
  return [...rows].sort((a, b) =>
    (STATE_ORDER[a.status.state] - STATE_ORDER[b.status.state]) || a.name.localeCompare(b.name));
}

export function CredentialTable({ rows, selected, onOpen, showCustomer = false }) {
  return html`<div class="table-scroll list-view"><table class="data secrets-table">
    <thead><tr>
      <th scope="col">Status</th><th scope="col">Credential</th>
      ${showCustomer ? html`<th scope="col">Customer</th>` : null}
      <th scope="col">Where</th><th scope="col">Grants</th><th scope="col">Agents</th>
      <th scope="col" class="wide-only">Last check</th>
    </tr></thead>
    <tbody>${sortCredentials(rows).map((s) => html`<tr key=${s.id} class=${cx(selected === s.id && "row-selected")}>
      <td><${StatusLight} status=${s.status} /></td>
      <td><span class="secret-name"><${KindMark} kind=${s.kind} size=${28} />
        <span><button type="button" class="linkish secret-open" onClick=${() => onOpen(s.id)}
          aria-expanded=${selected === s.id ? "true" : "false"}>${s.name}</button>
        <span class="muted secret-kind">${s.kind_label}</span></span></span></td>
      ${showCustomer ? html`<td>${s.customer_name || html`<span class="muted">none</span>`}</td>` : null}
      <td class="mono">${s.host}:${s.port}</td>
      <td><span class=${`access-pill access-${s.access_level}`}><${Icon}
        name=${(ACCESS[s.access_level] || ACCESS.view_only).icon} /> ${s.access_label.split(" — ")[0]}</span></td>
      <td>${s.agent_access ? html`<span class="badge badge-warn">on</span>` : html`<span class="muted">off</span>`}</td>
      <td class="wide-only" title=${timeTitle(s.status.last_checked_at)}>${s.status.last_checked_at
        ? relative(s.status.last_checked_at) : "—"}</td>
    </tr>`)}</tbody>
  </table></div>`;
}
