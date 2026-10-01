/* One situation's HEAD LINE: what an operator reads while scanning a list of them.
 *
 * **Split out of `views/situations.js` in v0.16.1**, on the seam the two halves already had: that
 * module finds a situation and this one judges it.
 *
 * **v0.16.4 applies the same seam once more, one level down.** The head line *is* the finding — an
 * id, a name, its badges, an age, a permalink and the toggle — and everything below the fold is
 * the judging, which is now `views/parts/judge.js`. The two are read at different moments, by an
 * operator scanning a list versus one working a single incident, and they share no state: this
 * file asks `store` whether the card is open and hands the held payload down, and the surface
 * below owns the marks, the gestures and the disclosure.
 *
 * It moved for the reason `card.js` itself moved. The module-graph guard's ceiling is a third of
 * the 52 738-byte file v0.12.0 replaced, and this release's action surface took this file past it
 * at 17 636 bytes — and *"the honest repair to a file that is over budget is not to write less
 * prose in it, it is to notice that it had been two things for a while"*. `views/parts/members.js`
 * came off the same seam two releases ago (DECISIONS #239's, applied a third time).
 *
 * ## The held card (ADR #173, v0.7.5 §5.1-§5.3)
 *
 * An expanded card is frozen on the payload the operator opened it with, and the head line is
 * where that is **said**: a pause mark, with the number of withheld updates in its title. It read
 * `held while open` beside a paragraph counting them until v0.16.4; a count that climbs while an
 * operator reads reads as an alarm about the appliance rather than as the courtesy it is.
 */

import { html, Component, cx } from "../../dom.js";
import { Icon } from "../../icons.js";
import { RESOLUTION_TEXT } from "./lifecycle.js";
import { Badge } from "../../widgets.js";
import { age, plural, timeTitle } from "../../format.js";
import { Detail } from "./judge.js";
import { MaintenanceMark } from "./mwmarker.js";
import * as store from "../../store.js";
import { post } from "../../api.js";
import { can } from "../../session.js";

