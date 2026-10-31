/* A case's milestones, as Salesforce's milestone tracker shows them (ADRs #460, #464, #467, #468).
 *
 * One row per clock the handbook keeps: the **take** (from entering the queue), the **customer
 * update** (30 minutes from the take), **restoring service** (from the event to validate or
 * resolved), the **next update** the NOC promised, and — when an agent holds the case — when it
 * **climbs** to the next tier. Each row is a mark, a word and a time: "12m left", "late 4m",
 * "met in 9m"; the bar beside it is the same fraction drawn, never the only signal. Restoring
 * service pauses while the case is pending, and says so (ADR #478).
 *
 * The server computed every state and target; this counts the running ones on between reads.
 */

import { html, cx } from "../../dom.js";
import { Icon } from "../../icons.js";
import { absolute } from "../../format.js";
import { Clock, NextUpdate, span } from "./caseclock.js";

const MS_ICON = { run: "kpi", late: "warn", met: "check", missed: "cross", off: "info" };

function stateOf(clock, now) {
  if (!clock) return "off";
  if (clock.state === "withdrawn" || clock.state === "paused") return "off";
  if (clock.state === "met") return "met";
  if (clock.state === "missed") return "missed";
  const elapsed = now - (clock.due_at - clock.target_s);
  return clock.state === "late" || elapsed > clock.target_s ? "late" : "run";
}

function fraction(clock, now) {
  if (!clock) return 0;
  const elapsed = clock.state === "running" || clock.state === "late"
    ? now - (clock.due_at - clock.target_s) : clock.elapsed_s;
  return Math.max(0, Math.min(1, elapsed / (clock.target_s || 1)));
}

function Row({ name, state, children, meta, bar }) {
  return html`<li class=${cx("ms", `ms-${state}`)}>
    <span class="ms-mark"><${Icon} name=${MS_ICON[state]} /></span>
    <div class="ms-body">
      <div class="ms-head"><b>${name}</b>${children}</div>
      ${bar != null ? html`<span class="ms-bar" aria-hidden="true"><span
        style=${`width:${(bar * 100).toFixed(1)}%`}></span></span>` : null}
      ${meta ? html`<p class="ms-meta">${meta}</p>` : null}
    </div>
  </li>`;
}

function ClockRow({ name, clock, now, idle }) {
  const state = stateOf(clock, now);
  const meta = !clock ? idle
    : clock.state === "withdrawn" ? "Ended before anyone took it: not charged."
      : clock.state === "paused" ? `Paused while the case waits on someone outside the NOC · ${span(clock.paused_s)} paused so far`
      : clock.state === "met" || clock.state === "missed" ? `Target ${span(clock.target_s)}`
        : `Target ${span(clock.target_s)} · due ${absolute(clock.due_at)}`;
  return html`<${Row} name=${name} state=${state} meta=${meta}
      bar=${clock && clock.state !== "withdrawn" ? fraction(clock, now) : null}>
    <${Clock} clock=${clock} now=${now} label=${name} />
  <//>`;
}

function Climb({ c, now }) {
  if (c.with_people) {
    return html`<${Row} name="Escalation" state="met"
      meta="Past the agents: the NOC team holds it.">
      <span class="clock clock-ok">with the NOC team</span><//>`;
  }
  if (["validate", "resolved", "closed"].includes(c.super_state)) {
    return html`<${Row} name="Escalation" state="met"
      meta=${c.agent || c.with_people ? `Concluded ${c.level_label.replace("With", "with")}.` : "Concluded."}>
      <span class="clock clock-ok">concluded</span><//>`;
  }
  if (!c.escalates_at) {
    const why = !c.agent ? "Not routed to an agent." : c.super_state === "pending"
      ? "Waiting on someone outside the NOC: it does not climb meanwhile."
      : "A person took it: it is theirs, and it does not climb.";
    return html`<${Row} name="Escalation" state="off" meta=${why}>
      <span class="clock clock-off">—</span><//>`;
  }
  const left = c.escalates_at - now;
  const next = c.tier < 4 ? `Tier ${c.tier + 1}` : "the NOC team";
  return html`<${Row} name=${`Escalates to ${next}`} state=${left > 0 ? "run" : "late"}
      meta=${`Unless ${c.agent ? c.agent.name : "its agent"} concludes it · ${absolute(c.escalates_at)}`}>
    <span class=${cx("clock", left > 0 ? "clock-ok" : "clock-late")}>${left > 0 ? `in ${span(left)}` : "climbing now"}</span>
  <//>`;
}

export function Milestones({ c, now }) {
  const clocks = c.clocks || {};
  return html`<section class="panel-block milestones" aria-label="Milestones">
    <h3>Milestones</h3>
    <ol class="ms-list">
      <${ClockRow} name="Take" clock=${clocks.take} now=${now}
        idle="Starts when the case enters the NOC queue." />
      <${ClockRow} name="Customer update" clock=${clocks.document} now=${now}
        idle="Starts when someone takes the case." />
      <${ClockRow} name="Restore service" clock=${clocks.restore} now=${now}
        idle="From the event to validate or resolved." />
      <${Row} name="Next update" state=${!c.cadence ? "off" : c.cadence.next_update_at <= now ? "late" : "run"}
        meta=${c.cadence ? `Promised for ${absolute(c.cadence.next_update_at)}` : "Set while the case is worked or waits."}>
        <${NextUpdate} cadence=${c.cadence} now=${now} />
      <//>
      <${Climb} c=${c} now=${now} />
    </ol>
  </section>`;
}
