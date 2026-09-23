/* **Four cards, one at a time** (D8, IV.1). Never 200 fields at once.
 *
 * Most maintenance windows are created by people under time pressure, often from a different time
 * zone than the site. The maintainer's bar: *no wall of fields; the operator opens one card at a
 * time and fills it; almost no explanatory text; the form is understood by UI/UX principles
 * alone.* The four are: what and where · when · what still gets through · review.
 *
 * A completed card **collapses to a one-line summary**, so the operator always sees where they
 * are and never sees everything at once. The summary is not decoration: it is the only thing
 * standing between this and a wall of fields, and tapping it reopens the card.
 *
 * **The defaults do the explaining**: start = next quarter hour, end = start + 2 h, site = the
 * operator's own zone until changed, visibility = editors, patch band = 10 min. Change nothing
 * and you get a sane two-hour window, which is the commonest thing anyone wants.
 *
 * **The one sentence of explanatory text on the whole form** is card 3's *"Nothing from these
 * hosts is collected unless a rule below says otherwise"*, and it is there because D4's default
 * is the one thing a control cannot say by existing.
 *
 * The arithmetic — instants, offsets, the request body — is `mwdraft.js`; the per-target rule
 * chips are `mwrules.js`; the timeline bar and the zone picker are `mwtime.js`.
 */

import { html, Component } from "../../dom.js";
import { get, post } from "../../api.js";
import { ApiError } from "../../api.js";
import { TIMEZONE, count, plural } from "../../format.js";
import { session } from "../../session.js";
import { RulesCard } from "./mwrules.js";
import { SiteAndYourTime, TimelineBar, ZonePicker, cityOf } from "./mwtime.js";
import { humanise, nextQuarter, plusHours, windowBody, withOffset } from "./mwdraft.js";

const CARDS = ["What and where", "When", "What still gets through", "Review"];

export class WindowForm extends Component {
  constructor(props) {
    super(props);
    const start = nextQuarter();
    this.state = {
      card: 0,
      name: "",
      showDescription: false,
      description: "",
      organizationId: null,
      organizations: [],
      hosts: [],
      targets: [],
      hostQuery: "",
      tz: TIMEZONE,
      siteOffset: "",
      localStart: start,
      localEnd: plusHours(start, 2),
      allDay: false,
      patchMinutes: 10,
      ledgerEnabled: true,
      visibility: "editors",
      rules: [],
      preview: null,
      busy: false,
      error: null,
    };
  }

  async componentDidMount() {
    try {
      const [orgs, entities] = await Promise.all([get("/api/organizations"), get("/api/entities")]);
      this.setState({
        organizations: orgs.organizations || [],
        organizationId: orgs.default_organization_id,
        hosts: entities.entities || entities || [],
      });
    } catch (e) {
      this.setState({ error: e.message });
    }
    this.refreshOffset(this.state.tz);
  }

  /* The site's UTC offset **at the window's start**, from the server. Never computed here: a
   * window across a DST transition has two offsets and the browser knows only its own zone. */
  async refreshOffset(zone) {
    try {
      const body = await get(`/api/timezones?q=${encodeURIComponent(zone)}`);
      const hit = (body.zones || []).find((z) => z.zone === zone) || (body.zones || [])[0];
      if (hit) this.setState({ siteOffset: hit.offset }, () => this.preview());
    } catch { /* the offset stays what it was; the server still refuses a bad zone */ }
  }

  body() { return windowBody(this.state); }

  /* **The live preview, and it is the API's own dry run** (Part III). The number an operator
   * reads here and the number an agent reads from `/preview` are the same number because they
   * are the same call. */
  async preview() {
    if (this.state.targets.length === 0) { this.setState({ preview: null }); return; }
    try {
      const preview = await post("/api/maintenance-windows/preview", this.body());
      this.setState({ preview, error: null });
    } catch (e) {
      this.setState({ preview: null, error: e instanceof ApiError ? e.message : String(e) });
    }
  }

  set(patch, then) { this.setState(patch, then || (() => this.preview())); }

  toggleHost(host) {
    const on = this.state.targets.some((t) => t.ne_id === host.ne_id);
    const targets = on
      ? this.state.targets.filter((t) => t.ne_id !== host.ne_id)
      : [...this.state.targets, host];
    // A rule on a host that is no longer a target can never fire, and the API refuses it. Drop it
    // here so the operator never has to be told about a rule they cannot see.
    const rules = this.state.rules.filter((r) => targets.some((t) => t.ne_id === r.ne_id));
    this.set({ targets, rules });
  }

