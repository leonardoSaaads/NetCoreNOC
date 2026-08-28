/* The landing screen, shaped by what the principal can act on (draft §5).
 *
 * ## Live versus on demand (Part III, closing draft §12.4)
 *
 * The three live figures — stats, graph, situations — are already in the store because the update
 * stream writes them; reading them costs this screen nothing. **Everything else is on demand.**
 *
 * The reason is principle 4's discipline pointed at the client: the corpus figures, the promotion
 * history and the audit tail are reads nobody has measured on a busy appliance, and putting an
 * unmeasured cost on the screen every operator lands on is exactly the shape of change this
 * project does not ship. So each of those is a tile with a control, and once fetched it shows
 * **when** it was fetched — a number with no timestamp is a number an operator will assume is
 * current.
 */

import { html, Component } from "../dom.js";
import { get } from "../api.js";
import { Stat, Empty, Loading, Failed, SectionHeading } from "../widgets.js";
import { plural, relative, absolute, TIMEZONE } from "../format.js";
import { can, canEdit, scopeSummary } from "../session.js";
import * as store from "../store.js";

export class Overview extends Component {
  constructor(props) {
    super(props);
    this.state = { live: store.get() };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
  }

  componentWillUnmount() { if (this.unsubscribe) this.unsubscribe(); }

