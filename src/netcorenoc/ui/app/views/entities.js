/* Entities: what the appliance has learned about each network element, and the evidence for it.
 *
 * This screen is the text equivalent of the network graph, and that is deliberate: the graph is
 * the one drawing no test executes and no keyboard can operate, so everything it shows has to be
 * reachable here as ordinary DOM.
 *
 * The varbind-profiler table is the product's explainability claim applied one level below
 * correlation: not "this is the entity key" but "here are the R, X and D scores, the observation
 * counts, and whether the candidate met the floor". An admin can reset a poisoned decision, and
 * both resets are destructive and preview first.
 */

import { html, Component } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, Empty, DataTable, SectionHeading, Loading, Failed, Badge } from "../widgets.js";
import { score, percent, count, plural } from "../format.js";
import { can } from "../session.js";
import { Destructive } from "../destructive.js";

export class Entities extends Loader {
  constructor(props) {
    super(props);
    this.what = "the entity list";
    this.loadingLabel = "Reading network elements";
    this.state = { ...this.state, open: null };
  }

  async load() {
    const [nes, states] = await Promise.all([get("/api/entities"), get("/api/state-clears")]);
    return { nes, states };
  }

  view({ nes, states }) {
    if (!nes.length) {
      return html`<${Empty}
        title="No network elements yet."
        will=${"One row appears for every device the appliance has heard a trap from, with the " +
               "entity tree it inferred and the varbind evidence behind that inference."}
        meanwhile=${"Nothing has to be imported. Point a device at this appliance and it appears " +
                    "here after its first trap."} />`;
    }
    return html`<div class="entitiesview">
      <p class="hint">${plural(nes.length, "network element")}. Everything below was inferred
        from traps: no inventory was imported and no MIB was loaded.</p>
      <ul class="cards">
        ${nes.map((ne) => html`<li key=${ne.id}>
          <${NeCard} ne=${ne} open=${this.state.open === ne.id}
                     onToggle=${() => this.setState({ open: this.state.open === ne.id ? null : ne.id })} />
        </li>`)}
      </ul>
      ${states.length ? html`<section class="panel-block">
        <${SectionHeading} title="Learned state-clear fields"
          hint=${"Which varbind tells the appliance an alarm has cleared, per class. Learned " +
                 "from the traffic; keyed on (class, varbind OID) and not on any device."} />
        <${DataTable} columns=${[
          { key: "class", label: "class" },
          { key: "oid", label: "varbind OID" },
          { key: "raise", label: "raise value" },
          { key: "clear", label: "clear value" },
        ]} rows=${states.map((s, i) => ({
          key: `${s.class}-${i}`,
          cells: {
            class: s.class,
            oid: html`<code class="mono">${s.varbind_oid}</code>`,
            raise: s.raise_value,
            clear: s.clear_value,
          },
        }))} />
      </section>` : null}
    </div>`;
  }
}

function NeCard({ ne, open, onToggle }) {
  return html`<article class=${open ? "sit expanded" : "sit"}>
    <div class="sit-head">
      <button type="button" class="sit-toggle" aria-expanded=${open ? "true" : "false"}
              onClick=${onToggle}>
        <!-- v0.16.3: this line rendered \`ne.label\` for three releases and \`/api/entities\` never
             served the field, so the fallback to the address was permanent — the whole of *"I
             renamed the host and nothing changed in Entities"*. \`list_ne\` joins the label now,
             and the marker says which value is in use (DECISIONS #281, #284). -->
        <span class="sid">${ne.label || ne.ip}</span>
        ${ne.label ? html`<span class="muted">(declared)</span>` : null}
        <${Badge}>${plural(ne.entity_count, "entity", "entities")}<//>
        ${ne.vendor ? html`<span class="age">${ne.vendor}</span>` : null}
      </button>
    </div>
    ${open ? html`<div class="detail"><${NeDetail} neId=${ne.id} /></div>` : null}
  </article>`;
}

class NeDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { status: "loading", data: null, error: null };
    this.reload = this.reload.bind(this);
  }

  componentDidMount() { this.reload(); }

  async reload() {
    this.setState({ status: "loading" });
    try { this.setState({ status: "ready", data: await get(`/api/entities/${this.props.neId}`) }); }
    catch (error) { this.setState({ status: "error", error }); }
  }

  render({ neId }, { status, data, error }) {
    if (status === "loading") return html`<${Loading} label="Reading the entity tree" />`;
    if (status === "error") {
      return html`<${Failed} error=${error} retry=${this.reload} what="this network element" />`;
    }
    return html`<div class="detail-body">
      <${SectionHeading} title="Entity tree"
        hint="What the appliance decided this element's sub-entities are, and from which OID." />
      <${DataTable} columns=${[
        { key: "level", label: "level", numeric: true },
        { key: "key", label: "key" },
        { key: "source", label: "key source (OID)" },
        { key: "confidence", label: "confidence", numeric: true },
      ]} rows=${data.entities.map((e, i) => ({
        key: `${e.level}-${i}`,
        cells: {
          level: String(e.level),
          key: e.key,
          source: e.key_source === "self"
            ? html`<span class="muted">— (the NE itself)</span>`
            : html`<code class="mono">${e.key_source}</code>`,
          confidence: e.confidence != null ? percent(e.confidence) : "—",
        },
      }))} />

      <${SectionHeading} title="Varbind profiler — the evidence behind the choice"
        hint=${"R, X and D are the three components of the discriminator score. A candidate is " +
               "promotable only when it meets the floor; the ones that did not are shown too, " +
               "because a decision is only checkable against what it rejected."} />
      <${DataTable} columns=${[
        { key: "oid", label: "varbind OID" },
        { key: "r", label: "R", numeric: true, title: "repeatability" },
        { key: "x", label: "X", numeric: true, title: "cross-device distinctness" },
        { key: "d", label: "D", numeric: true, title: "within-device distinctness" },
        { key: "score", label: "score", numeric: true },
        { key: "obs", label: "obs", numeric: true },
        { key: "distinct", label: "distinct", numeric: true },
        { key: "promotable", label: "meets floor" },
      ]} rows=${data.candidates.map((c) => ({
        key: c.varbind_oid,
        tone: c.meets_floor ? null : "quiet",
        cells: {
          oid: html`<code class="mono">${c.varbind_oid}</code>`,
          r: score(c.r), x: score(c.x), d: score(c.d),
          score: html`<b>${score(c.score)}</b>`,
          obs: count(c.n_obs), distinct: count(c.n_distinct),
          promotable: c.meets_floor
            ? html`<span class="outcome outcome-ok">yes</span>`
            : html`<span class="muted">no</span>`,
        },
      }))} />

      ${can("entity.reset") ? html`<${Destructive}
        title="Forget the learned identity decision"
        hint="Re-decides the entity key and severity from the evidence that exists now."
        previewLabel="Reset identity…"
        confirmLabel="Forget the decision"
        consequence=${"The learned entity key and severity for this element are discarded and " +
                      "re-decided. The accumulated evidence is kept, so the same decision will " +
                      "usually be reached again — this repairs a decision, not the data."}
        preview=${async () => ({
          measured: false,
          message: "No count to preview: this discards one decision for one network element. " +
                   "History is kept and the profiler evidence below is untouched.",
        })}
        apply=${async () => post(`/api/entities/${neId}/reset`, {})}
        onDone=${this.reload} />` : null}

      ${can("profile.reset") ? html`<${Destructive}
        title="Wipe the profiler evidence"
        hint="Drops the accumulated observations so identity and severity re-measure from scratch."
        previewLabel="Wipe evidence…"
        confirmLabel="Wipe the evidence"
        consequence=${`Every observation in the table above is deleted for this element — ` +
                      `${count(data.candidates.reduce((n, c) => n + c.n_obs, 0))} observations ` +
                      `across ${plural(data.candidates.length, "candidate OID")}. The appliance ` +
                      `re-learns from traffic that arrives after this point, and what has ` +
                      `already been observed cannot be recovered.`}
        preview=${async () => ({
          measured: true,
          candidates: data.candidates.length,
          observations: data.candidates.reduce((n, c) => n + c.n_obs, 0),
        })}
        renderPreview=${(p) => html`<p><b>Would delete</b> ${count(p.observations)} observations
          across ${plural(p.candidates, "candidate OID")}, for this element only.</p>`}
        apply=${async () => post(`/api/profiles/${neId}/reset`, {})}
        onDone=${this.reload} />` : null}
    </div>`;
  }
}