  async save() {
    this.setState({ busy: true, error: null });
    try {
      const created = await post("/api/maintenance-windows", this.body());
      this.props.onSaved(created);
    } catch (e) {
      this.setState({ busy: false, error: e instanceof ApiError ? e.message : String(e) });
    }
  }

  complete(index) {
    const s = this.state;
    if (index === 0) return s.name.trim().length > 0 && s.targets.length > 0;
    if (index === 1) return Boolean(s.localStart && s.localEnd && s.tz);
    if (index === 2) return true;  // "collect nothing" is a complete answer
    return false;
  }

  summary(index) {
    const s = this.state;
    if (index === 0) {
      return `${s.name || "unnamed"} · ${plural(s.targets.length, "host", "hosts")}`;
    }
    if (index === 1) {
      return `${s.localStart.slice(11, 16)}–${s.localEnd.slice(11, 16)} ${cityOf(s.tz)}`;
    }
    const withRules = new Set(s.rules.map((r) => r.ne_id)).size;
    return withRules === 0
      ? "nothing gets through"
      : `${plural(withRules, "host", "hosts")} with rules`;
  }

  render() {
    const s = this.state;
    return html`<form
      class="mw-form"
      data-role="window-form"
      onSubmit=${(e) => { e.preventDefault(); if (s.card === 3) this.save(); }}
    >
      ${CARDS.map((title, i) => this.card(i, title))}
      ${s.error ? html`<p class="error" role="alert">${s.error}</p>` : null}
    </form>`;
  }

  card(index, title) {
    const s = this.state;
    const open = s.card === index;
    const done = this.complete(index);
    if (!open) {
      return html`<button
        type="button"
        class="mw-card mw-card-shut"
        key=${index}
        data-card=${index}
        data-open="false"
        onClick=${() => this.setState({ card: index })}
      >
        <span class="mw-card-n">${index + 1}</span>
        <span class="mw-card-t">${title}</span>
        <span class="mw-card-s">${done ? this.summary(index) : ""}</span>
      </button>`;
    }
    return html`<section class="mw-card" key=${index} data-card=${index} data-open="true">
      <h3><span class="mw-card-n">${index + 1}</span> ${title}</h3>
      ${[this.whatAndWhere, this.when, this.through, this.review][index].call(this)}
      ${index < 3
        ? html`<button
            type="button"
            class="primary"
            disabled=${!done}
            data-role="next"
            onClick=${() => this.setState({ card: index + 1 }, () => this.preview())}
          >
            Next
          </button>`
        : null}
    </section>`;
  }

  whatAndWhere() {
    const s = this.state;
    const q = s.hostQuery.trim().toLowerCase();
    const shown = q
      ? s.hosts.filter(
          (h) => (h.label || "").toLowerCase().includes(q) || (h.ip || "").includes(q),
        )
      : s.hosts;
    return html`<div>
      <label
        >Name
        <input
          type="text"
          value=${s.name}
          maxlength="120"
          required
          onInput=${(e) => this.set({ name: e.target.value })}
      /></label>
      ${s.organizations.length > 1
        ? html`<label
            >Organization
            <select onChange=${(e) => this.set({ organizationId: Number(e.target.value) })}>
              ${s.organizations.map(
                (o) => html`<option key=${o.id} value=${o.id} selected=${o.id === s.organizationId}>
                  ${o.name}
                </option>`,
              )}
            </select>
          </label>`
        : null}
      <label
        >Hosts
        <input
          type="search"
          placeholder="Search by name or address"
          value=${s.hostQuery}
          onInput=${(e) => this.setState({ hostQuery: e.target.value })}
      /></label>
      <ul class="mw-hosts" data-role="hosts">
        ${shown.slice(0, 50).map((host) => {
          const on = s.targets.some((t) => t.ne_id === host.ne_id);
          return html`<li key=${host.ne_id}>
            <button
              type="button"
              class=${on ? "on" : ""}
              aria-pressed=${on ? "true" : "false"}
              onClick=${() => this.toggleHost(host)}
            >
              ${host.label || host.ip} ${host.label ? html`<span class="muted mono">${host.ip}</span>` : null}
            </button>
          </li>`;
        })}
      </ul>
      ${s.showDescription
        ? html`<label
            >Description
            <textarea
              rows="3"
              maxlength="2000"
              onInput=${(e) => this.setState({ description: e.target.value })}
            ></textarea>
          </label>`
        : html`<button type="button" class="link" onClick=${() => this.setState({ showDescription: true })}>
            Add a description
          </button>`}
    </div>`;
  }