  render(_props, { live }) {
    const stats = live.stats;
    const situations = live.situations || [];
    const scope = scopeSummary();

    // **A spinner is not an error state, and a monitoring console must not confuse them.**
    // Driving this screen with a failing `/api/stats` (Phase 6) left it on "Reading the
    // appliance…" indefinitely: on screen, a console that cannot reach its own API and a network
    // that is merely quiet looked identical. That is the exact failure `api.readAll` exists to
    // avoid elsewhere, reached here through a different door — the store, which has no error
    // channel of its own beyond `connection`.
    if (!stats) {
      if (live.connection === "error") {
        return html`<${Failed}
          error=${{ detail: "The appliance did not answer /api/stats, /api/graph or " +
                            "/api/situations. Nothing on this screen is current." }}
          what="the live picture"
          retry=${() => globalThis.location.reload()} />`;
      }
      return html`<${Loading} label="Reading the appliance" />`;
    }

    const quiet = stats.active_alarms === 0 && situations.length === 0 && stats.devices === 0;
    if (quiet) {
      return html`<${Empty}
        title="Nothing has arrived yet."
        will=${"This screen fills in as soon as the first SNMP trap reaches the appliance. " +
              "Devices, alarm classes, entities and severities are all learned from the trap " +
              "stream — there is no inventory to import and no MIB to load."}
        meanwhile=${`Point a device's trap destination at this appliance (UDP 162 by default) ` +
                    `and send one trap. The Alarm classes screen shows it immediately; grouping ` +
                    `begins once a second, related alarm arrives.`} />`;
    }

    return html`<div class="overview">
      ${scope ? html`<p class="warnbox" title=${scope.title}>
        Your view is scoped to ${plural(scope.neCount, "network element", "network elements")}.
        Situations may extend beyond it; members you cannot see are shown as a redacted count.
      </p>` : null}

      <div class="stat-row">
        <${Stat} label="active alarms" value=${stats.active_alarms}
                 tone=${stats.active_alarms ? "alarm" : "quiet"} />
        <${Stat} label="open situations" value=${stats.open_situations} />
        <${Stat} label="devices" value=${stats.devices}
                 note="learned, not configured" />
        <${Stat} label="alarm classes" value=${stats.classes}
                 note="learned, not configured" />
        <${Stat} label="p95 latency" value=${`${stats.latency_p95_s ?? 0} s`}
                 title="Time from trap arrival to correlation, 95th percentile." />
        ${(stats.ingest_gaps || []).length
          ? html`<${Stat} label="ingest gaps" value=${stats.ingest_gaps.length} tone="warn"
                          note="closed gaps, kept for history" />`
          : null}
      </div>

      <${Health} stats=${stats} rate=${live.trapRate} />

      <${SectionHeading} title="Open situations"
        hint="Newest first. Open one to see the per-term contributions that produced each link." />
      ${situations.length
        ? html`<ul class="mini-list">${situations.slice(0, 8).map((s) => html`
            <li key=${s.id}>
              <a href=${`#/situations/${s.id}`}>#${s.id}</a>
              <span>${plural(s.alarm_count, "alarm")}</span>
              <span class="muted">${relative(s.updated_at)}</span>
            </li>`)}</ul>`
        : html`<p class="hint">No open situations. Alarms are arriving but nothing has correlated
            into a group — which is the appliance saying the estate is noisy rather than broken.</p>`}

      ${canEdit() ? html`<${EditorPanel} />` : null}
      ${can("promotion.read") ? html`<${OnDemand}
          title="Judge and promotion"
          hint=${"What the promotion gate last decided, why it refused, and the sealed holdout's " +
                 "query count. Read on request rather than on load: this project does not put an " +
                 "unmeasured cost on the screen every operator lands on."}
          path="/api/promotion"
          render=${(data) => html`<div class="stat-row">
            <${Stat} label="model versions" value=${(data.model_versions || []).length} />
            <${Stat} label="decisions recorded" value=${(data.promotions || []).length} />
            <${Stat} label="seal query count" value=${data.seal_query_count}
                     tone=${data.seal_query_count === 0 ? "quiet" : "warn"}
                     note=${data.seal_query_count === 0
                       ? "the holdout has never been read"
                       : "the holdout has been read"} />
            <${Stat} label="active model version"
                     value=${data.active_model_version_id ?? "—"} />
          </div>
          <p><a href="#/promotion">Open the full record →</a></p>`} />` : null}
      ${can("config.read") ? html`<${OnDemand}
          title="What capture is holding"
          hint=${"The feedback corpus every evidence claim is built on, in rows. Deterministic " +
                 "and cheap to read, but not free, so it is read when asked for."}
          path="/api/dataset/retention"
          render=${(data) => html`<div class="stat-row">
            ${Object.entries(data.stats || {}).slice(0, 5).map(([key, value]) => html`
              <${Stat} key=${key} label=${key.replaceAll("_", " ")} value=${value} />`)}
            <${Stat} label="capture" value=${data.capture_enabled ? "on" : "off"}
                     tone=${data.capture_enabled ? "quiet" : "warn"} />
          </div>
          <p><a href="#/corpus">Open the corpus screen →</a></p>`} />` : null}
      ${can("audit.read") ? html`<${AdminNotes} />` : null}
    </div>`;
  }
}

/**
 * **Is the appliance keeping up, and is it hearing anything?** (DECISIONS #222, F68)
 *
 * Every number here was already served by `/api/stats` on every poll and rendered nowhere.
 * `queue_depth` is *the* figure that says whether correlation is behind the wire, and
 * `receiver.denied` is the only evidence an operator has that their allowlist is refusing their
 * own equipment — measured, an allowlist that denied every trap produced no log line, no warning
 * and no rendered counter, so the appliance received traffic, produced nothing, and said nothing.
 *
 * The trap rate is **derived in the client** between two polls rather than added to the route, and
 * it prints the window it covers beside it, because a rate with no window is a number nobody can
 * act on. Until a second sample arrives it says so instead of showing a zero.
 *
 * CPU, memory and uptime are genuinely absent from the API and are **not** invented here.
 */
function Health({ stats, rate }) {
  const receiver = stats.receiver;
  const depth = stats.queue_depth ?? 0;
  return html`<section class="panel-block">
    <${SectionHeading} title="System health"
      hint=${"What the appliance knows about itself. Queue depth is the one number that means it " +
             "is not keeping up: growing and not falling back is the signal to act on."} />
    <div class="stat-row">
      <${Stat} label="queue depth" value=${depth} tone=${depth ? "warn" : "quiet"}
               note="traps waiting to be correlated"
               title="Datagrams parsed and queued but not yet correlated. The trap path never
                      blocks: under sustained overload the queue fills and overflow is counted as
                      an ingest gap rather than silently lost." />
      <${Stat} label="trap rate"
               value=${rate ? `${rate.perSecond.toFixed(rate.perSecond < 10 ? 2 : 0)} /s` : "—"}
               note=${rate
                 ? `over the last ${rate.windowS.toFixed(1)} s`
                 : "waiting for a second reading"}
               title="Derived from receiver.received between the last two updates, in this browser.
                      The appliance serves the counter; it does not serve a rate." />
    </div>
    ${receiver
      ? html`<div class="stat-row">
          <${Stat} label="received" value=${receiver.received} note="datagrams on the socket" />
          <${Stat} label="accepted" value=${receiver.accepted} note="became alarms" />
          <${Stat} label="denied" value=${receiver.denied}
                   tone=${receiver.denied ? "alarm" : null}
                   note=${receiver.denied ? "source not in the allowlist" : "allowlist refused none"} />
          <${Stat} label="quarantined" value=${receiver.quarantined}
                   tone=${receiver.quarantined ? "warn" : null}
                   note="the parser refused them" />
          <${Stat} label="dropped" value=${receiver.dropped}
                   tone=${receiver.dropped ? "alarm" : null}
                   note="queue full — an ingest gap" />
        </div>`
      : html`<p class="hint">This appliance is serving <code>/api/stats</code> without a
          <code>receiver</code> block, so the socket counters are not available. That happens when
          the API is built without the process runner — it is not a fault of the network.</p>`}
  </section>`;
}

/** What an editor's labels have produced — the thing an editor has never been shown (draft §5.2). */
class EditorPanel extends Component {
  render() {
    const situations = store.get().situations || [];
    const splittable = situations.filter((s) => s.alarm_count >= 2);
    return html`<section class="panel-block">
      <${SectionHeading} title="Your labelling"
        hint=${"A label is only evidence if it was a decision. A situation with one member " +
               "contains no pair to judge, so it is not counted here."} />
      <div class="stat-row">
        <${Stat} label="situations you can judge" value=${splittable.length}
                 note="two or more members" />
        <${Stat} label="singletons" value=${situations.length - splittable.length}
                 note="nothing to confirm or split" />
      </div>
      <p class="hint">
        How many of your labels have reached the pre-registered floors is computed by${" "}
        <code>make shadow-report</code>, which runs offline over frozen inputs and is compared
        byte-for-byte by a test. <b>This console displays such numbers and never recomputes
        them</b> — a second implementation of the shadow verdict would be a second source of truth
        for the figure four releases of evidence discipline rest on (draft §4). That report has no
        HTTP route today; running it needs shell access to the appliance.
      </p>
      <p><a href="#/labelling">Open the labelling screen →</a></p>
    </section>`;
  }
}

function AdminNotes() {
  return html`<section class="panel-block">
    <${SectionHeading} title="Reports that are not on any screen"
      hint=${"Named rather than hidden. Each is a deterministic offline report over frozen " +
             "inputs, several are byte-compared by tests, and none has an HTTP route — so this " +
             "console cannot show them without becoming a second implementation of the number."} />
    <${DataTableOfReports} />
  </section>`;
}

function DataTableOfReports() {
  const reports = [
    ["make bias-report", "confirms vs splits, operator concentration, label latency, effective sample size"],
    ["make agreement-report", "how well the built-in scorer already agrees with your operators, conditioned"],
    ["make shadow-report", "the sufficiency verdict, the minimum detectable difference, the sealed holdout"],
    ["make census", "what the promotion gate decides on this corpus, stated in advance"],
    ["python -m netcorenoc audit verify", "walks the audit hash chain and reports the first broken link"],
  ];
  return html`<div class="table-scroll"><table class="data">
    <thead><tr><th scope="col">command</th><th scope="col">what it says</th></tr></thead>
    <tbody>${reports.map(([command, what]) => html`<tr key=${command}>
      <td><code>${command}</code></td><td>${what}</td></tr>`)}</tbody>
  </table></div>`;
}

/**
 * A tile that fetches only when asked, and then says when it fetched.
 *
 * The timestamp is the whole point. A figure with no time on it is a figure an operator will
 * assume is current, and during an incident that assumption is expensive.
 */
class OnDemand extends Component {
  constructor(props) {
    super(props);
    this.state = { status: "idle", data: null, error: null, at: null };
    this.load = this.load.bind(this);
  }

  async load() {
    this.setState({ status: "loading" });
    try {
      const data = await get(this.props.path);
      this.setState({ status: "ready", data, at: Date.now() / 1000, error: null });
    } catch (error) {
      this.setState({ status: "error", error });
    }
  }

  render({ title, hint, render: renderBody }, { status, data, error, at }) {
    return html`<section class="panel-block on-demand">
      <${SectionHeading} title=${title} hint=${hint}>
        <button type="button" onClick=${this.load} disabled=${status === "loading"}>
          ${status === "idle" ? "Compute now" : status === "loading" ? "Reading…" : "Recompute"}
        </button>
      <//>
      ${status === "idle" ? html`<p class="hint">Not computed yet.</p>` : null}
      ${status === "loading" ? html`<${Loading} label=${`Reading ${title.toLowerCase()}`} />` : null}
      ${status === "error" ? html`<${Failed} error=${error} retry=${this.load} what=${title} />` : null}
      ${status === "ready" ? html`<div>
        <p class="computed-at">Last computed ${relative(at)} —
          <span title=${`${absolute(at)} (${TIMEZONE})`}>${absolute(at)}</span></p>
        ${renderBody(data)}
      </div>` : null}
    </section>`;
  }
}
