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
import { WindowDetail } from "./parts/mwdetail.js";

/* D7's three sizes. */
const SIZES = [5, 10, 20];

const REFRESH_MS = 30000;

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
    this.state = { ...this.state, limit: 5, creating: false, busy: null, open: null, editing: null,
                   failed: null };
  }

  async load() {
    return get(`/api/maintenance-windows?limit=${this.state.limit}`);
  }

  /* **Each operation names its own route, literally.**
   *
   * An earlier draft built the path from a verb — `` `/api/maintenance-windows/${wid}/${what}` ``
   * — and `tests/ui/test_security_ui.py` was right to refuse it: a path the guard cannot resolve to
   * a declared route is exactly the write that would slip past it unchecked. Three call sites is
   * the price of a console whose every write is visible to the guard that checks them.
   */
  /* Named `confirmWindow` rather than `confirm`: `tests/ui/test_security_ui.py` forbids a bare
   * `confirm(` anywhere in the console, because a browser `confirm()` is the twentieth copy of a
   * dialogue that `destructive.js` exists to be the only one of. A method with that name reads
   * like one at a glance, which is the whole point of the guard. */
  async confirmWindow(wid) {
    await this.act(wid, () => post(`/api/maintenance-windows/${wid}/confirm`));
  }

  async endNow(wid) {
    await this.act(wid, () => post(`/api/maintenance-windows/${wid}/end`));
  }

  /* v0.29.0: a refusal is said on the row it came from, and the list is read again either way —
   * a Confirm on a window the sweep had already expired used to do nothing at all: the 409 was
   * thrown past this method, the list was never re-read, and the stale row kept its button. */
  async act(wid, send) {
    this.setState({ busy: wid, failed: null });
    try {
      await send();
    } catch (error) {
      this.setState({ failed: { wid, message: error.message || String(error) } });
    } finally {
      this.setState({ busy: null });
      await this.refresh();
    }
  }

  /* Read the list again without the loading state, so the rows do not blink out and back. */
  async refresh() {
    try {
      this.setState({ data: await this.load() });
    } catch { /* the rows stay as they were; the next tick reads again */ }
  }

  componentDidMount() {
    super.componentDidMount();
    // Countdowns and statuses move with the clock (a long window expires unconfirmed at its
    // start), so a list left open is read again every 30 s.
    this.timer = setInterval(() => {
      if (!this.state.creating && !this.state.editing) this.refresh();
    }, REFRESH_MS);
  }

  componentWillUnmount() { clearInterval(this.timer); }

  view(body) {
    const rows = body.windows || [];
    const editable = can("mw.write");
    if (this.state.editing) {
      // v0.22.0 (item 19): the same four cards, opened on what was scheduled.
      return html`<div>
        <div class="mw-formhead">
          <${SectionHeading} title=${`Edit — ${this.state.editing.name}`} />
          <button type="button" data-role="mw-cancel-form"
            onClick=${() => this.setState({ editing: null })}>Cancel</button>
        </div>
        <${WindowForm} initial=${this.state.editing}
          onSaved=${() => this.setState({ editing: null }, () => this.reload())} />
      </div>`;
    }
    if (this.state.creating) {
      return html`<div>
        <div class="mw-formhead">
          <${SectionHeading} title="New maintenance window" />
          <button type="button" data-role="mw-cancel-form"
            onClick=${() => this.setState({ creating: false })}>Cancel</button>
        </div>
        <${WindowForm}
          onSaved=${() => this.setState({ creating: false }, () => this.reload())}
        />
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
            ${rows.map((w) => this.row(w))}
          </ul>`}
      ${rows.length < body.total
        ? html`<p class="hint">Showing ${count(rows.length)} of ${count(body.total)}.</p>`
        : null}
    </div>`;
  }

  row(w) {
    const open = this.state.open === w.id;
    const busy = this.state.busy === w.id;
    const failed = this.state.failed && this.state.failed.wid === w.id ? this.state.failed : null;
    const confirmable = w.status === "pending_confirmation" && can("mw.confirm");
    return html`<li class="mw-row" key=${w.id} data-window=${w.id} data-status=${w.status}>
      <span class="mw-row-status"><${Badge} tone=${TONE[w.status] || "muted"}>${
        LABEL[w.status] || w.status}<//></span>
      <button type="button" class="mw-row-name" data-role="mw-open"
              aria-expanded=${open ? "true" : "false"}
              onClick=${() => this.setState({ open: open ? null : w.id, failed: null })}>
        <span class="mw-row-caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
        ${w.redacted
          ? html`<span class="muted" data-role="redacted"
              >Maintenance on ${plural(w.target_count, "host", "hosts")}</span
            >`
          : w.name}
        ${w.created_by_agent
          ? html`<${Badge} tone="info" title="Created through the API by a service token">agent<//>`
          : null}
      </button>
      <span class="mw-row-meta muted">${w.redacted ? "" : `${w.organization_name} · `}${
        plural(w.target_count, "host", "hosts")}</span>
      <span class="mw-row-when" data-role="countdown">${when(w)}</span>
      <span class="mw-row-actions">
        ${confirmable
          ? html`<button type="button" class="primary" data-role="confirm" disabled=${busy}
              onClick=${() => this.confirmWindow(w.id)}>${busy ? "Confirming…" : "Confirm"}</button>`
          : null}
        ${w.status === "active" && can("mw.write") && !open
          ? html`<button type="button" data-role="end-now" disabled=${busy}
              onClick=${() => this.endNow(w.id)}>End now</button>`
          : null}
      </span>
      ${failed
        ? html`<p class="mw-row-error error" role="alert" data-role="row-error">${failed.message}</p>`
        : null}
      ${open
        ? html`<${WindowDetail} wid=${w.id} onChanged=${() => this.refresh()}
            onEdit=${(detail) => this.setState({ editing: detail })} />`
        : null}
    </li>`;
  }
}

/* The one number a row is read for: until it starts, until it ends. A window awaiting
 * confirmation says what the confirmation is racing — its start, patch band included, which is
 * when the sweep expires it (D6). A window that stopped says nothing: its badge already does. */
function when(w) {
  if (w.status === "pending_confirmation") {
    const left = w.starts_in_s - (w.patch_s || 0);
    return left > 0 ? `confirm within ${humanise(left)}` : "expiring — start passed";
  }
  if (w.status === "scheduled") return w.starts_in_s > 0 ? `in ${humanise(w.starts_in_s)}` : "starting";
  if (w.status === "active") return w.ends_in_s > 0 ? `${humanise(w.ends_in_s)} left` : "ending";
  return "";
}
