/* One maintenance window, opened (v0.22.0, item 19, ADR #389).
 *
 * A scheduled window was a dead end: a row reading "Scheduled · teste 2 · 1 host · in 10 min" and
 * no way to see the hosts, the rules or the times, or to change anything. This is the window as
 * the server holds it — hosts and what each still collects, both clocks, the patch bands, owner,
 * visibility and its history — and the actions an engineer mid-task looks for, where they look:
 *
 *   * **scheduled or awaiting confirmation** — edit (the same form, opened on what was scheduled),
 *     cancel, confirm;
 *   * **running** — end now, extend, shorten. Nothing else: the part already in force is frozen,
 *     and the server refuses an edit that would rewrite it.
 *
 * Every consequential press is two presses: the first says what will happen, the second does it.
 */

import { html, Component } from "../../dom.js";
import { get, post } from "../../api.js";
import { Badge, Failed, Loading } from "../../widgets.js";
import { absolute, plural } from "../../format.js";
import { can } from "../../session.js";
import { describeRule } from "./mwrules.js";
import { SiteAndYourTime, TimelineBar } from "./mwtime.js";
import { endOnlyBody, humanise, siteLocal } from "./mwdraft.js";

const HOUR = 3600;

export class WindowDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { detail: null, error: null, arming: null, busy: false, note: null,
                   shortenTo: "" };
    this.read = this.read.bind(this);
  }

  componentDidMount() { this.read(); }

  async read() {
    try {
      this.setState({ detail: await get(`/api/maintenance-windows/${this.props.wid}`), error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  /** Run one action, then re-read the window and the list around it. */
  async act(send, note) {
    this.setState({ busy: true, arming: null });
    try {
      await send();
      this.setState({ busy: false, note });
      await this.read();
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, note: null, error });
    }
  }

  /** The first press arms, the second fires. */
  armed(key, label, run, danger = false) {
    const on = this.state.arming === key;
    return html`<button type="button" class=${danger && on ? "danger" : ""}
        data-role=${`mw-${key}`} disabled=${this.state.busy}
        onClick=${() => (on ? run() : this.setState({ arming: key }))}>
      ${on ? `${label} — press again` : label}</button>`;
  }

  render({ wid, onEdit }, { detail: w, error, busy, note, shortenTo }) {
    if (error && !w) return html`<${Failed} error=${error} retry=${this.read} what="this window" />`;
    if (!w) return html`<${Loading} label="Reading the window" />`;
    if (w.redacted) {
      return html`<div class="mw-detail" data-window=${wid}><p class="muted">
        Its details follow the window's visibility, and yours does not include them.</p></div>`;
    }
    const editable = can("mw.write");
    const live = ["pending_confirmation", "scheduled", "active"].includes(w.status);
    const byTarget = new Map();
    for (const rule of w.rules || []) {
      const one = { ...rule, slot_local_from: rule.slot_starts_at && siteLocal(rule.slot_starts_at, w.tz),
                    slot_local_to: rule.slot_ends_at && siteLocal(rule.slot_ends_at, w.tz) };
      byTarget.set(rule.ne_id, [...(byTarget.get(rule.ne_id) || []), describeRule(one)]);
    }
    const history = [
      ["created", w.created_at, w.owner_ref],
      ["confirmed", w.confirmed_at, w.confirmed_by],
      ["changed", w.updated_at !== w.created_at ? w.updated_at : null, null],
      ["cancelled", w.cancelled_at, null],
      ["ended", w.ended_at, null],
    ].filter(([, at]) => at);
    const extend = (seconds) => this.act(
      () => post(`/api/maintenance-windows/${wid}/extend`,
        { ends_at: new Date((w.ends_at + seconds) * 1000).toISOString() }),
      `Extended by ${humanise(seconds)}.`);
    return html`<div class="mw-detail" data-window=${wid}>
      <${TimelineBar} startsAt=${w.starts_at} endsAt=${w.ends_at} patchS=${w.patch_s}
        now=${Date.now() / 1000} />
      <dl class="mw-facts">
        <div><dt>Starts</dt><dd><${SiteAndYourTime} instant=${w.starts_at} siteZone=${w.tz}
          siteTime=${siteLocal(w.starts_at, w.tz)} siteOffset=${w.site_offset} /></dd></div>
        <div><dt>Ends</dt><dd><${SiteAndYourTime} instant=${w.ends_at} siteZone=${w.tz}
          siteTime=${siteLocal(w.ends_at, w.tz)} siteOffset=${w.site_offset} /></dd></div>
        <div><dt>Patch band</dt><dd>${humanise(w.patch_s)} either side</dd></div>
        <div><dt>Organization</dt><dd>${w.organization_name}</dd></div>
        <div><dt>Owner</dt><dd>${w.owner_ref || "—"}${w.created_by_agent
          ? html`${" "}<${Badge} tone="info">agent<//>` : null}</dd></div>
        <div><dt>Visible to</dt><dd>${w.visibility}</dd></div>
        ${w.description ? html`<div><dt>Notes</dt><dd>${w.description}</dd></div>` : null}
      </dl>
      <h4 class="mw-sub">${plural((w.targets || []).length, "host")} — what still gets through</h4>
      <ul class="mw-dtargets">${(w.targets || []).map((t) => html`<li key=${t.ne_id}>
        <b>${t.label || t.address}</b>${t.label ? html`${" "}<code class="mono">${t.address}</code>` : null}
        <span class="muted">${" — "}${(byTarget.get(t.ne_id) || ["nothing"]).join("; ")}</span>
      </li>`)}</ul>
      ${w.ledger ? html`<p class="muted">Ledger: ${plural(w.ledger.seen || 0, "fault")} raised,
        ${w.ledger.cleared || 0} cleared inside.</p>` : null}
      <h4 class="mw-sub">History</h4>
      <ul class="mw-history">${history.map(([what, at, who]) => html`<li key=${what}>
        ${what} ${absolute(at)}${who ? html`${" "}<span class="muted">by ${who}</span>` : null}</li>`)}
      </ul>
      ${note ? html`<p class="ok-note" role="status">${note}</p>` : null}
      ${error ? html`<p class="err" role="alert">${error.message}</p>` : null}
      ${editable && live ? html`<div class="mw-actions" data-role="mw-actions">
        ${w.status === "active"
          ? html`
            ${this.armed("end", "End now",
              () => this.act(() => post(`/api/maintenance-windows/${wid}/end`), "Ended."), true)}
            <button type="button" disabled=${busy} onClick=${() => extend(HOUR / 2)}>+30 min</button>
            <button type="button" disabled=${busy} onClick=${() => extend(HOUR)}>+1 h</button>
            <button type="button" disabled=${busy} onClick=${() => extend(2 * HOUR)}>+2 h</button>
            <form class="mw-shorten" onSubmit=${(e) => {
              e.preventDefault();
              const at = Date.parse(`${shortenTo}:00${(w.site_offset || "UTC+00:00").replace("UTC", "")}`);
              if (Number.isFinite(at)) {
                this.act(() => post(`/api/maintenance-windows/${wid}`, endOnlyBody(w, at / 1000)),
                  "Shortened.");
              }
            }}>
              <label for=${`short-${wid}`}>End at (site time)</label>
              <input id=${`short-${wid}`} type="datetime-local" value=${shortenTo}
                onInput=${(e) => this.setState({ shortenTo: e.target.value })} />
              <button type="submit" disabled=${busy || !shortenTo}>Shorten</button>
            </form>`
          : html`
            <button type="button" data-role="mw-edit" onClick=${() => onEdit(w)}>Edit</button>
            ${w.status === "pending_confirmation" && can("mw.confirm")
              ? html`<button type="button" disabled=${busy} onClick=${() => this.act(
                  () => post(`/api/maintenance-windows/${wid}/confirm`), "Confirmed.")}>Confirm</button>`
              : null}
            ${this.armed("cancel", "Cancel the window",
              () => this.act(() => post(`/api/maintenance-windows/${wid}/cancel`), "Cancelled."),
              true)}`}
      </div>` : null}
    </div>`;
  }
}
