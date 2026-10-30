/* Moving an agent in the organization chart by dragging it (ADR #477).
 *
 * An admin drags an agent's card onto the card it should report to — another agent, or the NOC
 * team at the top — and a dialog asks before anything changes: who it will report to, and at which
 * tier, proposed one below its new manager's (a case climbs to the manager, a tier up) and theirs to
 * change. Nothing moves until it is confirmed; the server refuses a loop as it always did.
 *
 * Where a drop would make a loop (a manager onto one of its own reports) or change nothing (onto
 * its own manager), the card does not take it. Dragging is not the only way: the panel beside the
 * chart sets the same two fields with a keyboard.
 */

import { html, Component } from "../../dom.js";
import { AgentGlyph } from "./nocui.js";

/** Every agent below ``id`` — its reports, theirs, and so on. */
export function below(agents, id) {
  const under = new Set();
  let grew = true;
  while (grew) {
    grew = false;
    for (const a of agents) {
      if (!under.has(a.id) && (a.reports_to === id || under.has(a.reports_to))) {
        under.add(a.id);
        grew = true;
      }
    }
  }
  return under;
}

/** May ``dragged`` be dropped on ``target`` (an agent id, or 0 for the NOC team)? */
export function droppable(agents, dragged, target) {
  const a = agents.find((x) => x.id === dragged);
  if (!a || target === dragged) return false;
  if ((a.reports_to || 0) === target) return false;
  return target === 0 || !below(agents, dragged).has(target);
}

/** One below the new manager's tier; at the top, the tier it has. */
export function proposedTier(agent, manager) {
  return manager ? Math.max(1, manager.tier - 1) : agent.tier;
}

export class MoveDialog extends Component {
  constructor(props) {
    super(props);
    this.state = { tier: proposedTier(props.agent, props.manager), busy: false, error: null };
    this.box = null;
  }

  componentDidMount() { if (this.box && this.box.showModal) this.box.showModal(); }

  async confirm(event) {
    event.preventDefault();
    this.setState({ busy: true, error: null });
    try {
      await this.props.onConfirm(Number(this.state.tier));
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ agent, manager, onCancel }, { tier, busy, error }) {
    const to = manager ? `${manager.name} (${manager.role_label}, Tier ${manager.tier})` : "the NOC team (people)";
    const same = Number(tier) === agent.tier;
    const title = same ? `Move ${agent.name} under ${manager ? manager.name : "the NOC team"}?`
      : `Make ${agent.name} a Tier ${tier} agent?`;
    return html`<dialog class="org-move" ref=${(el) => { this.box = el; }} aria-labelledby="org-move-h"
        onCancel=${(e) => { e.preventDefault(); onCancel(); }}>
      <form method="dialog" onSubmit=${(e) => this.confirm(e)}>
        <header class="org-move-head"><${AgentGlyph} agent=${agent} size="md" />
          <h3 id="org-move-h">${title}</h3></header>
        <p>${agent.name} will report to <b>${to}</b>${same ? "" : html` and work as <b>Tier ${tier}</b>${" "}
          (now Tier ${agent.tier})`}. A case ${agent.name} cannot conclude in time climbs to ${manager
          ? manager.name : "the people of the NOC"}.</p>
        <div class="pe-field org-move-tier"><label for="org-move-tier">Tier</label>
          <select id="org-move-tier" value=${tier} onChange=${(e) => this.setState({ tier: e.currentTarget.value })}>
            ${[1, 2, 3, 4].map((t) => html`<option key=${t} value=${t}>Tier ${t}</option>`)}</select></div>
        ${agent.entry && Number(tier) > 1 ? html`<p class="warnbox">${agent.name} is an entry point: cases from the
          queue start with it, at Tier 1. Change that beside the chart if it should not.</p>` : null}
        ${error ? html`<p class="err" role="alert">${error}</p>` : null}
        <div class="stepup-acts">
          <button type="submit" class="primary" disabled=${busy}>${busy ? "Moving…" : "Confirm"}</button>
          <button type="button" onClick=${onCancel}>Cancel</button>
        </div>
      </form>
    </dialog>`;
  }
}
