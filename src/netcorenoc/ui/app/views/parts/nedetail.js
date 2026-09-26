/* One element, opened on the Entities screen (v0.23.0, #395).
 *
 * What an operator opens an element for, in that order: how its alarm load moved over the last day
 * (the same stacked count as the Overview, for this element alone), which of its components carry
 * the alarms, and where to go next. The identification evidence — which varbind the appliance
 * chose as the component key, and the scores behind it — is kept, behind one disclosure: it is
 * what an admin needs to repair a wrong decision, and nothing an operator reads to work an incident.
 */

import { html, Component } from "../../dom.js";
import { get, post } from "../../api.js";
import { DataTable, Loading } from "../../widgets.js";
import { StackedArea } from "../../stack.js";
import { SCALE, UNPLACED, count, plural, percent, relative, score, timeTitle } from "../../format.js";
import { clock } from "../../chartdata.js";
import { can } from "../../session.js";
import { Destructive } from "../../destructive.js";

const DAY = 86400;
const SHOWN = 8;

export class ElementDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { trend: null, parts: null, evidence: null, errors: {}, all: false };
  }

  componentDidMount() { this.read(); }

  async read() {
    const id = this.props.neId;
    const [trend, parts] = await Promise.allSettled([
      get(`/api/activity/active?range_s=${DAY}&buckets=24&ne_id=${id}`),
      get(`/api/elements/${id}/components`),
    ]);
    const pick = (r) => (r.status === "fulfilled" ? r.value : null);
    this.setState({
      trend: pick(trend), parts: pick(parts),
      errors: { trend: trend.status === "rejected", parts: parts.status === "rejected" },
    });
  }

  async openEvidence(event) {
    if (!event.currentTarget.open || this.state.evidence) return;
    try { this.setState({ evidence: await get(`/api/entities/${this.props.neId}`) }); }
    catch { this.setState({ evidence: { failed: true } }); }
  }

  render({ neId }, { trend, parts, evidence, errors, all }) {
    const series = (trend && trend.series) || {};
    const bands = [...SCALE, UNPLACED].filter((b) => series[b.key])
      .map((b) => ({ key: b.key, label: b.label, level: b.level, values: series[b.key] }));
    const labels = trend ? Array.from({ length: trend.buckets },
      (_, i) => clock(trend.from + (i + 1) * trend.bucket_s, DAY)) : [];
    const rows = parts ? (all ? parts.components : parts.components.slice(0, SHOWN)) : [];
    const kinds = parts ? Object.entries(parts.kinds) : [];
    return html`<div class="nedetail">
      <div class="nedetail-grid">
        <div class="nedetail-trend">
          ${errors.trend ? html`<p class="hint">Could not read its alarm history.</p>` : null}
          ${trend ? html`<${StackedArea} title="Active alarms, last 24 hours" bands=${bands}
              labels=${labels} source="active at each point" span="every hour" />`
            : errors.trend ? null : html`<${Loading} label="Reading its history" />`}
        </div>
        <div class="nedetail-parts">
          <h4 class="chart-title">Components${parts ? ` · ${count(parts.total)}` : ""}</h4>
          ${errors.parts ? html`<p class="hint">Could not read its components.</p>` : null}
          ${parts && !parts.total ? html`<p class="muted">None learned: the traps carry no
              per-port or per-slot key.</p>` : null}
          ${kinds.length ? html`<p class="nedetail-kinds">${kinds.map(([k, n]) =>
              html`<span key=${k} class="badge">${plural(n, k)}</span>`)}</p>` : null}
          ${rows.length ? html`<${DataTable} kind="compact" columns=${[
              { key: "key", label: "component" },
              { key: "active", label: "active alarms", numeric: true },
              { key: "seen", label: "last trap" },
            ]} rows=${rows.map((c) => ({
              key: c.id, tone: c.active ? "alarm" : null,
              cells: {
                key: html`<code title=${`learned from ${c.key_source}, confidence ${percent(c.confidence)}`}>${c.key}</code>`,
                active: count(c.active),
                seen: html`<span title=${timeTitle(c.last_seen)}>${relative(c.last_seen)}</span>`,
              },
            }))} />` : null}
          ${parts && parts.components.length > SHOWN ? html`<button type="button" class="tap"
              onClick=${() => this.setState({ all: !all })}>${all ? `Show the busiest ${SHOWN}`
              : `Show ${count(parts.components.length)}`}</button>` : null}
        </div>
      </div>
      <nav class="nedetail-acts" aria-label="Open this element in">
        <a class="tap" href=${`#/graph?ne=${neId}`}>Graph</a>
        <a class="tap" href=${`#/timeline?ne=${neId}&win=${DAY}`}>Timeline</a>
      </nav>
      <details class="nedetail-evidence" onToggle=${(e) => this.openEvidence(e)}>
        <summary>How the appliance identified its components</summary>
        ${evidence ? html`<${Evidence} neId=${neId} data=${evidence} onDone=${() => this.setState({ evidence: null })} />`
          : html`<${Loading} label="Reading the evidence" />`}
      </details>
    </div>`;
  }
}

/** The varbind profiler's decision and its scores, and the admin's two repairs. */
function Evidence({ neId, data, onDone }) {
  if (data.failed) return html`<p class="hint">Could not read the identification evidence.</p>`;
  const obs = (data.candidates || []).reduce((n, c) => n + c.n_obs, 0);
  return html`<div class="nedetail-ev">
    <p class="hint">Each candidate varbind is scored on repeatability (R), distinctness across
      devices (X) and within this device (D); the chosen key is the best one that meets the floor.</p>
    <${DataTable} kind="compact" columns=${[
      { key: "oid", label: "varbind OID" },
      { key: "r", label: "R", numeric: true }, { key: "x", label: "X", numeric: true },
      { key: "d", label: "D", numeric: true }, { key: "score", label: "score", numeric: true },
      { key: "obs", label: "obs", numeric: true }, { key: "ok", label: "meets floor" },
    ]} rows=${(data.candidates || []).map((c) => ({
      key: c.varbind_oid, tone: c.meets_floor ? null : "quiet",
      cells: {
        oid: c.varbind_name ? html`<b>${c.varbind_name}</b> <code>${c.varbind_oid}</code>`
          : html`<code>${c.varbind_oid}</code>`, r: score(c.r), x: score(c.x), d: score(c.d),
        score: html`<b>${score(c.score)}</b>`, obs: count(c.n_obs), ok: c.meets_floor ? "yes" : "no",
      },
    }))} />
    ${can("entity.reset") ? html`<${Destructive} title="Forget the learned identity decision"
      hint="Re-decides the component key and severity from the evidence that exists now."
      previewLabel="Reset identity…" confirmLabel="Forget the decision"
      consequence=${"The learned component key and severity for this element are discarded and " +
                    "re-decided from the evidence that exists now. The evidence is kept."}
      preview=${async () => ({ measured: false, message: "One decision, for one element." })}
      apply=${async () => post(`/api/entities/${neId}/reset`, {})} onDone=${onDone} />` : null}
    ${can("profile.reset") ? html`<${Destructive} title="Wipe the profiler evidence"
      hint="Drops the accumulated observations so identity and severity re-measure from scratch."
      previewLabel="Wipe evidence…" confirmLabel="Wipe the evidence"
      consequence=${`Deletes ${count(obs)} observations across ` +
                    `${plural((data.candidates || []).length, "candidate OID")} for this element; ` +
                    "the appliance re-learns from traffic that arrives after this point."}
      preview=${async () => ({ measured: true, observations: obs })}
      renderPreview=${(p) => html`<p><b>Would delete</b> ${count(p.observations)} observations.</p>`}
      apply=${async () => post(`/api/profiles/${neId}/reset`, {})} onDone=${onDone} />` : null}
  </div>`;
}