  when() {
    const s = this.state;
    const startInstant = Date.parse(withOffset(s.localStart, s.siteOffset)) / 1000;
    const endInstant = Date.parse(withOffset(s.localEnd, s.siteOffset)) / 1000;
    const bad = endInstant <= startInstant;
    return html`<div>
      <${ZonePicker} value=${s.tz} onPick=${(zone) => this.set({ tz: zone }, () => this.refreshOffset(zone))} />
      <label class="mw-allday"
        ><input
          type="checkbox"
          checked=${s.allDay}
          onChange=${(e) => this.set({ allDay: e.target.checked })}
        />
        All day</label
      >
      ${s.allDay
        ? html`<label
              >Day
              <input
                type="date"
                value=${s.localStart.slice(0, 10)}
                onInput=${(e) =>
                  this.set({
                    localStart: `${e.target.value}T00:00`,
                    localEnd: `${e.target.value}T23:59`,
                  })}
            /></label>`
        : html`<div class="mw-when">
            <label
              >Start
              <input
                type="datetime-local"
                value=${s.localStart}
                onInput=${(e) =>
                  this.set({ localStart: e.target.value, localEnd: plusHours(e.target.value, 2) })}
            /></label>
            <label
              >End
              <input
                type="datetime-local"
                value=${s.localEnd}
                aria-invalid=${bad ? "true" : "false"}
                onInput=${(e) => this.set({ localEnd: e.target.value })}
            /></label>
            ${bad
              ? html`<p class="error" data-role="when-error">The end has to be after the start.</p>`
              : null}
          </div>`}
      <${SiteAndYourTime}
        instant=${startInstant}
        siteZone=${s.tz}
        siteTime=${withOffset(s.localStart, s.siteOffset)}
        siteOffset=${s.siteOffset}
      />
      <label
        >Patch window
        <input
          type="number"
          min="0"
          max="120"
          value=${s.patchMinutes}
          onInput=${(e) => this.set({ patchMinutes: Number(e.target.value) || 0 })}
        />
        minutes either side</label
      >
      <${TimelineBar}
        startsAt=${startInstant}
        endsAt=${endInstant}
        patchS=${s.patchMinutes * 60}
        now=${Date.now() / 1000}
      />
    </div>`;
  }

  through() {
    const s = this.state;
    return html`<div>
      <p class="hint">Nothing from these hosts is collected unless a rule here says otherwise.</p>
      <${RulesCard}
        targets=${s.targets}
        rules=${s.rules}
        onChange=${(rules) => this.set({ rules })}
      />
      <label class="mw-ledger"
        ><input
          type="checkbox"
          checked=${s.ledgerEnabled}
          onChange=${(e) => this.set({ ledgerEnabled: e.target.checked })}
        />
        Surface faults that outlive this window</label
      >
      ${!s.ledgerEnabled
        ? html`<p class="warn" data-role="ledger-risk">
            A fault that starts during this window and never clears will not be recorded at all.
          </p>`
        : null}
    </div>`;
  }

  review() {
    const s = this.state;
    const p = s.preview;
    const me = session();
    return html`<div>
      <p class="mw-preview" data-role="preview">
        ${p
          ? html`<strong>${count(p.devices)}</strong> ${plural(p.devices, "device", "devices")} ·
              <strong>${count(p.active_alarms)}</strong>
              ${plural(p.active_alarms, "active alarm", "active alarms")} ·
              ${p.starts_in_s > 0 ? `starts in ${humanise(p.starts_in_s)}` : "starts now"}`
          : "Pick at least one host to see what this would cover."}
      </p>
      ${p && p.targets_with_no_rule.length
        ? html`<p class="hint" data-role="silent-hosts">
            ${plural(p.targets_with_no_rule.length, "host", "hosts")} will send nothing at all.
          </p>`
        : null}
      ${p && p.needs_confirmation
        ? html`<p class="warn" data-role="needs-confirmation">
            Over six hours, so an editor or an admin has to confirm it before it takes effect.
          </p>`
        : null}
      <label
        >Who sees the details
        <select onChange=${(e) => this.set({ visibility: e.target.value })}>
          <option value="editors" selected=${s.visibility === "editors"}>Editors and admins</option>
          <option value="everyone" selected=${s.visibility === "everyone"}>Everyone</option>
        </select>
      </label>
      <p class="hint">Everyone sees that the window exists, whichever you pick.</p>
      <p class="muted">Owner: ${me ? me.username : "you"}</p>
      <button type="submit" class="primary" disabled=${s.busy || !this.complete(0)} data-role="save">
        ${s.busy ? "Saving…" : "Schedule it"}
      </button>
    </div>`;
  }
}