export function SituationCard({ situation, onToggle, onChanged }) {
  const sid = situation.id;
  const expanded = store.isExpanded(sid);
  const withheld = store.withheldCount(sid);
  const detail = store.heldDetail(sid);

  return html`<article class=${cx("sit", expanded && "expanded")} data-sid=${sid}>
    <div class="sit-head">
      <button type="button" class="sit-toggle" aria-expanded=${expanded ? "true" : "false"}
              aria-controls=${`sit-detail-${sid}`} onClick=${onToggle}>
        <span class=${expanded ? "sit-chevron open" : "sit-chevron"}>
          <${Icon} name="chevron" /></span>
        <span class="sid">#${sid}</span>
        ${situationName(situation)
          ? html`<span class=${cx("sit-name", situation.operator_name && "sit-name-operator")}
                       title=${NAME_TITLE[situation.operator_name ? "operator"
                         : situation.model_name ? "model" : "derived"]}
                 >${situationName(situation)}</span>` : null}
        <${Badge} tone=${situation.status === "resolved" ? "quiet" : situation.status === "pending"
          ? "pending" : "alarm"}>${situation.status}<//>
        ${situation.status === "resolved" && situation.resolution
          ? html`<${Badge} tone="quiet" title=${RESOLUTION_TEXT[situation.resolution] ?? ""}
                 >${situation.resolution.replace("_", " ")}<//>` : null}
        <${Badge}>${plural(situation.alarm_count, "alarm")}<//>
        ${situation.stale ? html`<${Badge} tone="stale" title=${STALE_TITLE}>stale<//>` : null}
        ${/* **IV.3.** At least one member element is under planned work, so this situation's
              alarm counts may be incomplete — and an operator reading it is entitled to know
              that before they conclude the incident is over. Every role sees the badge; what
              the window IS follows `visibility` and is not here. */ null}
        <${MaintenanceMark} marker=${situation.maintenance} />
        ${situation.redacted_count ? html`<${Badge} tone="redacted" title=${SCOPE_TITLE}>
          +${situation.redacted_count} outside your scope<//>` : null}
        ${/* **v0.16.4: a symbol, not a count.** This badge read `held while open` and the panel
              below it read `Frozen while open — 60 updates withheld. Collapse to resume.` A number
              that climbs while an operator reads reads as an alarm about the appliance rather than
              as the courtesy it is. The pause mark carries the fact, the `title` carries the
              count, and the accessible name is unchanged — a screen-reader user is told the same
              words a sighted one used to see. */ null}
        ${expanded && withheld > 0 ? html`<${Badge} tone="held"
          title=${`${HELD_TITLE} ${plural(withheld, "update")} withheld so far.`}
        ><span aria-hidden="true">⏸</span><span class="visually-hidden"
          >held while open</span><//>` : null}
        <span class="age" title=${timeTitle(situation.updated_at)}>${age(situation.updated_at)}</span>
      </button>
      ${/* v0.23.0 (#396): the situation's chain of events, for an RFO, on the Timeline. */ null}
      <a class="permalink tap" href=${`#/timeline?sid=${sid}`}
         aria-label=${`Sequence of events of situation #${sid}`}
         title="Sequence of events: what happened first, and what followed">
        <${Icon} name="timeline" />
      </a>
      <a class="permalink tap" href=${`#/situations/${sid}`}
         aria-label=${`Link to situation #${sid} alone`}
         title="A link to this situation alone, shareable during the incident">
        <${Icon} name="link" />
      </a>
    </div>
    ${situation.status === "pending" && situation.proposed_into
      ? html`<${Proposal} situation=${situation} onChanged=${onChanged} />` : null}
    <div id=${`sit-detail-${sid}`} class="detail" hidden=${!expanded}>
      ${expanded ? html`<${Detail} sid=${sid} detail=${detail} withheld=${withheld}
                                  onChanged=${onChanged} />` : null}
    </div>
  </article>`;
}

/** An operator's own name if there is one, else the server's projection of the membership. */
export function situationName(situation) {
  return situation.operator_name || situation.model_name || situation.derived_name || "";
}

const NAME_TITLE = {
  operator: "A name an operator gave this situation. The id above it is still the identity.",
  // v0.26.0 (ADR #412): autonomy's naming grade, when an admin has switched it on.
  model: "Named by the model deciding links (autonomy). An operator's rename replaces it, and " +
         "tells autonomy it was wrong.",
  derived: "Derived from this situation's members and recomputed when they change. An operator " +
           "can override it.",
};

const STALE_TITLE =
  "Nobody has touched this for over an hour and one of its alarms is still active. The appliance " +
  "will not resolve it while an alarm is on; it is waiting for a person.";
const HELD_TITLE =
  "This card is frozen while you have it open, so the grouping you are judging cannot change " +
  "under your click. It may not reflect the last few seconds. Collapse it to resume live updates.";
const SCOPE_TITLE =
  "Members of this situation are outside your visibility scope and are not shown. Scoping hides " +
  "them from you; it does not stop them correlating.";

/**
 * A pending proposal's question and its two answers (v0.27.0, ADR #428). The model would have
 * added these alarms to a situation an operator confirmed; instead they wait here. **Accept** merges
 * them in (and teaches every model that they belong together); **Reject** makes them a situation of
 * their own (and teaches that they do not). Shown on the card itself, not behind the fold, because
 * the answer is the whole point of the state.
 */
class Proposal extends Component {
  constructor(props) {
    super(props);
    this.state = { busy: false, error: null };
  }

  async answer(decision) {
    this.setState({ busy: true, error: null });
    try {
      await post(`/api/situations/${this.props.situation.id}/proposal`, { decision });
      this.setState({ busy: false });
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, error });
    }
  }

  render({ situation }, { busy, error }) {
    const target = situation.proposed_into;
    const p = situation.proposal_confidence;
    return html`<div class="proposal" role="group" aria-label=${`Proposal to join situation #${target}`}>
      <span>The model proposes adding these alarms to confirmed situation
        ${" "}<a href=${`#/situations/${target}`}>#${target}</a>${p != null
          ? html`${" "}— it is <b>${`${Math.round(p * 100)}%`}</b> sure they belong together` : null}.</span>
      ${can("situation.merge") ? html`<span class="proposal-acts">
        <button type="button" class="primary" disabled=${busy} onClick=${() => this.answer("accept")}>
          Accept — add to #${target}</button>
        <button type="button" disabled=${busy} onClick=${() => this.answer("reject")}>
          Reject — keep separate</button>
      </span>` : html`<span class="hint">An editor can accept or reject it.</span>`}
      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </div>`;
  }
}
