/* The NOC's clocks, drawn once for the queue and the case (ADR #460).
 *
 * A clock is a word and a time, never a colour alone: "12m left", "late 15m", "met in 4m",
 * "missed by 9m", "withdrawn". The server computed the state and the numbers; this only words them
 * and counts the running ones down between reads.
 */

import { html, cx } from "../../dom.js";

/** "4m", "1.5h", "2.0d" — a duration, unsigned. */
export function span(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

const TONE = { running: "clock-ok", met: "clock-ok", late: "clock-late", missed: "clock-late",
  withdrawn: "clock-off", paused: "clock-off" };

/** One clock, as of `now` (a running one keeps counting after the read). */
export function Clock({ clock, now, label }) {
  if (!clock) return html`<span class="clock clock-off">—</span>`;
  const elapsed = clock.state === "running" || clock.state === "late"
    ? Math.max(0, now - (clock.due_at - clock.target_s)) : clock.elapsed_s;
  const over = elapsed - clock.target_s;
  let text;
  if (clock.state === "withdrawn") text = "withdrawn";
  else if (clock.state === "paused") text = over > 0 ? `paused · late ${span(over)}` : `paused · ${span(-over)} left`;
  else if (clock.state === "met") text = `met in ${span(elapsed)}`;
  else if (clock.state === "missed") text = `missed by ${span(over)}`;
  else text = over > 0 ? `late ${span(over)}` : `${span(-over)} left`;
  const state = (clock.state === "running" && over > 0) ? "late" : clock.state;
  return html`<span class=${cx("clock", TONE[state])}
      title=${`${label || "Clock"}: target ${span(clock.target_s)}`}>${text}</span>`;
}

/** The next update: when, and whether it has come — advice beside it when beyond the cadence. */
export function NextUpdate({ cadence, now }) {
  if (!cadence) return html`<span class="muted">—</span>`;
  const left = cadence.next_update_at - now;
  return html`<span class=${cx("clock", left <= 0 ? "clock-late" : "clock-ok")}
      title=${cadence.beyond_cadence ? `Further ahead than the ${span(cadence.cadence_s)} cadence for this priority` : ""}>
    ${left <= 0 ? `due ${span(-left)} ago` : `in ${span(left)}`}${cadence.beyond_cadence
      ? html`<span class="clock-note"> · beyond the ${span(cadence.cadence_s)} cadence</span>` : null}
  </span>`;
}

const PRIORITY_TONE = { critical: "prio-critical", major: "prio-major", minor: "prio-minor" };

export function Priority({ priority, label }) {
  return html`<span class=${cx("case-priority", PRIORITY_TONE[priority])}>${label || priority}</span>`;
}
