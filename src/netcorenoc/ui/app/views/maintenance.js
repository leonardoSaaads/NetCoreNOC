/* Planned work: the upcoming list (D7) and the form that declares one (D8).
 *
 * ## The list is the screen; the form is a thing you open
 *
 * An operator comes here far more often to answer *"is anything scheduled tonight, and has
 * somebody confirmed the long one?"* than to create a window. So the list is what loads, and
 * `Schedule maintenance` opens the four cards over it.
 *
 * ## D7, exactly
 *
 * Next **5 / 10 / 20**, switchable. Each row: status chip, name, organization, target count, the
 * **time until it starts as a number**, and — for `Pending confirmation` — the confirm action
 * where an editor will look for it, which is on the row and not two clicks inside it.
 *
 * ## What a viewer sees here
 *
 * Every row, always. A window whose `visibility` is `editors` comes back **redacted** — an id, a
 * status and a time, and no name — and the row says so rather than being dropped. Prime directive
 * 4: a viewer who cannot see that planned work exists is a viewer who reads a quiet estate as a
 * healthy one.
 */

import { html } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, Empty, Badge, SectionHeading } from "../widgets.js";
import { count, plural } from "../format.js";
import { can } from "../session.js";
import { WindowForm } from "./parts/mwform.js";
import { humanise } from "./parts/mwdraft.js";

/* D7's three sizes. */
const SIZES = [5, 10, 20];

/* Status to the chip's tone. The five an operator acts on differently get their own; `ended` and
 * `cancelled` share `muted` because neither asks anything of anybody. */
const TONE = {
  pending_confirmation: "warn",
  scheduled: "info",
  active: "on",
  ended: "muted",
  cancelled: "muted",
  expired: "bad",
};

const LABEL = {
  pending_confirmation: "Pending confirmation",
  scheduled: "Scheduled",
  active: "Active",
  ended: "Ended",
  cancelled: "Cancelled",
  expired: "Expired",
};

export class Maintenance extends Loader {
  constructor(props) {
    super(props);
    this.what = "planned maintenance";
    this.loadingLabel = "Reading planned maintenance";
    this.state = { ...this.state, limit: 5, creating: false, busy: null };
  }

  async load() {
    return get(`/api/maintenance-windows?limit=${this.state.limit}`);
  }

  /* **Each operation names its own route, literally.**
   *
   * An earlier draft built the path from a verb — `` `/api/maintenance-windows/${wid}/${what}` ``
   * — and `tests/test_security_ui.py` was right to refuse it: a path the guard cannot resolve to
   * a declared route is exactly the write that would slip past it unchecked. Three call sites is
   * the price of a console whose every write is visible to the guard that checks them.
   */
  /* Named `confirmWindow` rather than `confirm`: `tests/test_security_ui.py` forbids a bare
   * `confirm(` anywhere in the console, because a browser `confirm()` is the twentieth copy of a
   * dialogue that `destructive.js` exists to be the only one of. A method with that name reads
   * like one at a glance, which is the whole point of the guard. */
  async confirmWindow(wid) {
    await this.act(wid, () => post(`/api/maintenance-windows/${wid}/confirm`));
  }

  async endNow(wid) {
    await this.act(wid, () => post(`/api/maintenance-windows/${wid}/end`));
  }

  async act(wid, send) {
    this.setState({ busy: wid });
    try {
      await send();
      await this.reload();
    } finally {
      this.setState({ busy: null });
    }
  }

  view(body) {
    const rows = body.windows || [];
    const editable = can("mw.write");
    if (this.state.creating) {
      return html`<div>
        <${SectionHeading} title="Schedule maintenance" />
        <${WindowForm}
          onSaved=${() => this.setState({ creating: false }, () => this.reload())}
        />
        <button type="button" class="link" onClick=${() => this.setState({ creating: false })}>
          Cancel
        </button>
      </div>`;
    }
    return html`<div>
      <div class="mw-head">
        ${editable
          ? html`<button
              type="button"
              class="primary"
              data-role="new-window"
              onClick=${() => this.setState({ creating: true })}
            >
              Schedule maintenance
            </button>`
          : null}
        <div class="mw-sizes" data-role="sizes">
          ${SIZES.map(
            (n) => html`<button
              type="button"
              key=${n}
              class=${this.state.limit === n ? "on" : ""}
              onClick=${() => this.setState({ limit: n }, () => this.reload())}
            >
              ${n}
            </button>`,
          )}
        </div>
      </div>
      ${rows.length === 0
        ? html`<${Empty}
            title="No maintenance is scheduled."
            will=${"A window appears here the moment anyone declares one — an operator from this " +
            "screen, or an agent through the API."}
            meanwhile=${"While a window is running, every device and every situation it covers " +
            "carries a marker, so a host that goes quiet is never mistaken for a healthy one."} />`
        : html`<ul class="mw-list" data-role="upcoming">
            ${rows.map((w) => this.row(w, body.now))}
          </ul>`}
      ${rows.length < body.total
        ? html`<p class="hint">Showing ${count(rows.length)} of ${count(body.total)}.</p>`
        : null}
    </div>`;
  }

  row(w, now) {
    const starting = w.starts_in_s > 0;
    const confirmable = w.status === "pending_confirmation" && can("mw.confirm");
    return html`<li class="mw-row" key=${w.id} data-window=${w.id} data-status=${w.status}>
      <${Badge} tone=${TONE[w.status] || "muted"}>${LABEL[w.status] || w.status}<//>
      <span class="mw-row-name">
        ${w.redacted
          ? html`<span class="muted" data-role="redacted"
              >Maintenance on ${plural(w.target_count, "host", "hosts")}</span
            >`
          : w.name}
        ${w.created_by_agent
          ? html`<${Badge} tone="info" title="Created through the API by a service token">agent<//>`
          : null}
      </span>
      ${w.redacted ? null : html`<span class="muted">${w.organization_name}</span>`}
      <span class="muted">${count(w.target_count)} ${plural(w.target_count, "host", "hosts")}</span>
      <span class="mw-row-when" data-role="countdown">
        ${starting
          ? `in ${humanise(w.starts_in_s)}`
          : w.ends_in_s > 0
            ? `${humanise(w.ends_in_s)} left`
            : "over"}
      </span>
      ${confirmable
        ? html`<button
            type="button"
            data-role="confirm"
            disabled=${this.state.busy === w.id}
            onClick=${() => this.confirmWindow(w.id)}
          >
            Confirm
          </button>`
        : null}
      ${w.status === "active" && can("mw.write")
        ? html`<button
            type="button"
            data-role="end-now"
            disabled=${this.state.busy === w.id}
            onClick=${() => this.endNow(w.id)}
          >
            End now
          </button>`
        : null}
    </li>`;
  }
}
